"""Static checks SC001-SC010 (D9).

Every check returns :class:`~boardmodeler.domain.records.Finding` rows: one per
violation, plus a single ``PASS`` row when the check ran over real subjects and
found nothing.  A check whose inputs are absent reports ``NOT_APPLICABLE`` with
the reason — never a silent pass.

The supply-domain rule (§9) is the one worth spelling out: **every power-capable
pin is evaluated individually**.  Grouping pins into a simulated domain must not
hide a disconnected pin, a pin on the wrong rail, or two domains shorted onto
one net, so ``SC009`` emits one finding per offending pin (naming the pin, the
net it is actually on and the expected domain) and one finding per net that
carries more than one declared domain.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from boardmodeler.domain.enums import Status
from boardmodeler.domain.records import Finding, PinDefinition
from boardmodeler.models.library import ModelRecord, subckt_ports, subckts_in_text
from boardmodeler.schematic.asc import asy_attributes, read_text
from boardmodeler.schematic.netlist import (
    NC_NODE_RE,
    TERMINAL_COUNTS,
    Circuit,
    Device,
    NetMap,
    display_refdes,
)
from boardmodeler.schematic.neutral import REFDES_RE, NeutralProject

__all__ = ["CHECK_CODES", "run_static_checks"]

CHECK_CODES: tuple[str, ...] = (
    "SC001_syntax",
    "SC002_units_names",
    "SC003_missing_dependency",
    "SC004_part_identity",
    "SC005_pinmap_physical_symbol_subckt",
    "SC006_symbol_prefix_model",
    "SC007_duplicate_dropped_connections",
    "SC008_export_portability",
    "SC009_supply_domain_assignment",
    "SC010_abstraction_boundary",
)

_MAX_INCLUDE_DEPTH = 8
_PLACEHOLDER_PARTS = frozenset({"tbd", "?", "unknown", "n/a", "na", "none", "-"})
#: Characters no SPICE node name may contain (``in+`` and ``out/2`` are legal).
_ILLEGAL_NODE_CHARS = re.compile(r"[\s,=()\[\]*;'\"]")
_NUMERIC_RE = re.compile(r"^\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
_MILLI_TRAP_RE = re.compile(r"^[Mm](?![eE][gG])")
_KNOWN_MULTIPLIERS = ("meg", "mil", "f", "p", "n", "u", "m", "k", "g", "t")
_POWER_DIRECTIONS = ("power", "ground")


# --------------------------------------------------------------------------- #
# finding helpers


def _finding(
    code: str,
    status: Status,
    message: str,
    *,
    refdes: str | None = None,
    nets: Sequence[str] = (),
    detail: Mapping[str, str] | None = None,
) -> Finding:
    return Finding(
        code=code,
        status=status,
        refdes=refdes,
        nets=list(nets),
        message=message,
        detail=dict(detail or {}),
    )


def _fail(code: str, message: str, **kwargs: object) -> Finding:
    return _finding(code, Status.FAIL, message, **kwargs)  # type: ignore[arg-type]


def _pass(code: str, message: str, **kwargs: object) -> Finding:
    return _finding(code, Status.PASS, message, **kwargs)  # type: ignore[arg-type]


def _not_applicable(code: str, message: str, **kwargs: object) -> Finding:
    return _finding(code, Status.NOT_APPLICABLE, message, **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# entry point


def run_static_checks(
    circuit: Circuit,
    netmap: NetMap,
    *,
    project: NeutralProject | None = None,
    pins: Mapping[str, Sequence[PinDefinition]] | None = None,
    model_records: Mapping[str, ModelRecord] | None = None,
    symbol_pins: Mapping[str, Sequence[str]] | None = None,
    symbol_texts: Mapping[str, str | Path] | None = None,
    project_root: str | Path | None = None,
) -> list[Finding]:
    """Run SC001-SC010 in code order and return every finding.

    ``pins`` maps a refdes to its datasheet :class:`PinDefinition` list,
    ``symbol_pins``/``symbol_texts`` to the part's symbol pin order and ``.asy``
    text (or path), ``model_records`` to the project's stored models, and
    ``project_root`` to the root an export must stay inside (defaults to the
    netlist's own directory).
    """
    root = _root_for(project_root, project, circuit)
    checks = (
        lambda: _sc001_syntax(circuit),
        lambda: _sc002_units_names(circuit),
        lambda: _sc003_missing_dependency(circuit, root, model_records),
        lambda: _sc004_part_identity(project),
        lambda: _sc005_pinmap(circuit, netmap, project, pins, symbol_pins, root, model_records),
        lambda: _sc006_symbol_prefix_model(circuit, symbol_texts, root),
        lambda: _sc007_duplicate_connections(project),
        lambda: _sc008_export_portability(circuit, symbol_texts, root),
        lambda: _sc009_supply_domains(netmap, project, pins),
        lambda: _sc010_abstraction_boundary(netmap, project, pins, symbol_pins),
    )
    findings: list[Finding] = []
    for check in checks:
        findings.extend(check())
    return findings


def _root_for(
    explicit: str | Path | None, project: NeutralProject | None, circuit: Circuit
) -> Path | None:
    if explicit is not None:
        return Path(explicit)
    if project is not None and project.root is not None:
        return Path(project.root)
    if circuit.source_path is not None:
        return Path(circuit.source_path).parent
    return None


# --------------------------------------------------------------------------- #
# SC001 — syntax


def _sc001_syntax(circuit: Circuit) -> list[Finding]:
    code = CHECK_CODES[0]
    findings: list[Finding] = []
    for refdes, device in circuit.devices.items():
        if device.kind == "X":
            if not device.subckt:
                findings.append(
                    _fail(
                        code,
                        f"{refdes} is a subcircuit instance without a subcircuit name",
                        refdes=refdes,
                        nets=device.nodes,
                        detail={"reason": "x_instance_without_subckt"},
                    )
                )
            if not device.nodes:
                findings.append(
                    _fail(
                        code,
                        f"{refdes} is a subcircuit instance with no nodes",
                        refdes=refdes,
                        detail={"reason": "x_instance_without_nodes"},
                    )
                )
            continue
        if device.kind == "K":
            if len(device.extra) < 2:
                findings.append(
                    _fail(
                        code,
                        f"{refdes} couples fewer than two inductors",
                        refdes=refdes,
                        detail={"reason": "coupling_without_inductors"},
                    )
                )
            continue
        counts = TERMINAL_COUNTS.get(device.kind)
        if counts is None:
            findings.append(
                _fail(
                    code,
                    f"{refdes} has element letter {device.kind!r}, which this parser does not model",
                    refdes=refdes,
                    detail={"reason": "unknown_element_letter", "kind": device.kind},
                )
            )
            continue
        if len(device.nodes) not in counts:
            findings.append(
                _fail(
                    code,
                    f"{refdes} has {len(device.nodes)} node(s); element {device.kind!r} needs "
                    f"{' or '.join(str(count) for count in counts)}",
                    refdes=refdes,
                    nets=device.nodes,
                    detail={"reason": "wrong_node_count", "expected": str(list(counts))},
                )
            )
    for directive in circuit.directives:
        if directive.count("(") != directive.count(")"):
            findings.append(
                _fail(
                    code,
                    f"directive has unbalanced parentheses: {directive}",
                    detail={"reason": "unbalanced_parentheses", "directive": directive},
                )
            )
    if findings:
        return findings
    return [
        _pass(
            code,
            f"parsed {len(circuit.devices)} device(s), {len(circuit.subckts)} subcircuit(s), "
            f"{len(circuit.includes)} include(s), {len(circuit.directives)} directive(s)",
            detail={
                "devices": str(len(circuit.devices)),
                "subckts": str(len(circuit.subckts)),
                "includes": str(len(circuit.includes)),
                "directives": str(len(circuit.directives)),
            },
        )
    ]


# --------------------------------------------------------------------------- #
# SC002 — units and names


def _sc002_units_names(circuit: Circuit) -> list[Finding]:
    code = CHECK_CODES[1]
    findings: list[Finding] = []
    nodes_checked = 0
    values_checked = 0
    for refdes, device in circuit.devices.items():
        if not REFDES_RE.match(refdes):
            findings.append(
                _fail(
                    code,
                    f"refdes {refdes!r} is not a legal reference designator",
                    refdes=refdes,
                    detail={"reason": "illegal_refdes", "name": refdes},
                )
            )
        for node in device.nodes:
            nodes_checked += 1
            if not node or _ILLEGAL_NODE_CHARS.search(node):
                findings.append(
                    _fail(
                        code,
                        f"node name {node!r} on {refdes} is illegal in SPICE",
                        refdes=refdes,
                        nets=[node],
                        detail={"reason": "illegal_node", "node": node, "refdes": refdes},
                    )
                )
        if device.kind in {"R", "L", "C", "D", "S", "J", "M", "Q"}:
            values_checked += 1
            findings.extend(_check_value(code, refdes, device.value))
    if findings:
        return findings
    return [
        _pass(
            code,
            f"checked {len(circuit.devices)} refdes, {nodes_checked} node name(s) and "
            f"{values_checked} value(s)",
            detail={
                "devices": str(len(circuit.devices)),
                "nodes": str(nodes_checked),
                "values": str(values_checked),
            },
        )
    ]


def _check_value(code: str, refdes: str, value: str) -> list[Finding]:
    findings: list[Finding] = []
    text = value.strip()
    if not text:
        return [
            _fail(
                code,
                f"{refdes} has an empty value",
                refdes=refdes,
                detail={"reason": "empty_value", "value": value},
            )
        ]
    if text.startswith(("{", "'")):
        return findings
    if len(text.split()) > 1:
        findings.append(
            _fail(
                code,
                f"{refdes} value {text!r} contains whitespace; SPICE reads only the first token",
                refdes=refdes,
                detail={"reason": "value_with_whitespace", "value": text},
            )
        )
        return findings
    numeric = _NUMERIC_RE.match(text)
    if numeric is None:
        return findings
    suffix = text[numeric.end() :]
    if _MILLI_TRAP_RE.match(suffix):
        findings.append(
            _fail(
                code,
                f"{refdes} value {text!r} reads as milli ({suffix[0]}), not mega; "
                "write 'meg' for 1e6",
                refdes=refdes,
                detail={
                    "reason": "milli_not_mega",
                    "value": text,
                    "interpreted_multiplier": "1e-3",
                },
            )
        )
        return findings
    lowered = suffix.lower()
    if suffix and not lowered.startswith(_KNOWN_MULTIPLIERS):
        findings.append(
            _fail(
                code,
                f"{refdes} value {text!r} carries the unknown unit suffix {suffix!r}",
                refdes=refdes,
                detail={"reason": "unknown_unit_suffix", "value": text, "suffix": suffix},
            )
        )
    return findings


# --------------------------------------------------------------------------- #
# SC003 — missing dependency


def _sc003_missing_dependency(
    circuit: Circuit,
    root: Path | None,
    model_records: Mapping[str, ModelRecord] | None,
) -> list[Finding]:
    code = CHECK_CODES[2]
    findings: list[Finding] = []
    available, missing = _resolve_includes(circuit, root)
    for target, resolved in missing:
        findings.append(
            _fail(
                code,
                f"include {target!r} cannot be found; instances depending on it cannot be resolved",
                detail={
                    "reason": "include_not_found",
                    "include": target,
                    "searched_from": str(resolved),
                },
            )
        )
    records = model_records or {}
    record_subckts: dict[str, str] = {}
    for model_id, record in records.items():
        for name in record.subckts:
            record_subckts.setdefault(name.lower(), model_id)
    checked = 0
    known = {name.lower() for name in circuit.subckts}
    available_lowered = {name.lower() for name in available}
    for refdes, scope, device in _iter_devices(circuit):
        if device.kind != "X" or not device.subckt:
            continue
        checked += 1
        target = device.subckt
        lowered = target.lower()
        if lowered in known or lowered in available_lowered:
            continue
        if lowered in record_subckts or lowered in {key.lower() for key in records}:
            continue
        findings.append(
            _fail(
                code,
                f"{display_refdes(refdes)} instantiates {target!r}, which is not defined in the "
                "circuit, any include that exists on disk, or any supplied model record",
                refdes=display_refdes(refdes),
                nets=device.nodes,
                detail={
                    "reason": "subckt_not_found",
                    "subckt": target,
                    "device": refdes,
                    "scope": scope or "top",
                    "defined_subckts": ", ".join(sorted(circuit.subckts)),
                    "included_subckts": ", ".join(sorted(available)),
                },
            )
        )
    if findings:
        return findings
    return [
        _pass(
            code,
            f"resolved {checked} subcircuit instance(s) against {len(available)} included "
            f"definition(s)",
            detail={"instances": str(checked), "included_subckts": str(len(available))},
        )
    ]


def _iter_devices(circuit: Circuit) -> list[tuple[str, str, Device]]:
    """Top-level devices, then every device declared inside a ``.subckt`` body.

    Each entry is ``(refdes, scope, device)``; ``scope`` is the enclosing
    subcircuit name (empty at the top level), so a finding can point at the body
    that contains it.
    """
    pairs: list[tuple[str, str, Device]] = [
        (refdes, "", device) for refdes, device in circuit.devices.items()
    ]
    for name, definition in circuit.subckts.items():
        pairs.extend((refdes, name, device) for refdes, device in definition.devices.items())
    return pairs


def _resolve_includes(
    circuit: Circuit, root: Path | None
) -> tuple[set[str], list[tuple[str, Path]]]:
    """Subcircuit names reachable from the circuit's includes; plus missing files."""
    names: set[str] = set()
    missing: list[tuple[str, Path]] = []
    visited: set[Path] = set()
    pending: list[tuple[str, int]] = [(target, 0) for target in circuit.includes]
    base = root or (Path(circuit.source_path).parent if circuit.source_path else Path.cwd())
    while pending:
        target, depth = pending.pop()
        resolved = _resolve_include(target, base)
        if resolved is None:
            missing.append((target, base))
            continue
        if resolved in visited:
            continue
        visited.add(resolved)
        try:
            text = read_text(resolved)
        except OSError:
            missing.append((target, base))
            continue
        names.update(subckts_in_text(text))
        if depth + 1 < _MAX_INCLUDE_DEPTH:
            for line in text.splitlines():
                stripped = line.strip().lower()
                if stripped.startswith((".include", ".lib", ".inc")):
                    parts = line.split(None, 1)
                    if len(parts) > 1:
                        nested = parts[1].strip().strip("\"'")
                        pending.append((nested, depth + 1))
    return names, missing


def _resolve_include(target: str, base: Path) -> Path | None:
    """Resolve an ``.include``/``.lib`` target the way LTspice would."""
    candidate = Path(target)
    attempts: list[Path] = []
    if candidate.is_absolute():
        attempts.append(candidate)
    else:
        attempts.extend([base / candidate, base.parent / candidate])
    lib = _ltspice_lib_dir()
    if lib is not None:
        attempts.extend(
            [lib / candidate, lib / "sub" / candidate.name, lib / "cmp" / candidate.name]
        )
    for attempt in attempts:
        try:
            if attempt.is_file():
                return attempt.resolve()
        except OSError:
            continue
    return None


def _ltspice_lib_dir() -> Path | None:
    from boardmodeler.simulation.ltspice import default_lib_dir

    return default_lib_dir()


def _inside_lib(path: Path) -> bool:
    lib = _ltspice_lib_dir()
    if lib is None:
        return False
    try:
        return Path(path).resolve().is_relative_to(Path(lib).resolve())
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# SC004 — part identity


def _sc004_part_identity(project: NeutralProject | None) -> list[Finding]:
    code = CHECK_CODES[3]
    if project is None:
        return [_not_applicable(code, "no neutral project was supplied")]
    if not project.components:
        return [_not_applicable(code, "the project declares no components")]
    findings: list[Finding] = []
    for row in project.components:
        part = row.part_number.strip()
        if not part or part.lower() in _PLACEHOLDER_PARTS:
            findings.append(
                _fail(
                    code,
                    f"{row.refdes} has no usable part number ({part or 'empty'!r}), so it is "
                    "not identified",
                    refdes=row.refdes,
                    detail={
                        "reason": "unidentified_part",
                        "part_number": part,
                        "manufacturer": row.manufacturer,
                        "package": row.package,
                    },
                )
            )
        elif not row.manufacturer.strip():
            findings.append(
                _fail(
                    code,
                    f"{row.refdes} ({part}) has no manufacturer",
                    refdes=row.refdes,
                    detail={
                        "reason": "missing_manufacturer",
                        "part_number": part,
                        "manufacturer": "",
                        "package": row.package,
                    },
                )
            )
    if findings:
        return findings
    return [
        _pass(
            code,
            f"every one of {len(project.components)} component(s) carries a manufacturer and "
            "part number",
            detail={"components": str(len(project.components))},
        )
    ]


# --------------------------------------------------------------------------- #
# SC005 — physical pin -> symbol pin -> subcircuit port


def _sc005_pinmap(
    circuit: Circuit,
    netmap: NetMap,
    project: NeutralProject | None,
    pins: Mapping[str, Sequence[PinDefinition]] | None,
    symbol_pins: Mapping[str, Sequence[str]] | None,
    root: Path | None,
    model_records: Mapping[str, ModelRecord] | None,
) -> list[Finding]:
    code = CHECK_CODES[4]
    if not pins:
        return [_not_applicable(code, "no PinDefinition lists were supplied")]
    if not symbol_pins:
        return [_not_applicable(code, "no symbol pin orders were supplied")]
    omitted = _declared_omissions(project)
    findings: list[Finding] = []
    parts_checked = 0
    mapped_pins = 0
    for refdes, definitions in pins.items():
        if not netmap.has_refdes(refdes):
            continue
        symbol = tuple(symbol_pins.get(refdes, ()))
        if not symbol:
            continue
        parts_checked += 1
        device = circuit.device_of(refdes)
        ports = _subckt_ports_for(device, circuit, root, model_records)
        if ports is None:
            findings.append(
                _fail(
                    code,
                    f"{refdes}: the subcircuit port list is unknown, so the symbol pin order "
                    "cannot be checked against it",
                    refdes=refdes,
                    detail={
                        "reason": "subckt_ports_unknown",
                        "subckt": (device.subckt if device else "") or "",
                    },
                )
            )
        elif len(ports) != len(symbol):
            findings.append(
                _fail(
                    code,
                    f"{refdes}: symbol declares {len(symbol)} pin(s) but subcircuit has "
                    f"{len(ports)} port(s)",
                    refdes=refdes,
                    detail={
                        "reason": "arity_mismatch",
                        "symbol_pins": ", ".join(symbol),
                        "subckt_ports": ", ".join(ports),
                    },
                )
            )
        seen_symbol_pins: dict[str, str] = {}
        for definition in definitions:
            mapped = (definition.mapped_symbol_pin or "").strip()
            if not mapped:
                continue
            mapped_pins += 1
            if mapped not in symbol:
                findings.append(
                    _fail(
                        code,
                        f"{refdes}: physical pin {definition.physical_pin} maps to symbol pin "
                        f"{mapped!r}, which the symbol does not declare",
                        refdes=refdes,
                        detail={
                            "reason": "symbol_pin_unknown",
                            "physical_pin": definition.physical_pin,
                            "mapped_symbol_pin": mapped,
                            "symbol_pins": ", ".join(symbol),
                        },
                    )
                )
                continue
            if mapped in seen_symbol_pins:
                findings.append(
                    _fail(
                        code,
                        f"{refdes}: symbol pin {mapped!r} is claimed by both "
                        f"{seen_symbol_pins[mapped]} and {definition.physical_pin}",
                        refdes=refdes,
                        detail={
                            "reason": "duplicate_symbol_pin",
                            "physical_pin": definition.physical_pin,
                            "mapped_symbol_pin": mapped,
                            "other_physical_pin": seen_symbol_pins[mapped],
                        },
                    )
                )
            else:
                seen_symbol_pins[mapped] = definition.physical_pin
            if device is not None and symbol.index(mapped) >= len(device.nodes):
                findings.append(
                    _fail(
                        code,
                        f"{refdes}: symbol pin {mapped!r} (SpiceOrder {symbol.index(mapped) + 1}) "
                        f"has no node in the instance",
                        refdes=refdes,
                        detail={
                            "reason": "node_missing",
                            "mapped_symbol_pin": mapped,
                            "node_count": str(len(device.nodes)),
                        },
                    )
                )
        for name in symbol:
            if name in seen_symbol_pins or name in omitted.get(refdes, set()):
                continue
            findings.append(
                _fail(
                    code,
                    f"{refdes}: symbol pin {name!r} is not mapped from any physical pin and is "
                    "not declared as an omitted pin",
                    refdes=refdes,
                    detail={"reason": "unmapped_symbol_pin", "mapped_symbol_pin": name},
                )
            )
    if findings:
        return findings
    if not parts_checked:
        return [
            _not_applicable(
                code, "no refdes with both a pin map and a symbol pin order was in the netlist"
            )
        ]
    return [
        _pass(
            code,
            f"{parts_checked} part(s): physical pin -> symbol pin -> subcircuit port is a "
            f"bijection over {mapped_pins} mapped pin(s)",
            detail={"parts": str(parts_checked), "mapped_pins": str(mapped_pins)},
        )
    ]


def _declared_omissions(project: NeutralProject | None) -> dict[str, set[str]]:
    omitted: dict[str, set[str]] = {}
    if project is None:
        return omitted
    for boundary in project.abstractions:
        omitted.setdefault(boundary.refdes, set()).update(boundary.omitted_pins)
    return omitted


def _subckt_ports_for(
    device: object,
    circuit: Circuit,
    root: Path | None,
    model_records: Mapping[str, ModelRecord] | None,
) -> tuple[str, ...] | None:
    subckt = getattr(device, "subckt", None)
    if not subckt:
        return None
    local = circuit.subckts.get(subckt)
    if local is not None:
        return local.ports
    names, _missing = _resolve_includes(circuit, root)
    if subckt in names:
        base = root or (Path(circuit.source_path).parent if circuit.source_path else Path.cwd())
        for target in circuit.includes:
            resolved = _resolve_include(target, base)
            if resolved is None:
                continue
            text = read_text(resolved)
            if subckt in subckts_in_text(text):
                ports = subckt_ports(text, subckt)
                if ports:
                    return ports
    for record in (model_records or {}).values():
        if subckt in record.subckts and root is not None:
            path = record.absolute(root)
            if path.is_file():
                text = read_text(path)
                ports = subckt_ports(text, subckt)
                if ports:
                    return ports
    return None


# --------------------------------------------------------------------------- #
# SC006 — symbol prefix and model file


def _sc006_symbol_prefix_model(
    circuit: Circuit,
    symbol_texts: Mapping[str, str | Path] | None,
    root: Path | None,
) -> list[Finding]:
    code = CHECK_CODES[5]
    if not symbol_texts:
        return [_not_applicable(code, "no symbol (.asy) texts were supplied")]
    findings: list[Finding] = []
    checked = 0
    for refdes, source in symbol_texts.items():
        device = circuit.device_of(refdes)
        if device is None or device.kind != "X":
            continue
        checked += 1
        path = Path(source) if isinstance(source, Path) else None
        text = read_text(path) if path is not None and path.is_file() else str(source)
        attributes = asy_attributes(text)
        prefix = attributes.get("Prefix", "").strip()
        model = attributes.get("SpiceModel", "").strip()
        if prefix != "X":
            findings.append(
                _fail(
                    code,
                    f"{refdes}: symbol declares Prefix {prefix!r}; a subcircuit instance needs 'X'",
                    refdes=refdes,
                    detail={"reason": "prefix_not_x", "prefix": prefix, "expected": "X"},
                )
            )
        if not model:
            findings.append(
                _fail(
                    code,
                    f"{refdes}: symbol declares no SpiceModel, so LTspice cannot include the model",
                    refdes=refdes,
                    detail={"reason": "missing_spicemodel", "spicemodel": ""},
                )
            )
            continue
        bases = [base for base in (path.parent if path else None, root) if base is not None]
        resolved = None
        for base in bases:
            resolved = _resolve_include(model, Path(base))
            if resolved is not None:
                break
        if resolved is None:
            findings.append(
                _fail(
                    code,
                    f"{refdes}: SpiceModel {model!r} was not found; LTspice would fail to "
                    "include it",
                    refdes=refdes,
                    detail={
                        "reason": "spicemodel_not_found",
                        "spicemodel": model,
                        "searched": ", ".join(str(base) for base in bases),
                    },
                )
            )
    if findings:
        return findings
    if not checked:
        return [_not_applicable(code, "no supplied symbol belongs to a subcircuit instance")]
    return [
        _pass(
            code,
            f"{checked} subcircuit symbol(s) declare Prefix X and an existing SpiceModel",
            detail={"symbols": str(checked)},
        )
    ]


# --------------------------------------------------------------------------- #
# SC007 — duplicate / dropped connections


def _sc007_duplicate_connections(project: NeutralProject | None) -> list[Finding]:
    code = CHECK_CODES[6]
    if project is None:
        return [_not_applicable(code, "no neutral project was supplied")]
    grouped: dict[tuple[str, str], list[str]] = {}
    for row in project.connections:
        grouped.setdefault((row.refdes, row.physical_pin), []).append(row.net_name)
    findings: list[Finding] = []
    for (refdes, pin), nets in grouped.items():
        unique = list(dict.fromkeys(nets))
        if len(unique) < 2:
            continue
        findings.append(
            _fail(
                code,
                f"{refdes}.{pin} is connected to {len(unique)} nets ({', '.join(unique)}); the "
                "schematic tool would keep one and silently drop the rest",
                refdes=refdes,
                nets=unique,
                detail={
                    "physical_pin": pin,
                    "nets": ", ".join(unique),
                    "connections": str(len(nets)),
                },
            )
        )
    if findings:
        return findings
    return [
        _pass(
            code,
            f"{len(project.connections)} connection(s) attach each pin to at most one net",
            detail={"connections": str(len(project.connections))},
        )
    ]


# --------------------------------------------------------------------------- #
# SC008 — export portability


def _sc008_export_portability(
    circuit: Circuit,
    symbol_texts: Mapping[str, str | Path] | None,
    root: Path | None,
) -> list[Finding]:
    code = CHECK_CODES[7]
    if root is None:
        return [_not_applicable(code, "no project root is known, so portability cannot be judged")]
    findings: list[Finding] = []
    lib_refs = 0
    for target in circuit.includes:
        resolved = _resolve_include(target, root)
        if resolved is None:
            findings.append(
                _fail(
                    code,
                    f"include {target!r} does not resolve, so the export cannot be portable",
                    detail={"reason": "include_unresolved", "include": target, "root": str(root)},
                )
            )
        elif _inside_lib(resolved):
            lib_refs += 1
        elif not _within(resolved, root):
            findings.append(
                _fail(
                    code,
                    f"include {target!r} resolves outside the project root ({resolved})",
                    detail={
                        "reason": "include_outside_root",
                        "include": target,
                        "resolved": str(resolved),
                        "root": str(root),
                    },
                )
            )
    for refdes, source in (symbol_texts or {}).items():
        path = Path(source) if isinstance(source, Path) else None
        if path is None or not path.is_file():
            continue
        attributes = asy_attributes(read_text(path))
        model = attributes.get("SpiceModel", "").strip()
        if not model:
            continue
        resolved = _resolve_include(model, path.parent)
        if resolved is None:
            resolved = _resolve_include(model, root)
        if resolved is None:
            findings.append(
                _fail(
                    code,
                    f"{refdes}: SpiceModel {model!r} does not resolve, so the export cannot be "
                    "portable",
                    refdes=refdes,
                    detail={
                        "reason": "spicemodel_unresolved",
                        "spicemodel": model,
                        "root": str(root),
                    },
                )
            )
        elif _inside_lib(resolved):
            lib_refs += 1
        elif not _within(resolved, root):
            findings.append(
                _fail(
                    code,
                    f"{refdes}: SpiceModel {model!r} resolves outside the project root ({resolved})",
                    refdes=refdes,
                    detail={
                        "reason": "spicemodel_outside_root",
                        "spicemodel": model,
                        "resolved": str(resolved),
                        "root": str(root),
                    },
                )
            )
    if findings:
        return findings
    return [
        _pass(
            code,
            f"every include and model target stays inside {root}"
            + (f" ({lib_refs} resolved from the LTspice library)" if lib_refs else ""),
            detail={
                "root": str(root),
                "includes": str(len(circuit.includes)),
                "lib_dir_refs": str(lib_refs),
            },
        )
    ]


def _within(path: Path, root: Path) -> bool:
    try:
        return Path(path).resolve().is_relative_to(Path(root).resolve())
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# SC009 — supply domain assignment


def _sc009_supply_domains(
    netmap: NetMap,
    project: NeutralProject | None,
    pins: Mapping[str, Sequence[PinDefinition]] | None,
) -> list[Finding]:
    code = CHECK_CODES[8]
    if not pins:
        return [_not_applicable(code, "no PinDefinition lists were supplied")]
    domains = dict(project.supply_domains) if project is not None else {}
    findings: list[Finding] = []
    examined = 0
    per_net: dict[str, list[tuple[str, str, str]]] = {}
    for refdes, definitions in pins.items():
        if not netmap.has_refdes(refdes):
            continue
        for definition in definitions:
            if definition.direction not in _POWER_DIRECTIONS and not definition.supply_domain:
                continue
            examined += 1
            expected = (definition.supply_domain or "").strip()
            keys = [
                key
                for key in (
                    definition.mapped_symbol_pin,
                    definition.physical_pin,
                    definition.name,
                )
                if key
            ]
            observed: str | None = None
            for key in keys:
                observed = netmap.node_of(refdes, key)
                if observed is not None:
                    break
            if observed is None:
                findings.append(
                    _fail(
                        code,
                        f"{refdes}.{definition.physical_pin} is a "
                        f"{definition.direction} pin that is not connected to any net",
                        refdes=refdes,
                        detail={
                            "reason": "disconnected",
                            "physical_pin": definition.physical_pin,
                            "pin_keys_tried": ", ".join(keys),
                            "observed_net": "",
                            "expected_domain": expected,
                            "direction": definition.direction,
                        },
                    )
                )
                continue
            if NC_NODE_RE.match(observed):
                findings.append(
                    _fail(
                        code,
                        f"{refdes}.{definition.physical_pin} sits on {observed}, LTspice's "
                        "marker for a pin with no wire",
                        refdes=refdes,
                        nets=[observed],
                        detail={
                            "reason": "disconnected",
                            "physical_pin": definition.physical_pin,
                            "observed_net": observed,
                            "expected_domain": expected,
                            "direction": definition.direction,
                        },
                    )
                )
                continue
            observed_domain = domains.get(observed, "")
            per_net.setdefault(observed, []).append((refdes, definition.physical_pin, expected))
            if not expected or observed_domain == expected:
                continue
            detail = {
                "physical_pin": definition.physical_pin,
                "observed_net": observed,
                "observed_domain": observed_domain,
                "expected_domain": expected,
                "direction": definition.direction,
            }
            if not observed_domain:
                # Not checkable rather than wrong: no domain is declared for the
                # net, so the pin's declared domain cannot be confirmed from the
                # netlist.  Saying PASS here would be a guess; saying FAIL would
                # be an accusation.
                findings.append(
                    _finding(
                        code,
                        Status.UNKNOWN,
                        f"{refdes}.{definition.physical_pin} declares domain {expected!r} but net "
                        f"{observed!r} has no declared domain, so the assignment cannot be "
                        "confirmed",
                        refdes=refdes,
                        nets=[observed],
                        detail={**detail, "reason": "net_domain_undeclared"},
                    )
                )
                continue
            findings.append(
                _fail(
                    code,
                    f"{refdes}.{definition.physical_pin} declares domain {expected!r} but "
                    f"sits on net {observed!r} whose declared domain is {observed_domain!r}",
                    refdes=refdes,
                    nets=[observed],
                    detail={**detail, "reason": "domain_mismatch"},
                )
            )
    for net, entries in per_net.items():
        declared = [domain for _refdes, _pin, domain in entries if domain]
        unique = sorted(set(declared))
        if len(unique) < 2:
            continue
        pins_on_net = ", ".join(
            f"{refdes}.{pin}({domain or 'undeclared'})" for refdes, pin, domain in entries
        )
        findings.append(
            _fail(
                code,
                f"supply domains {' and '.join(unique)} are shorted onto net {net!r}",
                nets=[net],
                detail={
                    "reason": "domains_shorted",
                    "net": net,
                    "domains": ", ".join(unique),
                    "pins": pins_on_net,
                },
            )
        )
    if findings:
        return findings
    if not examined:
        return [_not_applicable(code, "no power-capable pin was found in the supplied pin lists")]
    return [
        _pass(
            code,
            f"{examined} power-capable pin(s) were evaluated individually against the net each "
            "one is really on",
            detail={"pins_examined": str(examined), "nets": str(len(per_net))},
        )
    ]


# --------------------------------------------------------------------------- #
# SC010 — abstraction boundary


def _sc010_abstraction_boundary(
    netmap: NetMap,
    project: NeutralProject | None,
    pins: Mapping[str, Sequence[PinDefinition]] | None,
    symbol_pins: Mapping[str, Sequence[str]] | None,
) -> list[Finding]:
    code = CHECK_CODES[9]
    if project is None:
        return [_not_applicable(code, "no neutral project was supplied")]
    if not project.abstractions:
        return [_not_applicable(code, "the project declares no abstraction boundary")]
    findings: list[Finding] = []
    for boundary in project.abstractions:
        if not netmap.has_refdes(boundary.refdes):
            findings.append(
                _fail(
                    code,
                    f"abstraction boundary names {boundary.refdes}, which is not in the circuit",
                    refdes=boundary.refdes,
                    detail={"reason": "refdes_not_in_circuit", "refdes": boundary.refdes},
                )
            )
            continue
        known = _known_pins(boundary.refdes, pins, symbol_pins)
        for omitted in boundary.omitted_pins:
            if omitted in known:
                continue
            findings.append(
                _fail(
                    code,
                    f"{boundary.refdes}: omitted pin {omitted!r} is not a pin of the part "
                    f"(known: {', '.join(sorted(known)) or 'none'})",
                    refdes=boundary.refdes,
                    detail={
                        "reason": "omitted_pin_not_defined",
                        "omitted_pin": omitted,
                        "known_pins": ", ".join(sorted(known)),
                    },
                )
            )
        if not boundary.preserved_connectivity:
            findings.append(
                _finding(
                    code,
                    Status.UNKNOWN,
                    f"{boundary.refdes}: the abstraction does not preserve connectivity "
                    f"({boundary.reason}); results that depend on this boundary are not "
                    "established by this netlist",
                    refdes=boundary.refdes,
                    detail={
                        "reason": "connectivity_not_preserved",
                        "preserved_connectivity": "false",
                        "boundary_reason": boundary.reason,
                    },
                )
            )
    if findings:
        return findings
    return [
        _pass(
            code,
            f"{len(project.abstractions)} abstraction boundary/boundaries name existing parts "
            "and real pins",
            detail={"abstractions": str(len(project.abstractions))},
        )
    ]


def _known_pins(
    refdes: str,
    pins: Mapping[str, Sequence[PinDefinition]] | None,
    symbol_pins: Mapping[str, Sequence[str]] | None,
) -> set[str]:
    known: set[str] = set()
    for key in (refdes, f"X{refdes}"):
        known.update(name for name in (symbol_pins or {}).get(key, ()))
        for definition in (pins or {}).get(key, ()):
            known.update(
                name
                for name in (
                    definition.physical_pin,
                    definition.name,
                    definition.mapped_symbol_pin,
                )
                if name
            )
    return known
