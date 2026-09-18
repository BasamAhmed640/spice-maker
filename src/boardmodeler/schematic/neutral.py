"""Neutral CSV/JSON project model (D9, INTERFACES §3).

A *neutral project* describes a circuit independently of any schematic tool:

``components.csv``   ``refdes,manufacturer,part_number,package,value``
``connections.csv``  ``refdes,physical_pin,net_name``
``project.json``     model assignments, supply domains, loads, timing,
                     configuration, declared nets and abstraction boundaries

``connections.csv`` row order is significant: it defines the pin order of a
mapped part, which is what :func:`to_circuit` turns into a subcircuit instance's
node order (and therefore what ``NetMap.node_of("U1", "PG")`` resolves).

Validation never guesses: every violation is a :class:`Finding` with
``status=Status.FAIL`` naming the offending row.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from boardmodeler.domain.enums import Status
from boardmodeler.domain.records import AbstractionBoundary, Finding
from boardmodeler.schematic.netlist import ELEMENT_LETTERS, Circuit, Device

__all__ = [
    "COMPONENTS_CSV",
    "COMPONENT_FIELDS",
    "CONNECTIONS_CSV",
    "CONNECTIONS_FIELDS",
    "NEUTRAL001_DUPLICATE_REFDES",
    "NEUTRAL002_DUPLICATE_PIN",
    "NEUTRAL003_PIN_NOT_IN_PART",
    "NEUTRAL004_NO_MODEL_ASSIGNMENT",
    "NEUTRAL005_ILLEGAL_NAME",
    "NEUTRAL006_NET_NOT_DECLARED",
    "NEUTRAL007_MISSING_COMPONENT",
    "NEUTRAL_CODES",
    "PROJECT_JSON",
    "ComponentRow",
    "ConnectionRow",
    "NeutralProject",
    "read_components",
    "read_connections",
    "read_neutral_project",
    "to_circuit",
    "validate_neutral",
    "write_components",
    "write_connections",
    "write_neutral_project",
    "write_project_json",
]

COMPONENTS_CSV = "components.csv"
CONNECTIONS_CSV = "connections.csv"
PROJECT_JSON = "project.json"

COMPONENT_FIELDS: tuple[str, ...] = ("refdes", "manufacturer", "part_number", "package", "value")
CONNECTIONS_FIELDS: tuple[str, ...] = ("refdes", "physical_pin", "net_name")

NEUTRAL001_DUPLICATE_REFDES = "NEUTRAL001_duplicate_refdes"
NEUTRAL002_DUPLICATE_PIN = "NEUTRAL002_duplicate_pin"
NEUTRAL003_PIN_NOT_IN_PART = "NEUTRAL003_pin_not_in_part"
NEUTRAL004_NO_MODEL_ASSIGNMENT = "NEUTRAL004_no_model_assignment"
NEUTRAL005_ILLEGAL_NAME = "NEUTRAL005_illegal_name"
NEUTRAL006_NET_NOT_DECLARED = "NEUTRAL006_net_not_declared"
NEUTRAL007_MISSING_COMPONENT = "NEUTRAL007_missing_component"

NEUTRAL_CODES: tuple[str, ...] = (
    NEUTRAL001_DUPLICATE_REFDES,
    NEUTRAL002_DUPLICATE_PIN,
    NEUTRAL003_PIN_NOT_IN_PART,
    NEUTRAL004_NO_MODEL_ASSIGNMENT,
    NEUTRAL005_ILLEGAL_NAME,
    NEUTRAL006_NET_NOT_DECLARED,
    NEUTRAL007_MISSING_COMPONENT,
)

#: Legal refdes shape (INTERFACES §3).
REFDES_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-\[\]]*$")
_ILLEGAL_NET_CHARS = re.compile(r"[\s,]")

_PROJECT_KEYS = frozenset(
    {
        "schema_version",
        "supply_domains",
        "model_assignments",
        "loads",
        "timing",
        "abstractions",
        "configuration",
        "nets",
    }
)


@dataclass(frozen=True)
class ComponentRow:
    """One ``components.csv`` row."""

    refdes: str
    manufacturer: str = ""
    part_number: str = ""
    package: str = ""
    value: str = ""


@dataclass(frozen=True)
class ConnectionRow:
    """One ``connections.csv`` row: a physical pin tied to a net."""

    refdes: str
    physical_pin: str
    net_name: str


@dataclass
class NeutralProject:
    """The whole neutral model of a circuit."""

    components: list[ComponentRow] = field(default_factory=list)
    connections: list[ConnectionRow] = field(default_factory=list)
    supply_domains: dict[str, str] = field(default_factory=dict)
    model_assignments: dict[str, str] = field(default_factory=dict)
    loads: dict[str, float] = field(default_factory=dict)
    timing: dict[str, float] = field(default_factory=dict)
    abstractions: list[AbstractionBoundary] = field(default_factory=list)
    configuration: dict[str, str] = field(default_factory=dict)
    #: Nets that are neither supply domains nor ground but are declared
    #: deliberately (a genuinely single-ended net has to be named here).
    nets: list[str] = field(default_factory=list)
    #: Directory the project was read from (``<project>/circuit`` by default).
    root: Path | None = None
    #: Files that were expected but absent (reported, never silently ignored).
    missing_files: list[str] = field(default_factory=list)

    def connections_of(self, refdes: str) -> list[ConnectionRow]:
        return [row for row in self.connections if row.refdes == refdes]

    def declared_nets(self) -> set[str]:
        return {"0", *self.supply_domains, *self.nets}

    def as_json(self) -> dict[str, object]:
        return {
            "supply_domains": dict(sorted(self.supply_domains.items())),
            "model_assignments": dict(sorted(self.model_assignments.items())),
            "loads": dict(sorted(self.loads.items())),
            "timing": dict(sorted(self.timing.items())),
            "abstractions": [
                json.loads(boundary.model_dump_json()) for boundary in self.abstractions
            ],
            "configuration": dict(sorted(self.configuration.items())),
            "nets": list(self.nets),
        }


# --------------------------------------------------------------------------- #
# CSV


def read_components(path: str | Path) -> list[ComponentRow]:
    """Read ``components.csv``; unknown or missing columns raise ``ValueError``."""
    rows = _read_csv(path, COMPONENT_FIELDS)
    return [
        ComponentRow(
            refdes=row["refdes"].strip(),
            manufacturer=row["manufacturer"].strip(),
            part_number=row["part_number"].strip(),
            package=row["package"].strip(),
            value=row["value"].strip(),
        )
        for row in rows
    ]


def read_connections(path: str | Path) -> list[ConnectionRow]:
    """Read ``connections.csv``, preserving row order."""
    rows = _read_csv(path, CONNECTIONS_FIELDS)
    return [
        ConnectionRow(
            refdes=row["refdes"].strip(),
            physical_pin=row["physical_pin"].strip(),
            net_name=row["net_name"].strip(),
        )
        for row in rows
    ]


def write_components(path: str | Path, rows: Sequence[ComponentRow]) -> Path:
    """Write ``components.csv`` deterministically (LF endings, fixed header)."""
    return _write_csv(
        path,
        COMPONENT_FIELDS,
        [[row.refdes, row.manufacturer, row.part_number, row.package, row.value] for row in rows],
    )


def write_connections(path: str | Path, rows: Sequence[ConnectionRow]) -> Path:
    """Write ``connections.csv`` deterministically, preserving row order."""
    return _write_csv(
        path,
        CONNECTIONS_FIELDS,
        [[row.refdes, row.physical_pin, row.net_name] for row in rows],
    )


def _read_csv(path: str | Path, fields: Sequence[str]) -> list[dict[str, str]]:
    target = Path(path)
    with target.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = [str(name).strip() for name in (reader.fieldnames or [])]
        if header != list(fields):
            raise ValueError(
                f"{target.name}: expected columns {', '.join(fields)}, found "
                f"{', '.join(header) if header else 'none'}"
            )
        out: list[dict[str, str]] = []
        for row in reader:
            values = {name: (row.get(name) or "").strip() for name in fields}
            if not any(values.values()):
                continue
            out.append(values)
        return out


def _write_csv(path: str | Path, fields: Sequence[str], rows: Sequence[Sequence[str]]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(list(fields))
        writer.writerows(rows)
    return target


# --------------------------------------------------------------------------- #
# project.json


def read_project_json(path: str | Path) -> dict[str, object]:
    """Read and shape-check ``project.json``."""
    target = Path(path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{target.name}: expected a JSON object at the top level")
    unknown = sorted(set(payload) - _PROJECT_KEYS)
    if unknown:
        raise ValueError(f"{target.name}: unknown key(s) {', '.join(unknown)}")
    return payload


def write_project_json(path: str | Path, project: NeutralProject) -> Path:
    """Write ``project.json`` deterministically (sorted keys, LF endings)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(project.as_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target


def _apply_project_json(project: NeutralProject, payload: Mapping[str, object]) -> None:
    project.supply_domains = _str_map(payload.get("supply_domains"), "supply_domains")
    project.model_assignments = _str_map(payload.get("model_assignments"), "model_assignments")
    project.configuration = _str_map(payload.get("configuration"), "configuration")
    project.loads = _float_map(payload.get("loads"), "loads")
    project.timing = _float_map(payload.get("timing"), "timing")
    nets = payload.get("nets", [])
    if not isinstance(nets, list) or not all(isinstance(item, str) for item in nets):
        raise ValueError("project.json: 'nets' must be a list of strings")
    project.nets = [str(item) for item in nets]
    abstractions = payload.get("abstractions", [])
    if not isinstance(abstractions, list):
        raise ValueError("project.json: 'abstractions' must be a list of objects")
    parsed: list[AbstractionBoundary] = []
    for item in abstractions:
        if not isinstance(item, dict):
            raise ValueError(f"project.json: abstraction {item!r} is not an object")
        parsed.append(AbstractionBoundary.model_validate(item))
    project.abstractions = parsed


def _str_map(value: object, key: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"project.json: {key!r} must be an object")
    return {str(name): str(item) for name, item in value.items()}


def _float_map(value: object, key: str) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"project.json: {key!r} must be an object")
    out: dict[str, float] = {}
    for name, item in value.items():
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(f"project.json: {key}[{name!r}] must be a number, not {item!r}")
        out[str(name)] = float(item)
    return out


# --------------------------------------------------------------------------- #
# whole project


def read_neutral_project(directory: str | Path) -> NeutralProject:
    """Read a neutral project from ``directory``.

    ``<directory>/circuit/{components.csv,connections.csv,project.json}`` is the
    project layout (D3); passing the ``circuit`` directory itself also works.
    Absent files are recorded in :attr:`NeutralProject.missing_files`.
    """
    root = Path(directory)
    base = root
    if (root / "circuit").is_dir() and not (root / COMPONENTS_CSV).exists():
        base = root / "circuit"
    project = NeutralProject(root=base)
    components = base / COMPONENTS_CSV
    if components.is_file():
        project.components = read_components(components)
    else:
        project.missing_files.append(COMPONENTS_CSV)
    connections = base / CONNECTIONS_CSV
    if connections.is_file():
        project.connections = read_connections(connections)
    else:
        project.missing_files.append(CONNECTIONS_CSV)
    project_json = base / PROJECT_JSON
    if project_json.is_file():
        _apply_project_json(project, read_project_json(project_json))
    else:
        project.missing_files.append(PROJECT_JSON)
    return project


def write_neutral_project(directory: str | Path, project: NeutralProject) -> Path:
    """Write the three files of ``project`` into ``directory``."""
    base = Path(directory)
    base.mkdir(parents=True, exist_ok=True)
    write_components(base / COMPONENTS_CSV, project.components)
    write_connections(base / CONNECTIONS_CSV, project.connections)
    write_project_json(base / PROJECT_JSON, project)
    project.root = base
    project.missing_files = []
    return base


# --------------------------------------------------------------------------- #
# to Circuit


def to_circuit(
    project: NeutralProject,
    *,
    symbols: Mapping[str, Sequence[str]] | None = None,
) -> Circuit:
    """Turn the neutral model into a :class:`Circuit`.

    A component with a model assignment becomes an X instance; the others keep
    their primitive element letter.  Node order is the order of the component's
    ``connections.csv`` rows, recorded in :attr:`Circuit.pin_orders`, so
    ``NetMap.node_of(refdes, physical_pin)`` answers with the declared net name.

    When ``symbols`` supplies a part's pin order (its symbol pin names in
    ``SpiceOrder`` order) and the connection rows name those pins, the nodes are
    placed by name and a pin with no connection becomes ``NC_<position>`` — the
    same marker LTspice uses for a pin with no wire, so a dropped connection is
    visible instead of silently shifting the remaining pins.
    """
    circuit = Circuit(source_path=(project.root / CONNECTIONS_CSV if project.root else None))
    for component in project.components:
        if component.refdes in circuit.devices:
            continue  # duplicates are a NEUTRAL001 finding, not a second device
        rows = project.connections_of(component.refdes)
        order = tuple(symbols.get(component.refdes, ())) if symbols else ()
        nodes, pin_order = _place_nodes(rows, order)
        model = (project.model_assignments.get(component.refdes) or "").strip()
        if model:
            device = Device(
                refdes=component.refdes, kind="X", nodes=nodes, value=model, subckt=model
            )
        else:
            letter = component.refdes[:1].upper()
            device = Device(
                refdes=component.refdes,
                kind=letter if letter in ELEMENT_LETTERS else "X",
                nodes=nodes,
                value=component.value,
            )
        circuit.devices[component.refdes] = device
        if pin_order:
            circuit.pin_orders[component.refdes] = pin_order
    return circuit


def _place_nodes(
    rows: Sequence[ConnectionRow],
    order: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Node tuple and pin-name order for one component."""
    if order and any(row.physical_pin in order for row in rows):
        by_pin = {row.physical_pin: row.net_name for row in rows}
        nodes = tuple(
            by_pin.get(name) or f"NC_{position:02d}" for position, name in enumerate(order, start=1)
        )
        return nodes, tuple(order)
    return tuple(row.net_name for row in rows), tuple(row.physical_pin for row in rows)


# --------------------------------------------------------------------------- #
# validation


def validate_neutral(
    project: NeutralProject,
    *,
    pins: Mapping[str, Sequence[object]] | None = None,
) -> list[Finding]:
    """Validate the neutral model; one ``Finding`` per violation.

    ``pins`` maps a refdes to its known physical pins — plain names or
    :class:`~boardmodeler.domain.records.PinDefinition` objects (their
    ``physical_pin`` is used).  A refdes missing from ``pins`` is not checked
    against a pin list: an absent pin list is not evidence of an illegal pin.
    """
    pin_lists = {
        refdes: [_pin_label(pin) for pin in names] for refdes, names in (pins or {}).items()
    }
    findings: list[Finding] = []
    findings.extend(_check_components(project))
    findings.extend(_check_connections(project, pin_lists))
    findings.extend(_check_nets(project))
    return _dedupe(findings)


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """Drop repeated name findings (a bad refdes is one problem, not one per row)."""
    seen: set[tuple[str, str, str]] = set()
    out: list[Finding] = []
    for finding in findings:
        if finding.code == NEUTRAL005_ILLEGAL_NAME:
            key = (finding.code, finding.detail.get("kind", ""), finding.detail.get("name", ""))
            if key in seen:
                continue
            seen.add(key)
        out.append(finding)
    return out


def _check_components(project: NeutralProject) -> list[Finding]:
    findings: list[Finding] = []
    seen: dict[str, list[int]] = {}
    for index, row in enumerate(project.components, start=1):
        seen.setdefault(row.refdes, []).append(index)
    for refdes, rows in seen.items():
        if len(rows) > 1:
            findings.append(
                Finding(
                    code=NEUTRAL001_DUPLICATE_REFDES,
                    status=Status.FAIL,
                    refdes=refdes,
                    message=(
                        f"{refdes} is declared {len(rows)} times in components.csv; "
                        "the later definition would silently win"
                    ),
                    detail={
                        "rows": ", ".join(str(row) for row in rows),
                        "count": str(len(rows)),
                    },
                )
            )
    illegal: set[str] = set()
    for row in project.components:
        if not REFDES_RE.match(row.refdes):
            if row.refdes in illegal:
                continue
            illegal.add(row.refdes)
            findings.append(
                Finding(
                    code=NEUTRAL005_ILLEGAL_NAME,
                    status=Status.FAIL,
                    refdes=row.refdes,
                    message=(
                        f"refdes {row.refdes!r} is not a legal reference designator; expected "
                        "[A-Za-z][A-Za-z0-9_+-[]]*"
                    ),
                    detail={"name": row.refdes, "kind": "refdes"},
                )
            )
        model = (project.model_assignments.get(row.refdes) or "").strip()
        if not model and not _is_primitive_row(row):
            findings.append(
                Finding(
                    code=NEUTRAL004_NO_MODEL_ASSIGNMENT,
                    status=Status.FAIL,
                    refdes=row.refdes,
                    message=(
                        f"{row.refdes} has no model assignment, so it cannot be simulated or "
                        "checked against a model"
                    ),
                    detail={
                        "model_assignment": model,
                        "part_number": row.part_number,
                        "package": row.package,
                    },
                )
            )
    return findings


def _is_primitive_row(row: ComponentRow) -> bool:
    """A passive/source row needs a value, not a model assignment."""
    letter = row.refdes[:1].upper()
    return letter in ELEMENT_LETTERS and letter != "X" and bool(row.value.strip())


def _check_connections(
    project: NeutralProject, pin_lists: Mapping[str, list[str]]
) -> list[Finding]:
    findings: list[Finding] = []
    known_refdes = {row.refdes for row in project.components}
    reported_illegal: set[str] = set()
    seen_pins: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for index, row in enumerate(project.connections, start=1):
        seen_pins.setdefault((row.refdes, row.physical_pin), []).append((index, row.net_name))
        if row.refdes not in known_refdes:
            findings.append(
                Finding(
                    code=NEUTRAL007_MISSING_COMPONENT,
                    status=Status.FAIL,
                    refdes=row.refdes,
                    nets=[row.net_name],
                    message=(
                        f"connections.csv row {index} references {row.refdes}, which is not "
                        "declared in components.csv"
                    ),
                    detail={"row": str(index), "physical_pin": row.physical_pin},
                )
            )
        if not REFDES_RE.match(row.refdes) and row.refdes not in reported_illegal:
            reported_illegal.add(row.refdes)
            findings.append(
                Finding(
                    code=NEUTRAL005_ILLEGAL_NAME,
                    status=Status.FAIL,
                    refdes=row.refdes,
                    message=(
                        f"connections.csv uses the illegal refdes {row.refdes!r}; expected "
                        "[A-Za-z][A-Za-z0-9_+-[]]*"
                    ),
                    detail={"name": row.refdes, "kind": "refdes"},
                )
            )
        if _ILLEGAL_NET_CHARS.search(row.net_name) or not row.net_name:
            findings.append(
                Finding(
                    code=NEUTRAL005_ILLEGAL_NAME,
                    status=Status.FAIL,
                    refdes=row.refdes,
                    nets=[row.net_name],
                    message=(
                        f"net name {row.net_name!r} on {row.refdes}.{row.physical_pin} is "
                        "illegal: a net name may not be empty or contain whitespace or ','"
                    ),
                    detail={"name": row.net_name, "kind": "net", "row": str(index)},
                )
            )
        names = pin_lists.get(row.refdes)
        if names is not None and names and row.physical_pin not in names:
            findings.append(
                Finding(
                    code=NEUTRAL003_PIN_NOT_IN_PART,
                    status=Status.FAIL,
                    refdes=row.refdes,
                    nets=[row.net_name],
                    message=(
                        f"{row.refdes}.{row.physical_pin} is not a pin of the part; known pins: "
                        f"{', '.join(names)}"
                    ),
                    detail={
                        "physical_pin": row.physical_pin,
                        "known_pins": ", ".join(names),
                        "row": str(index),
                    },
                )
            )
    for (refdes, pin), entries in seen_pins.items():
        if len(entries) < 2:
            continue
        nets = [net for _row, net in entries]
        findings.append(
            Finding(
                code=NEUTRAL002_DUPLICATE_PIN,
                status=Status.FAIL,
                refdes=refdes,
                nets=list(dict.fromkeys(nets)),
                message=(
                    f"{refdes}.{pin} is connected to {len(entries)} nets "
                    f"({', '.join(dict.fromkeys(nets))}); all but one connection would be dropped"
                ),
                detail={
                    "physical_pin": pin,
                    "rows": ", ".join(str(row) for row, _net in entries),
                    "nets": ", ".join(dict.fromkeys(nets)),
                },
            )
        )
    return findings


def _check_nets(project: NeutralProject) -> list[Finding]:
    counts: dict[str, int] = {}
    for row in project.connections:
        counts[row.net_name] = counts.get(row.net_name, 0) + 1
    declared = project.declared_nets()
    findings: list[Finding] = []
    for net, count in counts.items():
        if count != 1 or net in declared:
            continue
        findings.append(
            Finding(
                code=NEUTRAL006_NET_NOT_DECLARED,
                status=Status.FAIL,
                refdes=None,
                nets=[net],
                message=(
                    f"net {net!r} is attached to a single pin and is not declared; a net used "
                    "once is usually a typo (declare it in project.json 'nets' if intended)"
                ),
                detail={"net": net, "connection_count": str(count), "declared": "false"},
            )
        )
    return findings


def _pin_label(pin: object) -> str:
    physical = getattr(pin, "physical_pin", None)
    if isinstance(physical, str) and physical:
        return physical
    return str(pin)
