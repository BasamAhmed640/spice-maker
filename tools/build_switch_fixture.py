"""Build the synthetic switch fixture's requirement set from its contract.

```powershell
uv run python tools/build_switch_fixture.py
```

Same honesty rule as the datasheet extraction: every `excerpt` is sliced out of
the contract Markdown by anchor at build time, so a requirement cannot quote text
that is not in the fixture. The clock-availability item is deliberately built as an
`ASSUMPTION` with `origin=TEST_FIXTURE`, which is what makes the pipeline report
clock-dependent conclusions as UNKNOWN.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from boardmodeler.domain.records import Requirement

FIXTURE = Path("fixtures") / "switch_fixture"
CONTRACT = FIXTURE / "SYNTHETIC_SWITCH_CONTRACT.md"
PINS = FIXTURE / "pins.csv"
OUT = FIXTURE / "requirements.json"
DOC_ID = "doc_synthetic_switch_contract"
PART = "SYNTH-U1"
MAX_EXCERPT = 400


@dataclass(frozen=True)
class Spec:
    suffix: str
    kind: str
    req_class: str
    criticality: str
    statement: str
    section: str
    anchor: str
    limits: dict[str, Any] | None = None
    expression: dict[str, Any] | None = None
    signal_refs: tuple[str, ...] = ()
    condition: str | None = None
    after: int = 160


SPECS: tuple[Spec, ...] = (
    Spec(
        "ELEC_001",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "The 3V3 domain stays between 3.135 V and 3.465 V.",
        "Supply domains",
        r"\|`3V3`\|3\.3 V\|3\.135 V … 3\.465 V\|",
        {"min": 3.135, "max": 3.465, "unit": "V"},
        {"op": "between", "signal": "V(3V3)", "low": 3.135, "high": 3.465, "unit": "V"},
        ("V(3V3)",),
    ),
    Spec(
        "ELEC_002",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "The 1V8 domain stays between 1.710 V and 1.890 V.",
        "Supply domains",
        r"\|`1V8`\|1\.8 V\|1\.710 V … 1\.890 V\|",
        {"min": 1.710, "max": 1.890, "unit": "V"},
        {"op": "between", "signal": "V(1V8)", "low": 1.710, "high": 1.890, "unit": "V"},
        ("V(1V8)",),
    ),
    Spec(
        "ELEC_003",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "The 3V3 rail current stays within its 500 mA peak while its static load is 250 mA and a "
        "+150 mA step occurs 2 ms after the rail is valid.",
        "Supply domains",
        r"\|`3V3`\|250 mA\|",
        {"min": 0.0, "max": 0.5, "unit": "A"},
        {"op": "lt", "signal": "I(3V3)", "value": 0.5, "unit": "A"},
        ("I(3V3)",),
        condition="static 250 mA plus a +150 mA step at 2 ms",
    ),
    Spec(
        "ELEC_004",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "The 1V8 rail current stays within its 800 mA peak while its static load is 400 mA and a "
        "+200 mA step occurs 2 ms after the rail is valid.",
        "Supply domains",
        r"\|`1V8`\|400 mA\|",
        {"min": 0.0, "max": 0.8, "unit": "A"},
        {"op": "lt", "signal": "I(1V8)", "value": 0.8, "unit": "A"},
        ("I(1V8)",),
        condition="static 400 mA plus a +200 mA step at 2 ms",
    ),
    Spec(
        "CONN_010",
        "CONNECTIVITY",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "Every domain pin sits on a net whose declared domain is that pin's own domain; two "
        "domains shorted together, a disconnected domain pin, or a pull-up on the wrong domain "
        "are connection errors.",
        "Supply domains",
        r"Each domain's pins must sit on a net whose declared domain is that domain",
        expression={"op": "pin_connected", "refdes": "U1", "pin": "3V3_IN"},
        signal_refs=("3V3", "1V8"),
    ),
    Spec(
        "CONN_011",
        "CONNECTIVITY",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "PERST# is required and must not float; it is active low.",
        "Reset",
        r"\|Required\|Yes; must not float\|",
        expression={"op": "pin_connected", "refdes": "U1", "pin": "PERST_N"},
        signal_refs=("PERST_N",),
    ),
    Spec(
        "TEMP_012",
        "TEMPORAL",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "PERST# is held low while either domain is outside its allowed range, and is released "
        "only after both domains are valid and both power-good signals are asserted.",
        "Reset",
        r"Must be held low while \*\*either\*\* domain is outside its allowed range",
        expression={
            "op": "ordering",
            "first": {"signal": "V(3V3_PG)", "kind": "rise_above", "value": 1.0, "unit": "V"},
            "then": {"signal": "V(PERST_N)", "kind": "rise_above", "value": 2.0, "unit": "V"},
        },
        signal_refs=("PERST_N", "3V3_PG", "1V8_PG"),
    ),
    Spec(
        "TEMP_013",
        "TEMPORAL",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "PERST# is released between 1 ms and 100 ms after the last prerequisite becomes valid.",
        "Reset",
        r"\|Release delay\|At least 1 ms and at most 100 ms after the last prerequisite\|",
        {"min": 1e-3, "max": 0.1, "unit": "s"},
        {
            "op": "event_delay",
            "start": {"signal": "V(1V8_PG)", "kind": "rise_above", "value": 1.0, "unit": "V"},
            "end": {"signal": "V(PERST_N)", "kind": "rise_above", "value": 2.0, "unit": "V"},
            "min_s": 1e-3,
            "max_s": 0.1,
        },
        ("PERST_N", "1V8_PG"),
    ),
    Spec(
        "CONN_020",
        "CONNECTIVITY",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "The CONFIG[0..2] straps are sampled between 1 ms and 5 ms after 1V8 becomes valid.",
        "CONFIG straps",
        r"\|Sampling window\|1 ms … 5 ms after `1V8` valid\|",
        {"min": 1e-3, "max": 5e-3, "unit": "s"},
        signal_refs=("CONFIG0", "CONFIG1", "CONFIG2"),
    ),
    Spec(
        "FUNC_021",
        "FUNCTIONAL",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "The strap combination 111 is invalid and must be reported rather than interpreted.",
        "CONFIG straps",
        r"`000` … `110` are documented; `111` is \*\*invalid\*\*",
        expression={"op": "pin_open", "refdes": "U1", "pin": "CONFIG0"},
        signal_refs=("CONFIG0", "CONFIG1", "CONFIG2"),
    ),
    Spec(
        "TEMP_022",
        "TEMPORAL",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "A strap that changes after the sampling window closes is a connection or timing error.",
        "CONFIG straps",
        r"A strap that changes after the window closes is a connection/timing error",
        expression={
            "op": "hold",
            "signal": "V(CONFIG0)",
            "value": 1.8,
            "unit": "V",
            "interval": {"start_s": 5e-3, "end_s": 2e-2},
            "stable": True,
        },
        signal_refs=("CONFIG0",),
    ),
    Spec(
        "CONN_030",
        "CONNECTIVITY",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "SMB_CLK and SMB_DAT are open-drain sideband signals pulled up on the 3V3 domain.",
        "Sideband (SMB_CLK, SMB_DAT)",
        r"\|Pull-up domain\|`3V3`\|",
        expression={
            "op": "pullup_domain",
            "refdes": "U1",
            "pin": "SMB_DAT",
            "net": "SMB_DAT",
            "domain": "3V3",
        },
        signal_refs=("SMB_CLK", "SMB_DAT"),
    ),
    Spec(
        "ELEC_031",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "A released sideband line reaches at least 0.9 x 3V3 within 5 us.",
        "Sideband (SMB_CLK, SMB_DAT)",
        r"\|Idle level\|Must reach at least 0\.9 \u00d7 3V3 within 5 \u00b5s",
        {"min": 2.97, "unit": "V"},
        {"op": "rise_above", "signal": "V(SMB_DAT)", "value": 2.97, "unit": "V"},
        ("SMB_DAT",),
    ),
    Spec(
        "CONN_032",
        "CONNECTIVITY",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "Pulling a sideband line up to 1V8 instead of 3V3 is a connection error even if the link "
        "appears to work.",
        "Sideband (SMB_CLK, SMB_DAT)",
        r"\|Wrong-domain pull-up\|A pull-up to `1V8` is a connection error",
        expression={
            "op": "pullup_domain",
            "refdes": "U1",
            "pin": "SMB_CLK",
            "net": "SMB_CLK",
            "domain": "3V3",
        },
        signal_refs=("SMB_CLK",),
    ),
    Spec(
        "CONN_040",
        "CONNECTIVITY",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "CLK_REQ# is an active-low open-drain output pulled up on 3V3.",
        "Clock request (CLK_REQ#)",
        r"\|Pull-up domain\|`3V3`\|",
        expression={
            "op": "pullup_domain",
            "refdes": "U1",
            "pin": "CLK_REQ_N",
            "net": "CLK_REQ_N",
            "domain": "3V3",
        },
        signal_refs=("CLK_REQ_N",),
    ),
    Spec(
        "SYSTEM_050",
        "SYSTEM",
        "ASSUMPTION",
        "CRITICAL",
        "REFCLK_100M is assumed to be present when CLK_REQ# is asserted; the fixture does not "
        "model or verify the clock source, so any clock-dependent conclusion is UNKNOWN.",
        "Clock availability — an explicit assumption, not a verified path",
        r"\* any conclusion that depends on the clock being present is reported as",
        signal_refs=("REFCLK_100M",),
    ),
    Spec(
        "FUNC_060",
        "FUNCTIONAL",
        "DOCUMENTED_LIMIT",
        "INFORMATIONAL",
        "PRECONDITIONS_SATISFIED is a diagnostic signal, not a device pin, and is not proof that "
        "the device booted.",
        "Clock availability — an explicit assumption, not a verified path",
        r"is published as a \*\*diagnostic signal only\*\*",
        signal_refs=("PRECONDITIONS_SATISFIED",),
    ),
    Spec(
        "CONN_070",
        "CONNECTIVITY",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "A pin on an unpowered domain must not be driven externally (back-powering).",
        "Partial power",
        r"Any pin on a domain that is unpowered must not be driven externally",
        expression={"op": "pin_connected", "refdes": "U1", "pin": "1V8_IN"},
        signal_refs=("1V8",),
    ),
)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def excerpt(contract: str, spec: Spec) -> str:
    match = re.search(spec.anchor, contract, re.IGNORECASE)
    if not match:
        raise SystemExit(f"anchor not found for {spec.suffix!r}: {spec.anchor!r}")
    text = contract[max(0, match.start() - 40) : match.end() + spec.after]
    text = normalize(text)
    if len(text) > MAX_EXCERPT:
        text = text[: MAX_EXCERPT - 1].rstrip() + "…"
    return text


def build() -> dict[str, Any]:
    contract = CONTRACT.read_text(encoding="utf-8")
    requirements: list[dict[str, Any]] = []
    for spec in SPECS:
        text = excerpt(contract, spec)
        payload = {
            "req_id": f"REQ_{PART.replace('-', '')}_{spec.suffix}",
            "applies_to": PART,
            "configuration": None,
            "kind": spec.kind,
            "class": spec.req_class,
            "criticality": spec.criticality,
            "origin": "TEST_FIXTURE",
            "statement": spec.statement,
            "limits": spec.limits,
            "expression": spec.expression,
            "conditions": (
                [{"text": spec.condition, "parameter_overrides": {}}] if spec.condition else []
            ),
            "signal_refs": list(spec.signal_refs),
            "evidence": [
                {
                    "doc_id": DOC_ID,
                    "page": None,
                    "section": spec.section,
                    "table": None,
                    "figure": None,
                    "excerpt": text,
                    "extraction": "synthetic_fixture",
                }
            ],
            "conflicts": [],
            "citation_verified": True,
            "status": "active",
        }
        Requirement.model_validate(payload)
        requirements.append(payload)

    pins = []
    lines = PINS.read_text(encoding="utf-8").strip().splitlines()
    header = lines[0].split(",")
    for line in lines[1:]:
        values = line.split(",")
        row = dict(zip(header, values, strict=True))
        pins.append(
            {
                "part_id": PART,
                "physical_pin": row["physical_pin"],
                "name": row["name"],
                "function": f"synthetic fixture pin {row['name']}",
                "polarity": row["polarity"],
                "direction": row["direction"],
                "supply_domain": row["supply_domain"] or None,
                "output_topology": row["output_topology"],
                "connection_requirement": row["connection_requirement"],
                "unused_pin_treatment": row["unused_pin_treatment"] or None,
                "behavior": [],
                "evidence": [
                    {
                        "doc_id": DOC_ID,
                        "page": None,
                        "section": "pins.csv",
                        "table": "pins.csv",
                        "figure": None,
                        "excerpt": line[:MAX_EXCERPT],
                        "extraction": "synthetic_fixture",
                    }
                ],
                "mapped_symbol_pin": row["name"],
            }
        )

    return {
        "document": {
            "doc_id": DOC_ID,
            "title": "SYNTHETIC_SWITCH_CONTRACT.md — a test fixture, not device data",
            "provenance": "synthetic_fixture",
            "classification": "public",
            "remote_inference_allowed": False,
            "file": str(CONTRACT).replace("\\", "/"),
        },
        "extraction": {
            "method": "fixture-authored; excerpts sliced from the contract Markdown by anchor",
            "tool": "tools/build_switch_fixture.py",
            "note": "every requirement carries origin=TEST_FIXTURE so no report can present it "
            "as device data",
        },
        "requirements": requirements,
        "pins": pins,
    }


def main() -> int:
    data = build()
    OUT.write_text(
        json.dumps(
            {
                "document": data["document"],
                "extraction": data["extraction"],
                "requirements": data["requirements"],
                "pins": data["pins"],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    kinds = sorted({item["kind"] for item in data["requirements"]})
    classes = sorted({item["class"] for item in data["requirements"]})
    print(f"requirements: {len(data['requirements'])} -> {OUT}")
    print(f"pins:         {len(data['pins'])}")
    print(f"kinds:        {kinds}")
    print(f"classes:      {classes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
