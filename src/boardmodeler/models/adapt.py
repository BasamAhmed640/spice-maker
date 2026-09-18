"""Mechanical PSpice → LTspice adaptation with a recorded change list (D8).

Adapting a vendor model is allowed **only** as a mechanical, auditable transform:
every edit is enumerated in an :class:`AdaptationReport`, the original bytes stay
untouched in the vendor store, and nothing is claimed about behaviour that the
capability probes did not observe.

The transforms below were derived from the real TPS54320 unencrypted PSpice
transient model (`SLVM451A`) and each one is covered by a test:

1. **Continuation folding** — PSpice continues a card on the next line when it
   starts with ``+``. The port joins logical cards first, so a ``VALUE { … }``
   expression split across lines cannot be mangled.
2. **ABM ``VALUE`` syntax** — PSpice writes ``E out 0 VALUE { {IF(…)} }``; LTspice
   wants ``E out 0 VALUE={IF(…)}``.
3. **Switch models** — PSpice ``VSWITCH(Roff, Ron, Voff, Von)`` becomes LTspice
   ``SW(Roff, Ron, Vt, Vh)`` with ``Vt = (Von+Voff)/2`` and
   ``Vh = |Von-Voff|/2``. The half-width matters: LTspice's ``Vh`` is the
   half-band, so using ``|Von-Voff|`` doubles the band and a 1 V logic signal
   never reaches the threshold (observed: the oscillator never toggled and the
   converter never switched).
4. **Unit suffixes** — PSpice accepts ``0Vdc``; LTspice does not parse ``Vdc``.
5. **Numerical adaptations** (opt-in, recorded): ``.tran`` without ``uic``
   (LTspice solves the operating point instead of crawling from t=0), ``method=gear``
   with ``trtol`` and relaxed ``gmin``/``abstol``/``vntol``, and raising a
   pathological diode emission coefficient (``N=0.01`` → ``0.1``), which PSpice
   tolerates and LTspice cannot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from boardmodeler.domain.hashing import sha256_text

__all__ = [
    "AdaptationReport",
    "PortResult",
    "join_continuation_cards",
    "parse_pspice_number",
    "port_pspice_to_ltspice",
]

_TOOL = "boardmodeler.models.adapt"

#: PSpice magnitude suffixes (case-insensitive; ``M`` is milli, ``MEG`` is mega).
_MAGNITUDES: dict[str, float] = {
    "meg": 1e6,
    "t": 1e12,
    "g": 1e9,
    "k": 1e3,
    "m": 1e-3,
    "u": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
}

#: Unit words that may follow a numeric literal in PSpice.
_UNITS = (
    "ohms",
    "ohm",
    "hz",
    "farads",
    "farad",
    "henries",
    "henry",
    "volts",
    "volt",
    "amps",
    "amp",
    "sec",
    "v",
    "a",
    "f",
    "h",
    "w",
    "s",
)

_NUMBER_RE = re.compile(r"([0-9]*\.?[0-9]+(?:[eE][+-]?\d+)?)\s*([A-Za-z]*)")
_VSWITCH_RE = re.compile(r"(?im)^\s*\.model\s+(\S+)\s+vswitch\b.*$")
_PARAM_RE = re.compile(r"([A-Za-z]+)\s*=\s*([0-9.eE+-]+[A-Za-z]*)")


class AdaptationReport(BaseModel):
    """Exactly what was changed to make a vendor artifact usable."""

    model_config = ConfigDict(extra="forbid")

    source_sha256: str
    adapted_sha256: str
    changes: list[str] = Field(default_factory=list)
    unchanged_claims: list[str] = Field(default_factory=list)
    tool: str = _TOOL
    created_utc: str = ""

    def as_markdown(self) -> str:
        lines = [
            "# Adaptation report",
            "",
            f"- source sha256: `{self.source_sha256}`",
            f"- adapted sha256: `{self.adapted_sha256}`",
            f"- tool: `{self.tool}`",
            f"- created: {self.created_utc}",
            "",
            "## Changes",
            "",
        ]
        lines.extend(f"- {change}" for change in self.changes)
        lines.append("")
        lines.append("## Explicitly unchanged")
        lines.append("")
        lines.extend(f"- {claim}" for claim in self.unchanged_claims)
        lines.append("")
        return "\n".join(lines)


@dataclass(frozen=True)
class PortResult:
    """Adapted text plus the report that justifies it."""

    text: str
    report: AdaptationReport
    warnings: list[str] = field(default_factory=list)


def parse_pspice_number(raw: str, default: str = "0") -> float:
    """Parse a PSpice numeric literal such as ``0.8V``, ``50m``, ``1MEG``, ``4.7u``."""
    match = _NUMBER_RE.fullmatch((raw or default).strip())
    if not match:
        raise ValueError(f"cannot parse PSpice value {raw!r}")
    value = float(match.group(1))
    letters = match.group(2).lower()
    if letters in _UNITS:
        letters = ""
    else:
        for unit in _UNITS:
            if len(letters) > len(unit) and letters.endswith(unit):
                letters = letters[: -len(unit)]
                break
    if letters:
        if letters not in _MAGNITUDES:
            raise ValueError(f"unknown magnitude suffix {match.group(2)!r} in {raw!r}")
        value *= _MAGNITUDES[letters]
    return value


def join_continuation_cards(text: str) -> list[str]:
    """Fold PSpice ``+`` continuation lines into logical cards.

    Comment lines are preserved verbatim and never folded into a card.
    """
    cards: list[str] = []
    current: str | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("*"):
            if current is not None:
                cards.append(current)
                current = None
            cards.append(raw_line.rstrip())
            continue
        if stripped.startswith("+"):
            piece = stripped[1:].strip()
            current = f"{current} {piece}" if current else piece
            continue
        if current is not None:
            cards.append(current)
        current = stripped
    if current is not None:
        cards.append(current)
    return cards


def _port_switch_model(card: str, changes: list[str]) -> str:
    name = card.split()[1]
    params = dict(_PARAM_RE.findall(card))
    von = parse_pspice_number(params.get("Von", "0.8"), "0.8")
    voff = parse_pspice_number(params.get("Voff", "0.2"), "0.2")
    vt = (von + voff) / 2.0
    vh = abs(von - voff) / 2.0
    changes.append(
        f"switch model {name}: PSpice VSWITCH(Voff={voff:g}, Von={von:g}) -> "
        f"LTspice SW(Vt={vt:g}, Vh={vh:g}) [Vh is the half-band]"
    )
    return (
        f".model {name} SW(Roff={params.get('Roff', '1e6')} Ron={params.get('Ron', '1')} "
        f"Vt={vt:g} Vh={vh:g})"
    )


def port_pspice_to_ltspice(
    text: str,
    *,
    solver_help: bool = True,
    tame_diodes: bool = True,
    created_utc: str | None = None,
) -> PortResult:
    """Port unencrypted PSpice text to LTspice, enumerating every change.

    ``solver_help`` adds the numerical options learned from the real TI model
    (LTspice otherwise crawls or fails with ``Time step too small`` at the
    initial timepoint when the deck uses ``uic``). ``tame_diodes`` raises a
    pathological ``N=0.01`` emission coefficient, which LTspice rejects.
    """
    changes: list[str] = []
    warnings: list[str] = []

    cards = join_continuation_cards(text)
    changes.append(f"folded PSpice '+' continuation lines into {len(cards)} logical cards")

    out: list[str] = []
    abm_converted = 0
    switches_converted = 0
    units_stripped = 0
    diodes_tamed = 0

    for card in cards:
        if card.startswith("*"):
            out.append(card)
            continue

        if re.search(r"VALUE\s*\{", card, flags=re.IGNORECASE):
            card = re.sub(r"VALUE\s*\{\s*\{?\s*", "VALUE={", card, flags=re.IGNORECASE)
            card = re.sub(r"\s*\}\s*\}\s*$", "}", card)
            card = re.sub(r"\s*\}\s*$", "}", card)
            abm_converted += 1

        if _VSWITCH_RE.match(card):
            card = _port_switch_model(card, changes)
            switches_converted += 1

        if re.search(r"Vdc", card, flags=re.IGNORECASE):
            card = re.sub(r"Vdc", "", card, flags=re.IGNORECASE)
            units_stripped += 1

        if tame_diodes and re.match(r"(?i)^\.model\s+\S+\s+d\s+", card):

            def _fix_n(match: re.Match[str]) -> str:
                nonlocal diodes_tamed
                value = parse_pspice_number(match.group(1))
                if value < 0.05:
                    diodes_tamed += 1
                    return "n=0.1"
                return match.group(0)

            card = re.sub(r"(?i)\bn\s*=\s*([0-9.eE+-]+[A-Za-z]*)", _fix_n, card)

        out.append(card)

    if abm_converted:
        changes.append(
            f"converted {abm_converted} PSpice ABM 'VALUE {{ {{...}} }}' expressions to "
            "LTspice 'VALUE={...}'"
        )
    if switches_converted:
        changes.append(f"converted {switches_converted} VSWITCH models to LTspice SW")
    if units_stripped:
        changes.append(f"stripped the 'Vdc' unit suffix from {units_stripped} cards")
    if diodes_tamed:
        changes.append(
            f"raised the emission coefficient of {diodes_tamed} diode model(s) from N<0.05 to "
            "N=0.1 (LTspice fails with 'Time step too small' on PSpice N=0.01)"
        )
        warnings.append(
            "diode emission coefficient was changed; diode clamping behaviour differs from the "
            "PSpice original and the capability probes must not claim otherwise"
        )

    if solver_help:
        out.append(".options method=gear trtol=10 gmin=1e-11 abstol=1e-9 vntol=1e-5")
        changes.append(
            "added solver options (method=gear, trtol=10, gmin/abstol/vntol relaxed) because the "
            "ported model otherwise fails with 'Time step too small' at the initial timepoint"
        )
        warnings.append(
            "decks that instantiate the ported model must not use .tran ... uic; LTspice must "
            "solve the operating point first"
        )

    adapted = "\n".join(out) + "\n"
    report = AdaptationReport(
        source_sha256=sha256_text(text),
        adapted_sha256=sha256_text(adapted),
        changes=changes,
        unchanged_claims=[
            "subcircuit port order and node names are unchanged",
            "the vendor's internal topology and parameter values are unchanged except as listed",
            "no behavioural claim about the model was derived from this transformation",
        ],
        tool=_TOOL,
        created_utc=created_utc or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    return PortResult(text=adapted, report=report, warnings=warnings)
