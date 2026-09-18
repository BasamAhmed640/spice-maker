"""SPICE netlist parsing and flattened connectivity (D9, INTERFACES §3).

The parser is deliberately conservative: it understands the element and
directive forms listed in the interface, keeps every unrecognised ``.xxx``
directive verbatim (LTspice decks carry plenty of them), and raises
:class:`NetlistError` — always carrying the offending line number and text —
for input it cannot represent rather than guessing.

Two details of real LTspice output (measured on LTspice 26.0.0.3) shape this
module:

* a subcircuit instance is written ``X§U1 n1 n2 SUB`` — the instance name from
  the schematic is prefixed with ``X`` and separated by ``§``.  The refdes is
  normalised back to the schematic's ``InstName`` (``X§U1`` -> ``U1``).
* dangling pins are given names like ``NC_01`` by LTspice; connectivity
  consumers (``static_check.SC009``) treat those as not connected.

:class:`NetMap` flattens subcircuit instances through their ``.subckt``
definitions so ``node_of(refdes, pin)`` answers with the node the pin is really
attached to.  Pin identifiers may be:

* a symbol pin name, when the caller supplies the ``.asy`` pin order via
  ``symbols`` or the circuit carries :attr:`Circuit.pin_orders`,
* a subcircuit port name, when the instance's ``.subckt`` is defined locally,
* a 1-based position string (``"3"`` = ``SpiceOrder 3`` / the 3rd node), which
  matches the netlist's own node order.

Nested devices (a device declared inside a ``.subckt`` body) are exposed under
a dotted instance path (``X1.R9``) with their nodes resolved outward through
the port bindings; nodes with no outer binding are qualified as
``X1#<internal>`` so two instantiations of the same subcircuit never collide.
Recursion is cycle-guarded and every detected cycle is recorded in
:attr:`NetMap.cycles` instead of hanging.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "ELEMENT_LETTERS",
    "NC_NODE_RE",
    "TERMINAL_COUNTS",
    "Circuit",
    "Device",
    "NetMap",
    "NetlistError",
    "SubcktDef",
    "build_netmap",
    "display_refdes",
    "parse_netlist",
    "parse_netlist_file",
]

#: Element letters this parser represents.  A first character outside this set
#: is a malformed deck, not something to skip.
ELEMENT_LETTERS: frozenset[str] = frozenset("RLCVIDEGHSXBKJMQ")

#: Terminal counts per element letter, most common first.  ``X`` and ``K`` are
#: handled separately (variable arity / coupling references).
TERMINAL_COUNTS: dict[str, tuple[int, ...]] = {
    "B": (2,),
    "C": (2,),
    "D": (2,),
    "E": (4, 2),
    "G": (4, 2),
    "H": (2,),
    "I": (2,),
    "J": (3,),
    "L": (2,),
    "M": (4, 3),
    "Q": (4, 3),
    "R": (2,),
    "S": (4,),
    "V": (2,),
}

#: Markers for a node attached to a pin that is not connected: LTspice's
#: ``NC_01`` and the plain ``NC`` that netlist builders write for a missing pin.
NC_NODE_RE = re.compile(r"^nc(?:_\d+)?$", re.IGNORECASE)

_REFDES_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-\[\]]*$")
_MIRROR_SEPARATOR = "\u00a7"

#: Alias -> 0-based terminal index, used on top of the positional ``"1".."n"``.
_PIN_ALIASES: dict[str, dict[str, int]] = {
    "B": {"+": 0, "-": 1},
    "C": {"1": 0, "2": 1},
    "D": {"A": 0, "K": 1, "C": 1},
    "E": {"+": 0, "-": 1, "IN+": 2, "IN-": 3},
    "G": {"+": 0, "-": 1, "IN+": 2, "IN-": 3},
    "I": {"+": 0, "-": 1},
    "L": {"1": 0, "2": 1},
    "R": {"1": 0, "2": 1},
    "V": {"+": 0, "-": 1},
}


class NetlistError(ValueError):
    """Malformed netlist input.  Carries the offending line number and text."""

    def __init__(self, message: str, *, line: int | None = None, text: str = "") -> None:
        where = f"line {line}: " if line is not None else ""
        offending = f" [{text.strip()}]" if text.strip() else ""
        super().__init__(f"{where}{message}{offending}")
        self.message = message
        self.line = line
        self.text = text


@dataclass(frozen=True)
class Device:
    """One netlist element."""

    refdes: str
    kind: str
    nodes: tuple[str, ...]
    value: str = ""
    params: dict[str, str] = field(default_factory=dict)
    subckt: str | None = None
    extra: tuple[str, ...] = ()
    #: 1-based line of the element in the parsed text (0 when synthesised).
    line: int = 0


@dataclass(frozen=True)
class SubcktDef:
    """A ``.subckt`` / ``.ends`` block: its ports, parameters and body."""

    name: str
    ports: tuple[str, ...]
    params: dict[str, str] = field(default_factory=dict)
    devices: dict[str, Device] = field(default_factory=dict)
    line: int = 0


@dataclass
class Circuit:
    """A parsed netlist."""

    devices: dict[str, Device] = field(default_factory=dict)
    subckts: dict[str, SubcktDef] = field(default_factory=dict)
    includes: list[str] = field(default_factory=list)
    directives: list[str] = field(default_factory=list)
    source_path: Path | None = None
    #: refdes -> pin names in node order (set by :func:`to_circuit`, or by a
    #: caller that knows the symbol/ScriptOrder mapping).
    pin_orders: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: First comment line of the deck, when it is not the LTspice banner.
    title: str = ""

    def nodes(self) -> list[str]:
        """Every node name, in order of first appearance (ground included)."""
        seen: dict[str, None] = {}
        for device in self.devices.values():
            for node in device.nodes:
                seen.setdefault(node, None)
        for subckt in self.subckts.values():
            for device in subckt.devices.values():
                for node in device.nodes:
                    seen.setdefault(node, None)
        return list(seen)

    def refdes(self) -> list[str]:
        """Top-level reference designators, in file order."""
        return list(self.devices)

    def device_of(self, refdes: str) -> Device | None:
        """A device by exact token, else by the ``X<refdes>`` instance name.

        A neutral project names a component ``U5`` while SPICE names its
        subcircuit instance ``XU5``; lookup accepts both, with an exact match
        winning.
        """
        device = self.devices.get(refdes)
        if device is not None:
            return device
        return self.devices.get(f"X{refdes}")


# --------------------------------------------------------------------------- #
# text scanning


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Fold continuations and strip comments, keeping each line's number."""
    out: list[tuple[int, str]] = []
    pending_number: int | None = None
    pending = ""
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.lstrip("\ufeff") if number == 1 else raw
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("+"):
            if pending is None:
                raise NetlistError(
                    "continuation line with nothing to continue", line=number, text=line
                )
            pending = f"{pending} {' '.join(stripped[1:].split())}".rstrip()
            continue
        if pending is not None:
            out.append((pending_number or number, pending))
            pending = None
        if stripped.startswith("*") or stripped.startswith(";"):
            continue
        body = stripped.split(";", 1)[0].strip()
        if not body:
            continue
        pending_number = number
        pending = body
    if pending is not None:
        out.append((pending_number or 0, pending))
    return out


def _params_from(tokens: Sequence[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for token in tokens:
        if "=" not in token:
            continue
        key, _, value = token.partition("=")
        if key:
            params[key] = value
    return params


def _normalise_refdes(token: str) -> str:
    """Undo LTspice's ``X§<InstName>`` encoding for subcircuit instances."""
    if _MIRROR_SEPARATOR in token:
        return token.rsplit(_MIRROR_SEPARATOR, 1)[1]
    return token


def display_refdes(refdes: str) -> str:
    """The name a person uses for a preserved refdes: ``XU5`` -> ``U5``.

    SPICE spells a subcircuit instance ``X<refdes>`` while neutral data names the
    component ``U5``; findings about a *component* report the latter and keep the
    netlist token in their detail.
    """
    if len(refdes) > 1 and refdes[:1] in {"X", "x"}:
        return refdes[1:]
    return refdes


def _int_or_none(token: str) -> int | None:
    try:
        return int(token)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# element parsing


def _parse_device(tokens: list[str], *, line: int, text: str) -> Device:
    raw = tokens[0]
    letter = raw[:1].upper()
    if letter not in ELEMENT_LETTERS:
        raise NetlistError(
            f"unknown element letter {letter!r} in {raw!r}; "
            f"known letters: {''.join(sorted(ELEMENT_LETTERS))}",
            line=line,
            text=text,
        )
    refdes = _normalise_refdes(raw)
    if not refdes:
        raise NetlistError("element has no reference designator", line=line, text=text)
    if not _REFDES_RE.match(refdes):
        raise NetlistError(f"illegal reference designator {refdes!r}", line=line, text=text)

    args = tokens[1:]
    if letter == "X":
        if len(args) < 2:
            raise NetlistError(
                "subcircuit instance needs at least one node and a subcircuit name",
                line=line,
                text=text,
            )
        params = _params_from(args[:-1])
        return Device(
            refdes=refdes,
            kind="X",
            nodes=tuple(args[:-1]),
            value="",
            params=params,
            subckt=args[-1],
            extra=(),
            line=line,
        )
    if letter == "K":
        if len(args) < 3:
            raise NetlistError(
                "coupling needs two inductors and a coefficient", line=line, text=text
            )
        return Device(
            refdes=refdes,
            kind="K",
            nodes=(),
            value=args[-1],
            params=_params_from(args[2:-1]),
            extra=tuple(args[:2]),
            line=line,
        )

    counts = TERMINAL_COUNTS[letter]
    count = _pick_terminal_count(letter, args, counts)
    if count is None:
        expected = " or ".join(str(value) for value in counts)
        raise NetlistError(
            f"{refdes}: expected {expected} node(s), found {len(args)} token(s)",
            line=line,
            text=text,
        )
    nodes = tuple(args[:count])
    rest = args[count:]
    if letter == "H":
        # ``H1 n+ n- <control source> <transresistance>``: the control element
        # is a name, not a node.
        if not rest:
            raise NetlistError("H element needs a control source and a value", line=line, text=text)
        return Device(
            refdes=refdes,
            kind=letter,
            nodes=nodes,
            value=rest[-1],
            params=_params_from(rest[1:-1]),
            extra=(rest[0],),
            line=line,
        )
    if letter in {"V", "I", "B"} or (letter in {"E", "G"} and len(nodes) == 2):
        # Source/behavioural values contain spaces and parentheses
        # (``PULSE(0 1 0 1n 1n 1 2)``, ``VALUE={V(a)*2}``): keep the tail verbatim.
        return Device(
            refdes=refdes, kind=letter, nodes=nodes, value=" ".join(rest).strip(), line=line
        )
    value = ""
    if rest and "=" not in rest[0]:
        value = rest[0]
        rest = rest[1:]
    return Device(
        refdes=refdes, kind=letter, nodes=nodes, value=value, params=_params_from(rest), line=line
    )


def _pick_terminal_count(letter: str, args: Sequence[str], counts: Sequence[int]) -> int | None:
    if letter in {"E", "G"}:
        # ``E1 out+ out- VALUE={...}`` (two nodes) vs ``E1 o+ o- in+ in- gain``.
        if len(args) >= 5 and "=" not in args[3]:
            return 4
        if len(args) >= 3 and (len(args) == 3 or "=" in args[3]):
            return 2
        return None
    if letter in {"M", "Q"}:
        # A fourth terminal exists only when a token follows it (the model name).
        if len(args) >= 5:
            return 4
        if len(args) >= 4:
            return 3
        return None
    count = counts[0]
    return count if len(args) >= count else None


# --------------------------------------------------------------------------- #
# the parser


def parse_netlist(text: str, *, source_path: Path | None = None) -> Circuit:
    """Parse SPICE text into a :class:`Circuit`.

    Raises :class:`NetlistError` (with ``.line`` and ``.text``) for an unknown
    element letter, an element with the wrong number of nodes, a duplicate
    refdes in the same scope, a ``.ends`` without an open ``.subckt``, or a
    ``.subckt`` that is never closed.
    """
    circuit = Circuit(source_path=source_path)
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped:
            if stripped.startswith("*"):
                circuit.title = stripped.lstrip("*").strip()
            break
    stack: list[SubcktDef] = []
    for line, body in _logical_lines(text):
        tokens = body.split()
        if not tokens:
            continue
        head = tokens[0]
        if head.startswith("."):
            _parse_directive(tokens, body, line, circuit, stack)
            continue
        device = _parse_device(tokens, line=line, text=body)
        scope_devices = stack[-1].devices if stack else circuit.devices
        previous = scope_devices.get(device.refdes)
        if previous is not None:
            raise NetlistError(
                f"duplicate refdes {device.refdes!r} (first defined on line {previous.line})",
                line=line,
                text=body,
            )
        scope_devices[device.refdes] = device
    if stack:
        open_def = stack[-1]
        raise NetlistError(
            f".subckt {open_def.name!r} is never closed by .ends",
            line=open_def.line,
            text=f".subckt {open_def.name} {' '.join(open_def.ports)}",
        )
    return circuit


def _parse_directive(
    tokens: list[str],
    body: str,
    line: int,
    circuit: Circuit,
    stack: list[SubcktDef],
) -> None:
    keyword = tokens[0].lower()
    if keyword == ".subckt":
        if stack:
            raise NetlistError("nested .subckt definitions are not supported", line=line, text=body)
        if len(tokens) < 2:
            raise NetlistError(".subckt needs a name", line=line, text=body)
        name = tokens[1]
        ports: list[str] = []
        params: dict[str, str] = {}
        collecting_params = False
        for token in tokens[2:]:
            if token.lower().rstrip(":") == "params":
                collecting_params = True
                continue
            if collecting_params:
                key, _, value = token.partition("=")
                if key:
                    params[key] = value
            else:
                ports.append(token)
        if name.lower() in {existing.lower() for existing in circuit.subckts}:
            raise NetlistError(f"duplicate .subckt definition {name!r}", line=line, text=body)
        stack.append(SubcktDef(name=name, ports=tuple(ports), params=params, line=line))
        return
    if keyword == ".ends":
        if not stack:
            raise NetlistError(".ends without an open .subckt", line=line, text=body)
        definition = stack.pop()
        if len(tokens) > 1 and tokens[1].lower() != definition.name.lower():
            raise NetlistError(
                f".ends {tokens[1]!r} does not close {definition.name!r}",
                line=line,
                text=body,
            )
        circuit.subckts[definition.name] = definition
        return
    if keyword in {".include", ".inc", ".lib"}:
        target = body.split(None, 1)[1].strip() if len(body.split(None, 1)) > 1 else ""
        if (target.startswith('"') and target.endswith('"')) or (
            target.startswith("'") and target.endswith("'")
        ):
            target = target[1:-1]
        if not target:
            raise NetlistError(f"{tokens[0]} needs a path", line=line, text=body)
        circuit.includes.append(target)
        return
    if keyword == ".end":
        return
    circuit.directives.append(body)


def parse_netlist_file(path: Path) -> Circuit:
    """Read ``path`` (UTF-8, else UTF-16LE, else latin-1) and parse it."""
    raw = Path(path).read_bytes()
    text = _decode(raw)
    return parse_netlist(text, source_path=Path(path))


def _decode(raw: bytes) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if len(raw) >= 2 and raw[1] == 0:
        return raw.decode("utf-16-le", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


# --------------------------------------------------------------------------- #
# connectivity


class NetMap:
    """Connectivity built from a :class:`Circuit` (flattened through X instances).

    Satisfies :class:`boardmodeler.verification.assertions.Connectivity`, so the
    ``net_equals`` / ``pin_connected`` / ``pullup_domain`` ops can be evaluated
    against a real netlist.
    """

    def __init__(
        self,
        circuit: Circuit,
        symbols: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self.circuit = circuit
        self.symbols: dict[str, tuple[str, ...]] = {
            refdes: tuple(names) for refdes, names in (symbols or {}).items()
        }
        #: Cycles found while flattening (``"<path>:<subckt>"`` entries).
        self.cycles: list[str] = []
        self._pins: dict[str, dict[str, str]] = {}
        self._net_pins: dict[str, list[tuple[str, str]]] = {}
        self._bare: dict[str, str] = {}
        #: Bare refdes of nested devices that resolve to more than one instance.
        self.ambiguous: set[str] = set()
        self._top_level: set[str] = set()
        self._nested_devices: dict[str, Device] = {}
        self._build()

    # -- construction -------------------------------------------------------

    def _build(self) -> None:
        for refdes, device in self.circuit.devices.items():
            self._top_level.add(refdes)
            names = self._pin_names(refdes, device)
            self._add(refdes, device, names)
        for refdes, device in self.circuit.devices.items():
            if device.kind != "X" or not device.subckt:
                continue
            definition = self.circuit.subckts.get(device.subckt)
            if definition is None:
                continue
            bindings = self._bindings(definition, device.nodes)
            self._walk(refdes, definition, bindings, (definition.name.lower(),))

    def _pin_names(self, refdes: str, device: Device) -> tuple[str, ...]:
        names = self.symbols.get(refdes) or self.circuit.pin_orders.get(refdes)
        if names:
            return tuple(names)
        if device.kind == "X" and device.subckt:
            definition = self.circuit.subckts.get(device.subckt)
            if definition is not None:
                return definition.ports
        return ()

    @staticmethod
    def _bindings(definition: SubcktDef, nodes: Sequence[str]) -> dict[str, str]:
        return {
            port: nodes[index] for index, port in enumerate(definition.ports) if index < len(nodes)
        }

    def _add(self, refdes: str, device: Device, names: Sequence[str]) -> None:
        keys: dict[str, str] = {}
        aliases = _PIN_ALIASES.get(device.kind, {})
        for index, node in enumerate(device.nodes):
            keys[str(index + 1)] = node
            if index < len(names) and names[index]:
                keys.setdefault(str(names[index]), node)
        for alias, index in aliases.items():
            if index < len(device.nodes):
                keys.setdefault(alias, device.nodes[index])
        self._pins[refdes] = keys
        for index, node in enumerate(device.nodes):
            label = str(names[index]) if index < len(names) and names[index] else ""
            if not label:
                label = next(
                    (
                        alias
                        for alias, position in aliases.items()
                        if position == index and not alias.isdigit()
                    ),
                    str(index + 1),
                )
            self._net_pins.setdefault(node, []).append((refdes, label))

    def _walk(
        self,
        path: str,
        definition: SubcktDef,
        bindings: Mapping[str, str],
        seen: tuple[str, ...],
    ) -> None:
        for refdes, device in definition.devices.items():
            qualified = f"{path}.{refdes}"
            nodes = tuple(self._resolve(node, bindings, path) for node in device.nodes)
            flattened = Device(
                refdes=qualified,
                kind=device.kind,
                nodes=nodes,
                value=device.value,
                params=dict(device.params),
                subckt=device.subckt,
                extra=device.extra,
                line=device.line,
            )
            names = self._pin_names(qualified, device)
            if not names and device.kind == "X" and device.subckt:
                nested = self.circuit.subckts.get(device.subckt)
                if nested is not None:
                    names = nested.ports
            self._add(qualified, flattened, names)
            self._nested_devices[qualified] = flattened
            previous = self._bare.get(refdes)
            if previous is None:
                self._bare[refdes] = qualified
            elif previous != qualified:
                self.ambiguous.add(refdes)
            if device.kind == "X" and device.subckt:
                nested = self.circuit.subckts.get(device.subckt)
                if nested is None:
                    continue
                if nested.name.lower() in seen:
                    self.cycles.append(f"{qualified}:{nested.name}")
                    continue
                inner_bindings = self._bindings(nested, nodes)
                self._walk(qualified, nested, inner_bindings, (*seen, nested.name.lower()))

    @staticmethod
    def _resolve(node: str, bindings: Mapping[str, str], path: str) -> str:
        if node in bindings:
            return bindings[node]
        return f"{path}#{node}"

    def _entry(self, refdes: str) -> dict[str, str] | None:
        direct = self._pins.get(refdes)
        if direct is not None:
            return direct
        canonical = self.resolve_refdes(refdes)
        if canonical is None:
            return None
        return self._pins.get(canonical)

    def resolve_refdes(self, refdes: str) -> str | None:
        """Canonical netlist token for ``refdes``, or ``None`` when absent.

        SPICE names a subcircuit instance ``X<refdes>``, so a neutral project's
        component name (``U5``) is resolved to the netlist's ``XU5`` when the
        exact token is not present.  An exact match always wins.
        """
        if refdes in self._pins:
            return refdes
        alias = f"X{refdes}"
        if alias in self._pins:
            return alias
        for candidate in (refdes, alias):
            qualified = self._bare.get(candidate)
            if qualified is not None and candidate not in self.ambiguous:
                return qualified
        return None

    def _device(self, refdes: str) -> Device | None:
        canonical = self.resolve_refdes(refdes)
        if canonical is None:
            return None
        device = self.circuit.devices.get(canonical)
        if device is not None:
            return device
        for _path, nested in self._nested_devices.items():
            if nested.refdes == canonical:
                return nested
        return None

    # -- Connectivity protocol ---------------------------------------------

    def node_of(self, refdes: str, pin: str) -> str | None:
        """Net name attached to ``refdes.pin``, or ``None`` when unresolvable."""
        entry = self._entry(refdes)
        if entry is None:
            return None
        return entry.get(pin)

    def pins_on(self, net: str) -> list[tuple[str, str]]:
        """Every ``(refdes, pin)`` attached to ``net``, sorted for stability."""
        return sorted(self._net_pins.get(net, []))

    def has_refdes(self, refdes: str) -> bool:
        """Whether the netlist contains this reference designator at all.

        A component name from neutral data (``U5``) also matches the netlist's
        subcircuit instance token (``XU5``); see :meth:`resolve_refdes`.  A nested
        refdes that appears in more than one instance is reported as present even
        though it cannot be resolved to a single net (see :attr:`ambiguous`).
        """
        if self.resolve_refdes(refdes) is not None:
            return True
        return refdes in self._bare or f"X{refdes}" in self._bare

    # -- extras -------------------------------------------------------------

    def is_ground(self, net: str) -> bool:
        """True for SPICE's global reference node."""
        return net == "0"

    def is_connected_node(self, net: str) -> bool:
        """False for LTspice's dangling-pin placeholders (``NC_01`` ...)."""
        return NC_NODE_RE.match(net) is None

    def port_name(self, refdes: str, pin: str) -> str | None:
        """The subcircuit-internal port name for ``refdes.pin``, when defined.

        This is the subckt-side name (``.subckt SUB A B PG`` -> ``"PG"``) and is
        what the ground-truth pin order of a mapped part is checked against; the
        net answer is :meth:`node_of`.
        """
        device = self._device(refdes)
        if device is None or device.kind != "X" or not device.subckt:
            return None
        definition = self.circuit.subckts.get(device.subckt)
        if definition is None:
            return None
        canonical = self.resolve_refdes(refdes) or refdes
        index = self._index_of(canonical, device, pin)
        if index is None or index >= len(definition.ports):
            return None
        return definition.ports[index]

    def _index_of(self, refdes: str, device: Device, pin: str) -> int | None:
        names = self._pin_names(refdes, device)
        if pin in names:
            return names.index(pin)
        index = _int_or_none(pin)
        if index is not None and 1 <= index <= len(device.nodes):
            return index - 1
        aliases = _PIN_ALIASES.get(device.kind, {})
        if pin.upper() in aliases:
            return aliases[pin.upper()]
        return None


def build_netmap(
    circuit: Circuit,
    symbols: Mapping[str, Sequence[str]] | None = None,
) -> NetMap:
    """Build the flattened connectivity for ``circuit``.

    ``symbols`` maps a refdes to its symbol's pin names in ``SpiceOrder`` order
    (as read from the ``.asy``), which turns ``node_of(refdes, "PG")`` into a
    real answer for instances whose subcircuit ports do not name the pin.
    """
    return NetMap(circuit, symbols)
