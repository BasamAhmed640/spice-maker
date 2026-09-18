"""Neutral CSV/JSON project model: round-trip, findings per code, and the
connectivity that makes the assertion ops work against a CSV project.

The last test is the important one: it evaluates a real ``net_equals``
expression against a ``NetMap`` built by ``to_circuit`` — if the pin order or the
node mapping were wrong, the connectivity ops would answer for the wrong net.
"""

from __future__ import annotations

import json

import pytest

from boardmodeler.domain.enums import Status
from boardmodeler.domain.expressions import NetEqualsOp
from boardmodeler.domain.records import AbstractionBoundary, PinDefinition
from boardmodeler.schematic.netlist import build_netmap
from boardmodeler.schematic.neutral import (
    NEUTRAL001_DUPLICATE_REFDES,
    NEUTRAL002_DUPLICATE_PIN,
    NEUTRAL003_PIN_NOT_IN_PART,
    NEUTRAL004_NO_MODEL_ASSIGNMENT,
    NEUTRAL005_ILLEGAL_NAME,
    NEUTRAL006_NET_NOT_DECLARED,
    NEUTRAL007_MISSING_COMPONENT,
    ComponentRow,
    ConnectionRow,
    NeutralProject,
    read_components,
    read_connections,
    read_neutral_project,
    to_circuit,
    validate_neutral,
    write_components,
    write_connections,
    write_neutral_project,
)
from boardmodeler.verification.assertions import EvalContext, evaluate

COMPONENTS = [
    ComponentRow("U1", "Acme", "AC-1000", "QFN-24", ""),
    ComponentRow("R1", "Yageo", "RC0402", "0402", "10k"),
    ComponentRow("V1", "", "", "", "12"),
]
#: Physical pins as printed on the part; row order is the symbol's SpiceOrder order.
CONNECTIONS = [
    ConnectionRow("U1", "VIN", "12V"),
    ConnectionRow("U1", "GND", "0"),
    ConnectionRow("U1", "EN", "EN_12V"),
    ConnectionRow("U1", "PG", "PG_3V3"),
    ConnectionRow("U1", "VDD", "3V3"),
    ConnectionRow("R1", "1", "PG_3V3"),
    ConnectionRow("R1", "2", "3V3"),
    ConnectionRow("V1", "+", "12V"),
    ConnectionRow("V1", "-", "0"),
]
SYMBOLS = {"U1": ("VIN", "GND", "EN", "PG", "VDD")}


def _project(**overrides: object) -> NeutralProject:
    project = NeutralProject(
        components=list(COMPONENTS),
        connections=list(CONNECTIONS),
        supply_domains={"12V": "12V", "3V3": "3V3"},
        model_assignments={"U1": "AC1000_MODEL"},
        nets=["EN_12V", "PG_3V3"],
    )
    for key, value in overrides.items():
        setattr(project, key, value)
    return project


def _factory_pins() -> dict[str, list[PinDefinition]]:
    def pin(physical: str, name: str, direction: str, domain: str | None) -> PinDefinition:
        return PinDefinition(
            part_id="AC-1000",
            physical_pin=physical,
            name=name,
            function=name,
            polarity="not_applicable",
            direction=direction,
            supply_domain=domain,
            output_topology="unknown",
            connection_requirement="required",
            mapped_symbol_pin=name,
        )

    return {
        "U1": [
            pin("VIN", "VIN", "power", "12V"),
            pin("GND", "GND", "ground", None),
            pin("EN", "EN", "input", None),
            pin("PG", "PG", "output", "3V3"),
            pin("VDD", "VDD", "power", "3V3"),
        ]
    }


# --------------------------------------------------------------------------- #
# CSV / JSON round-trip


def test_components_csv_roundtrip(tmp_path) -> None:
    path = write_components(tmp_path / "components.csv", COMPONENTS)
    assert path.read_text(encoding="utf-8").splitlines()[0] == (
        "refdes,manufacturer,part_number,package,value"
    )
    assert read_components(path) == COMPONENTS


def test_connections_csv_preserves_row_order(tmp_path) -> None:
    path = write_connections(tmp_path / "connections.csv", CONNECTIONS)
    assert read_connections(path) == CONNECTIONS


def test_csv_rejects_a_wrong_header(tmp_path) -> None:
    path = tmp_path / "components.csv"
    path.write_text("refdes,part_number\nU1,AC-1000\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected columns"):
        read_components(path)


def test_project_json_roundtrip_includes_abstractions(tmp_path) -> None:
    project = _project(
        abstractions=[
            AbstractionBoundary(
                refdes="U1",
                omitted_pins=["NC1", "NC2"],
                preserved_connectivity=False,
                reason="lanes not modeled",
            )
        ],
        loads={"V1": 0.5},
        timing={"PG_DELAY": 0.001},
        configuration={"MODE": "forced_pwm"},
    )
    write_neutral_project(tmp_path, project)
    reloaded = read_neutral_project(tmp_path)
    assert reloaded.components == project.components
    assert reloaded.connections == project.connections
    assert reloaded.supply_domains == project.supply_domains
    assert reloaded.model_assignments == project.model_assignments
    assert reloaded.loads == {"V1": 0.5}
    assert reloaded.timing == {"PG_DELAY": 0.001}
    assert reloaded.configuration == {"MODE": "forced_pwm"}
    assert reloaded.nets == ["EN_12V", "PG_3V3"]
    assert reloaded.abstractions == project.abstractions
    assert reloaded.missing_files == []


def test_project_json_is_written_deterministically(tmp_path) -> None:
    project = _project()
    write_neutral_project(tmp_path / "a", project)
    write_neutral_project(tmp_path / "b", project)
    assert (tmp_path / "a" / "project.json").read_bytes() == (
        tmp_path / "b" / "project.json"
    ).read_bytes()


def test_unknown_project_json_key_is_rejected(tmp_path) -> None:
    (tmp_path / "project.json").write_text(
        json.dumps({"supply_domains": {}, "surprise": 1}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="unknown key"):
        read_neutral_project(tmp_path)


def test_read_neutral_project_uses_the_circuit_subdirectory(tmp_path) -> None:
    write_neutral_project(tmp_path / "circuit", _project())
    project = read_neutral_project(tmp_path)
    assert project.root == tmp_path / "circuit"
    assert len(project.components) == 3


def test_missing_files_are_recorded_not_ignored(tmp_path) -> None:
    project = read_neutral_project(tmp_path)
    assert set(project.missing_files) == {"components.csv", "connections.csv", "project.json"}


# --------------------------------------------------------------------------- #
# validation findings


def test_clean_project_has_no_findings() -> None:
    assert validate_neutral(_project(), pins=_factory_pins()) == []


def test_neutral001_duplicate_refdes() -> None:
    project = _project(components=[*COMPONENTS, ComponentRow("U1", "Acme", "AC-2000", "QFN", "")])
    findings = [f for f in validate_neutral(project) if f.code == NEUTRAL001_DUPLICATE_REFDES]
    assert len(findings) == 1
    finding = findings[0]
    assert finding.status is Status.FAIL
    assert finding.refdes == "U1"
    assert finding.detail["rows"] == "1, 4"
    assert finding.detail["count"] == "2"


def test_neutral002_duplicate_pin() -> None:
    project = _project(connections=[*CONNECTIONS, ConnectionRow("R1", "1", "3V3")])
    findings = validate_neutral(project)
    duplicates = [f for f in findings if f.code == NEUTRAL002_DUPLICATE_PIN]
    assert len(duplicates) == 1
    assert duplicates[0].refdes == "R1"
    assert duplicates[0].nets == ["PG_3V3", "3V3"]
    assert duplicates[0].detail["physical_pin"] == "1"
    assert duplicates[0].status is Status.FAIL


def test_neutral003_pin_not_in_part() -> None:
    project = _project(connections=[*CONNECTIONS, ConnectionRow("U1", "99", "3V3")])
    findings = validate_neutral(project, pins=_factory_pins())
    mismatches = [f for f in findings if f.code == NEUTRAL003_PIN_NOT_IN_PART]
    assert len(mismatches) == 1
    assert mismatches[0].refdes == "U1"
    assert mismatches[0].detail["physical_pin"] == "99"
    assert "VIN" in mismatches[0].detail["known_pins"]


def test_neutral003_is_not_raised_without_a_pin_list() -> None:
    project = _project(connections=[*CONNECTIONS, ConnectionRow("U1", "99", "3V3")])
    assert not [f for f in validate_neutral(project) if f.code == NEUTRAL003_PIN_NOT_IN_PART]


def test_neutral004_component_without_model() -> None:
    project = _project(
        components=[*COMPONENTS, ComponentRow("U2", "Acme", "AC-2000", "QFN-24", "")]
    )
    findings = validate_neutral(project)
    missing = [f for f in findings if f.code == NEUTRAL004_NO_MODEL_ASSIGNMENT]
    assert [f.refdes for f in missing] == ["U2"]
    assert missing[0].detail["model_assignment"] == ""
    assert missing[0].detail["part_number"] == "AC-2000"
    # A resistor with a value needs no model assignment.
    assert "R1" not in [f.refdes for f in missing]


def test_neutral005_illegal_refdes_and_net_name() -> None:
    project = _project(
        components=[*COMPONENTS, ComponentRow("1U", "Acme", "AC", "QFN", "")],
        connections=[*CONNECTIONS, ConnectionRow("1U", "A", "bad net")],
    )
    findings = validate_neutral(project)
    illegal = [f for f in findings if f.code == NEUTRAL005_ILLEGAL_NAME]
    kinds = {(f.detail["kind"], f.detail["name"]) for f in illegal}
    assert ("refdes", "1U") in kinds
    assert ("net", "bad net") in kinds
    # The illegal refdes is reported once, not once per row.
    assert len([f for f in illegal if f.detail["kind"] == "refdes"]) == 1


def test_neutral006_net_used_once_and_undeclared() -> None:
    project = _project(connections=[*CONNECTIONS, ConnectionRow("U1", "RT", "RT_TYPO")])
    findings = validate_neutral(project)
    nets = [f for f in findings if f.code == NEUTRAL006_NET_NOT_DECLARED]
    assert [f.nets for f in nets] == [["RT_TYPO"]]
    assert nets[0].detail["connection_count"] == "1"
    # A declared single-use net is fine.
    project.nets.append("RT_TYPO")
    assert not [f for f in validate_neutral(project) if f.code == NEUTRAL006_NET_NOT_DECLARED]


def test_neutral007_connection_to_a_missing_component() -> None:
    project = _project(connections=[*CONNECTIONS, ConnectionRow("U9", "A", "3V3")])
    findings = validate_neutral(project)
    missing = [f for f in findings if f.code == NEUTRAL007_MISSING_COMPONENT]
    assert [f.refdes for f in missing] == ["U9"]
    assert missing[0].nets == ["3V3"]
    assert missing[0].status is Status.FAIL


# --------------------------------------------------------------------------- #
# to_circuit / connectivity


def test_to_circuit_answers_pin_to_net_by_physical_pin_name() -> None:
    project = _project()
    circuit = to_circuit(project, symbols=SYMBOLS)
    netmap = build_netmap(circuit, SYMBOLS)
    assert netmap.has_refdes("U1")
    assert netmap.node_of("U1", "PG") == "PG_3V3"
    assert netmap.node_of("U1", "VIN") == "12V"
    assert netmap.node_of("U1", "GND") == "0"
    # Positional keys work too: the symbol order defines the node order.
    assert netmap.node_of("U1", "4") == "PG_3V3"
    assert netmap.node_of("R1", "1") == "PG_3V3"
    assert netmap.node_of("U1", "NOPE") is None
    assert ("R1", "1") in netmap.pins_on("PG_3V3")


def test_to_circuit_is_usable_by_the_connectivity_assertion_ops() -> None:
    netmap = build_netmap(to_circuit(_project(), symbols=SYMBOLS), SYMBOLS)
    context = EvalContext(connectivity=netmap, supply_domains={"PG_3V3": "3V3"})

    good = evaluate(NetEqualsOp(op="net_equals", refdes="U1", pin="PG", net="PG_3V3"), context)
    assert good.status is Status.PASS
    assert good.measured["observed_net"] == "PG_3V3"

    bad = evaluate(NetEqualsOp(op="net_equals", refdes="U1", pin="PG", net="3V3"), context)
    assert bad.status is Status.FAIL
    assert bad.measured["observed_net"] == "PG_3V3"


def test_connection_dropped_from_csv_shows_up_as_an_open_pin() -> None:
    project = _project(connections=[c for c in CONNECTIONS if c.physical_pin != "PG"])
    netmap = build_netmap(to_circuit(project, symbols=SYMBOLS), SYMBOLS)
    node = netmap.node_of("U1", "PG")
    assert node is not None
    assert netmap.is_connected_node(node) is False
    # The pins after the dropped one keep their own nets.
    assert netmap.node_of("U1", "VDD") == "3V3"


def test_to_circuit_falls_back_to_row_order_without_symbol_names() -> None:
    """Numeric physical pins still map, in connection-row order."""
    numeric = _project(
        connections=[
            ConnectionRow("U1", "1", "12V"),
            ConnectionRow("U1", "2", "0"),
            ConnectionRow("U1", "3", "EN_12V"),
            ConnectionRow("U1", "4", "PG_3V3"),
            ConnectionRow("U1", "5", "3V3"),
        ]
    )
    netmap = build_netmap(to_circuit(numeric, symbols=SYMBOLS), SYMBOLS)
    assert netmap.node_of("U1", "PG") == "PG_3V3"
    assert netmap.node_of("U1", "VIN") == "12V"
