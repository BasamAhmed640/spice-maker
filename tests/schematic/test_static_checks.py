"""Static checks SC001-SC010: one minimal fixture per code.

The supply-domain tests carry the two rules that matter most: every power pin is
evaluated individually (a disconnected pin is reported even when a sibling pin on
the same rail is fine), and two domains shorted onto one net produce a finding
naming both domains and the shared net.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from boardmodeler.domain.enums import Status
from boardmodeler.domain.records import (
    AbstractionBoundary,
    Finding,
    PinDefinition,
)
from boardmodeler.models.library import ModelRecord
from boardmodeler.schematic.netlist import build_netmap, parse_netlist
from boardmodeler.schematic.neutral import (
    ComponentRow,
    ConnectionRow,
    NeutralProject,
)
from boardmodeler.schematic.static_check import CHECK_CODES, run_static_checks

SUB_DECK = """* subcircuit instance
.subckt PART a b
R1 a b 1k
.ends PART
XU5 n1 n2 PART
"""


def checks(circuit, netmap=None, **kwargs) -> list[Finding]:
    return run_static_checks(
        circuit, netmap if netmap is not None else build_netmap(circuit), **kwargs
    )


def of_code(findings: list[Finding], code: str) -> list[Finding]:
    return [finding for finding in findings if finding.code == code]


def pin(
    physical: str,
    name: str,
    direction: str,
    domain: str | None,
    *,
    mapped: str | None = None,
) -> PinDefinition:
    return PinDefinition(
        part_id="PART",
        physical_pin=physical,
        name=name,
        function=name,
        polarity="not_applicable",
        direction=direction,
        supply_domain=domain,
        output_topology="unknown",
        connection_requirement="required",
        mapped_symbol_pin=mapped,
    )


# --------------------------------------------------------------------------- #
# SC001


def test_sc001_passes_on_a_well_formed_circuit() -> None:
    findings = of_code(
        checks(parse_netlist("* ok\nV1 in 0 1\nR1 in 0 1k\n.tran 1m\n")), CHECK_CODES[0]
    )
    assert len(findings) == 1
    assert findings[0].status is Status.PASS
    assert findings[0].detail["devices"] == "2"


def test_sc001_flags_an_instance_without_a_subcircuit() -> None:
    circuit = parse_netlist("* ok\nR1 a b 1k\n")
    circuit.devices["U1"] = type(circuit.devices["R1"])(refdes="U1", kind="X", nodes=("a", "b"))
    findings = of_code(checks(circuit), CHECK_CODES[0])
    assert [f.status for f in findings] == [Status.FAIL]
    assert findings[0].refdes == "U1"
    assert findings[0].detail["reason"] == "x_instance_without_subckt"


def test_sc001_flags_a_wrong_node_count() -> None:
    circuit = parse_netlist("* ok\nR1 a b 1k\n")
    device = circuit.devices["R1"]
    circuit.devices["R1"] = type(device)(refdes="R1", kind="R", nodes=("a", "b", "c"), value="1k")
    findings = of_code(checks(circuit), CHECK_CODES[0])
    assert findings[0].detail["reason"] == "wrong_node_count"
    assert findings[0].refdes == "R1"


def test_sc001_flags_an_unbalanced_directive() -> None:
    circuit = parse_netlist("* ok\nR1 a b 1k\n.tran 1m\n")
    circuit.directives.append(".param X=(1")
    findings = of_code(checks(circuit), CHECK_CODES[0])
    assert findings[0].detail["reason"] == "unbalanced_parentheses"


# --------------------------------------------------------------------------- #
# SC002


def test_sc002_passes_on_legal_names_and_units() -> None:
    findings = of_code(checks(parse_netlist("* ok\nR1 in+ 0 1k\nC1 0 out 10n\n")), CHECK_CODES[1])
    assert findings[0].status is Status.PASS
    assert findings[0].detail["values"] == "2"


def test_sc002_flags_an_illegal_node_name() -> None:
    findings = of_code(checks(parse_netlist("* ok\nR1 bad,node 0 1k\n")), CHECK_CODES[1])
    assert findings[0].detail["reason"] == "illegal_node"
    assert findings[0].nets == ["bad,node"]


def test_sc002_flags_an_empty_value() -> None:
    findings = of_code(checks(parse_netlist("* ok\nC1 a b\n")), CHECK_CODES[1])
    assert findings[0].detail["reason"] == "empty_value"
    assert findings[0].refdes == "C1"


def test_sc002_flags_the_milli_not_mega_trap() -> None:
    findings = of_code(checks(parse_netlist("* ok\nR1 a b 1M\n")), CHECK_CODES[1])
    assert findings[0].detail["reason"] == "milli_not_mega"
    assert findings[0].detail["interpreted_multiplier"] == "1e-3"
    # 'meg' is the unambiguous form and passes.
    assert not of_code(checks(parse_netlist("* ok\nR1 a b 1meg\n")), CHECK_CODES[1])[0].detail.get(
        "reason"
    )


def test_sc002_flags_a_value_with_whitespace() -> None:
    circuit = parse_netlist("* ok\nR1 a b 1k\n")
    device = circuit.devices["R1"]
    circuit.devices["R1"] = type(device)(refdes="R1", kind="R", nodes=("a", "b"), value="1 k")
    findings = of_code(checks(circuit), CHECK_CODES[1])
    assert findings[0].detail["reason"] == "value_with_whitespace"


# --------------------------------------------------------------------------- #
# SC003


def test_sc003_passes_when_the_subcircuit_is_defined_locally() -> None:
    findings = of_code(checks(parse_netlist(SUB_DECK)), CHECK_CODES[2])
    assert findings[0].status is Status.PASS
    assert findings[0].detail["instances"] == "1"


def test_sc003_flags_an_undefined_subcircuit() -> None:
    findings = of_code(checks(parse_netlist("* missing\nXU1 a b NOSUCHPART\n")), CHECK_CODES[2])
    assert findings[0].status is Status.FAIL
    # The finding names the component (U1), the detail keeps the netlist token.
    assert findings[0].refdes == "U1"
    assert findings[0].detail["device"] == "XU1"
    assert findings[0].detail["subckt"] == "NOSUCHPART"
    assert findings[0].detail["reason"] == "subckt_not_found"


def test_sc003_resolves_a_definition_from_an_include_on_disk(tmp_path: Path) -> None:
    (tmp_path / "models.lib").write_text(".subckt PART a b\nR1 a b 1k\n.ends\n", encoding="utf-8")
    deck = tmp_path / "deck.cir"
    deck.write_text("* include\n.include models.lib\nXU5 n1 n2 PART\n", encoding="utf-8")
    from boardmodeler.schematic.netlist import parse_netlist_file

    findings = of_code(checks(parse_netlist_file(deck)), CHECK_CODES[2])
    assert findings[0].status is Status.PASS


def test_sc003_flags_an_include_that_does_not_exist(tmp_path: Path) -> None:
    deck = tmp_path / "deck.cir"
    deck.write_text("* include\n.include missing.lib\nXU5 n1 n2 PART\n", encoding="utf-8")
    from boardmodeler.schematic.netlist import parse_netlist_file

    findings = of_code(checks(parse_netlist_file(deck)), CHECK_CODES[2])
    reasons = {f.detail["reason"] for f in findings}
    assert reasons == {"include_not_found", "subckt_not_found"}
    assert all(f.status is Status.FAIL for f in findings)


def test_sc003_flags_an_instance_inside_a_subcircuit_body() -> None:
    findings = of_code(
        checks(
            parse_netlist("* body\n.subckt TOP a b\nXINNER a b MISSING\n.ends TOP\nXU1 a b TOP\n")
        ),
        CHECK_CODES[2],
    )
    assert [f.detail["subckt"] for f in findings] == ["MISSING"]
    assert findings[0].refdes == "INNER"
    assert findings[0].detail["device"] == "XINNER"
    assert findings[0].detail["scope"] == "TOP"


def test_sc003_accepts_a_subcircuit_declared_by_a_model_record() -> None:
    records = {
        "PART_MODEL": ModelRecord(
            model_id="PART_MODEL",
            kind="generated",
            path="models/part.lib",
            sha256="0" * 64,
            size=1,
            subckts=["PART"],
        )
    }
    findings = of_code(
        checks(parse_netlist("* model\nXU5 a b PART\n"), model_records=records), CHECK_CODES[2]
    )
    assert findings[0].status is Status.PASS


# --------------------------------------------------------------------------- #
# SC004


def test_sc004_requires_an_identified_part() -> None:
    project = NeutralProject(
        components=[
            ComponentRow("U1", "Acme", "AC-1000", "QFN-24", ""),
            ComponentRow("U2", "Acme", "", "QFN-24", ""),
            ComponentRow("U3", "Acme", "TBD", "QFN-24", ""),
        ]
    )
    findings = of_code(checks(parse_netlist("* ok\nR1 a b 1k\n"), project=project), CHECK_CODES[3])
    assert [f.refdes for f in findings] == ["U2", "U3"]
    assert {f.detail["reason"] for f in findings} == {"unidentified_part"}
    assert findings[0].detail["part_number"] == ""


def test_sc004_is_not_applicable_without_a_project() -> None:
    findings = of_code(checks(parse_netlist("* ok\nR1 a b 1k\n")), CHECK_CODES[3])
    assert findings[0].status is Status.NOT_APPLICABLE


# --------------------------------------------------------------------------- #
# SC005


def _pinmap_case() -> tuple:
    circuit = parse_netlist(SUB_DECK)
    netmap = build_netmap(circuit, {"U5": ("A", "B")})
    pins = {
        "U5": [
            pin("1", "A", "input", None, mapped="A"),
            pin("2", "B", "output", None, mapped="B"),
        ]
    }
    symbol_pins = {"U5": ("A", "B")}
    return circuit, netmap, pins, symbol_pins


def test_sc005_passes_on_a_consistent_pinmap() -> None:
    circuit, netmap, pins, symbol_pins = _pinmap_case()
    findings = of_code(checks(circuit, netmap, pins=pins, symbol_pins=symbol_pins), CHECK_CODES[4])
    assert findings[0].status is Status.PASS
    assert findings[0].detail["mapped_pins"] == "2"


def test_sc005_flags_a_physical_pin_mapped_to_an_unknown_symbol_pin() -> None:
    circuit, netmap, pins, symbol_pins = _pinmap_case()
    pins["U5"][0] = pin("1", "A", "input", None, mapped="PG")
    findings = of_code(checks(circuit, netmap, pins=pins, symbol_pins=symbol_pins), CHECK_CODES[4])
    reasons = {f.detail["reason"] for f in findings}
    assert "symbol_pin_unknown" in reasons
    assert all(f.status is Status.FAIL for f in findings)


def test_sc005_flags_a_symbol_arity_mismatch() -> None:
    circuit, netmap, pins, symbol_pins = _pinmap_case()
    symbol_pins["U5"] = ("A", "B", "C")
    findings = of_code(checks(circuit, netmap, pins=pins, symbol_pins=symbol_pins), CHECK_CODES[4])
    assert "arity_mismatch" in {f.detail["reason"] for f in findings}


def test_sc005_flags_two_physical_pins_sharing_a_symbol_pin() -> None:
    circuit, netmap, pins, symbol_pins = _pinmap_case()
    pins["U5"].append(pin("3", "A2", "input", None, mapped="A"))
    findings = of_code(checks(circuit, netmap, pins=pins, symbol_pins=symbol_pins), CHECK_CODES[4])
    assert "duplicate_symbol_pin" in {f.detail["reason"] for f in findings}


def test_sc005_allows_an_unmapped_symbol_pin_declared_as_omitted() -> None:
    circuit = parse_netlist(SUB_DECK)
    netmap = build_netmap(circuit, {"U5": ("A", "B")})
    pins = {"U5": [pin("1", "A", "input", None, mapped="A")]}
    without = of_code(
        checks(circuit, netmap, pins=pins, symbol_pins={"U5": ("A", "B")}), CHECK_CODES[4]
    )
    assert "unmapped_symbol_pin" in {f.detail["reason"] for f in without}
    project = NeutralProject(
        abstractions=[
            AbstractionBoundary(
                refdes="U5",
                omitted_pins=["B"],
                preserved_connectivity=True,
                reason="B is not modeled",
            )
        ]
    )
    with_boundary = of_code(
        checks(circuit, netmap, project=project, pins=pins, symbol_pins={"U5": ("A", "B")}),
        CHECK_CODES[4],
    )
    assert with_boundary[0].status is Status.PASS


# --------------------------------------------------------------------------- #
# SC006


ASYS = {
    "U5": """Version 4
SymbolType CELL
SYMATTR Prefix X
SYMATTR Value PART
SYMATTR SpiceModel part.lib
SYMATTR Value2 PART
PIN -64 0 LEFT 8
PINATTR PinName a
PINATTR SpiceOrder 1
PIN 64 0 RIGHT 8
PINATTR PinName b
PINATTR SpiceOrder 2
"""
}


def test_sc006_passes_when_prefix_and_model_are_right(tmp_path: Path) -> None:
    (tmp_path / "part.lib").write_text(".subckt PART a b\nR1 a b 1k\n.ends\n", encoding="utf-8")
    (tmp_path / "part.asy").write_text(ASYS["U5"], encoding="utf-8")
    findings = of_code(
        checks(
            parse_netlist(SUB_DECK),
            symbol_texts={"U5": tmp_path / "part.asy"},
            project_root=tmp_path,
        ),
        CHECK_CODES[5],
    )
    assert findings[0].status is Status.PASS
    assert findings[0].detail["symbols"] == "1"


def test_sc006_flags_a_non_x_prefix(tmp_path: Path) -> None:
    (tmp_path / "part.lib").write_text(".subckt PART a b\nR1 a b 1k\n.ends\n", encoding="utf-8")
    asy = tmp_path / "part.asy"
    asy.write_text(ASYS["U5"].replace("SYMATTR Prefix X", "SYMATTR Prefix R"), encoding="utf-8")
    findings = of_code(
        checks(parse_netlist(SUB_DECK), symbol_texts={"U5": asy}, project_root=tmp_path),
        CHECK_CODES[5],
    )
    assert findings[0].detail["reason"] == "prefix_not_x"
    assert findings[0].detail["prefix"] == "R"


def test_sc006_flags_a_missing_model_file(tmp_path: Path) -> None:
    asy = tmp_path / "part.asy"
    asy.write_text(ASYS["U5"], encoding="utf-8")
    findings = of_code(
        checks(parse_netlist(SUB_DECK), symbol_texts={"U5": asy}, project_root=tmp_path),
        CHECK_CODES[5],
    )
    assert findings[0].detail["reason"] == "spicemodel_not_found"
    assert findings[0].detail["spicemodel"] == "part.lib"


def test_sc006_flags_a_missing_spicemodel_attribute(tmp_path: Path) -> None:
    asy = tmp_path / "part.asy"
    asy.write_text(ASYS["U5"].replace("SYMATTR SpiceModel part.lib\n", ""), encoding="utf-8")
    findings = of_code(
        checks(parse_netlist(SUB_DECK), symbol_texts={"U5": asy}, project_root=tmp_path),
        CHECK_CODES[5],
    )
    assert findings[0].detail["reason"] == "missing_spicemodel"


# --------------------------------------------------------------------------- #
# SC007


def test_sc007_flags_a_pin_connected_to_two_nets() -> None:
    project = NeutralProject(
        components=[ComponentRow("U1", "Acme", "AC-1000", "QFN", "")],
        connections=[
            ConnectionRow("U1", "EN", "EN_3V3"),
            ConnectionRow("U1", "EN", "EN_1V8"),
        ],
    )
    findings = of_code(checks(parse_netlist("* ok\nR1 a b 1k\n"), project=project), CHECK_CODES[6])
    assert findings[0].status is Status.FAIL
    assert findings[0].refdes == "U1"
    assert findings[0].nets == ["EN_3V3", "EN_1V8"]
    assert findings[0].detail["physical_pin"] == "EN"


def test_sc007_passes_for_one_net_per_pin() -> None:
    project = NeutralProject(
        components=[ComponentRow("U1", "Acme", "AC-1000", "QFN", "")],
        connections=[ConnectionRow("U1", "EN", "EN_3V3")],
    )
    findings = of_code(checks(parse_netlist("* ok\nR1 a b 1k\n"), project=project), CHECK_CODES[6])
    assert findings[0].status is Status.PASS


# --------------------------------------------------------------------------- #
# SC008


def test_sc008_passes_for_targets_inside_the_root(tmp_path: Path) -> None:
    (tmp_path / "models.lib").write_text(".subckt PART a b\nR1 a b 1k\n.ends\n", encoding="utf-8")
    deck = tmp_path / "deck.cir"
    deck.write_text("* include\n.include models.lib\nXU5 n1 n2 PART\n", encoding="utf-8")
    from boardmodeler.schematic.netlist import parse_netlist_file

    findings = of_code(checks(parse_netlist_file(deck)), CHECK_CODES[7])
    assert findings[0].status is Status.PASS
    assert findings[0].detail["includes"] == "1"


def test_sc008_flags_an_include_outside_the_root(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "models.lib").write_text(".subckt PART a b\nR1 a b 1k\n.ends\n", encoding="utf-8")
    root = tmp_path / "project"
    root.mkdir()
    deck = root / "deck.cir"
    deck.write_text(
        "* include\n.include ../elsewhere/models.lib\nXU5 n1 n2 PART\n", encoding="utf-8"
    )
    from boardmodeler.schematic.netlist import parse_netlist_file

    findings = of_code(checks(parse_netlist_file(deck)), CHECK_CODES[7])
    assert findings[0].detail["reason"] == "include_outside_root"
    assert findings[0].detail["include"] == "../elsewhere/models.lib"


def test_sc008_flags_an_absolute_include_outside_the_root(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.lib"
    elsewhere.write_text(".subckt PART a b\nR1 a b 1k\n.ends\n", encoding="utf-8")
    root = tmp_path / "project"
    root.mkdir()
    circuit = parse_netlist(f"* abs\n.lib {elsewhere}\nXU5 n1 n2 PART\n")
    findings = of_code(checks(circuit, project_root=root, symbol_texts={}), CHECK_CODES[7])
    assert findings[0].detail["reason"] == "include_outside_root"


def test_sc008_allows_a_spicemodel_inside_the_root(tmp_path: Path) -> None:
    (tmp_path / "part.lib").write_text(".subckt PART a b\nR1 a b 1k\n.ends\n", encoding="utf-8")
    asy = tmp_path / "part.asy"
    asy.write_text(ASYS["U5"], encoding="utf-8")
    findings = of_code(
        checks(parse_netlist(SUB_DECK), symbol_texts={"U5": asy}, project_root=tmp_path),
        CHECK_CODES[7],
    )
    assert findings[0].status is Status.PASS


# --------------------------------------------------------------------------- #
# SC009 — supply domains, evaluated per pin


def test_sc009_reports_each_pin_not_only_the_group() -> None:
    """A disconnected power pin is reported while its fine sibling is not."""
    pins = {
        "U1": [
            pin("VIN", "VIN", "power", "12V", mapped="VIN"),
            pin("VDD", "VDD", "power", "3V3", mapped="VDD"),
        ]
    }
    # VIN is on its declared rail; VDD sits on LTspice's marker for an unwired pin.
    circuit = parse_netlist(
        "* supply\nXU1 12V NC_02 M1\n.subckt M1 VIN VDD\nRINT VIN VDD 1k\n.ends M1\n"
    )
    netmap = build_netmap(circuit, {"U1": ("VIN", "VDD")})
    project = NeutralProject(supply_domains={"12V": "12V", "3V3": "3V3"})
    findings = of_code(checks(circuit, netmap, project=project, pins=pins), CHECK_CODES[8])
    assert [f.status for f in findings] == [Status.FAIL]
    assert findings[0].refdes == "U1"
    assert findings[0].detail["reason"] == "disconnected"
    assert findings[0].detail["physical_pin"] == "VDD"
    assert findings[0].detail["observed_net"] == "NC_02"
    assert findings[0].detail["expected_domain"] == "3V3"


def test_sc009_passes_when_every_power_pin_is_on_its_domain() -> None:
    pins = {
        "U1": [
            pin("VIN", "VIN", "power", "12V", mapped="VIN"),
            pin("VDD", "VDD", "power", "3V3", mapped="VDD"),
        ]
    }
    circuit = parse_netlist(
        "* supply\nXU1 12V 3V3 M1\n.subckt M1 VIN VDD\nRINT VIN VDD 1k\n.ends M1\n"
    )
    netmap = build_netmap(circuit, {"U1": ("VIN", "VDD")})
    project = NeutralProject(supply_domains={"12V": "12V", "3V3": "3V3"})
    findings = of_code(checks(circuit, netmap, project=project, pins=pins), CHECK_CODES[8])
    assert findings[0].status is Status.PASS
    assert findings[0].detail["pins_examined"] == "2"


def test_sc009_flags_a_pin_on_the_wrong_rail() -> None:
    pins = {
        "U1": [pin("VDD", "VDD", "power", "1V8", mapped="VDD")],
    }
    circuit = parse_netlist("* supply\nXU1 3V3 M1\n.subckt M1 VDD\nRINT VDD 0 1k\n.ends M1\n")
    netmap = build_netmap(circuit, {"U1": ("VDD",)})
    project = NeutralProject(supply_domains={"3V3": "3V3", "1V8": "1V8"})
    findings = of_code(checks(circuit, netmap, project=project, pins=pins), CHECK_CODES[8])
    assert findings[0].detail["reason"] == "domain_mismatch"
    assert findings[0].detail["observed_net"] == "3V3"
    assert findings[0].detail["observed_domain"] == "3V3"
    assert findings[0].detail["expected_domain"] == "1V8"


def test_sc009_reports_two_domains_shorted_onto_one_net() -> None:
    pins = {
        "U1": [pin("VDD", "VDD", "power", "3V3", mapped="VDD")],
        "U2": [pin("VDD1V8", "VDD1V8", "power", "1V8", mapped="VDD1V8")],
    }
    circuit = parse_netlist(
        "* shorted\nXU1 RAIL M1\nXU2 RAIL M2\n"
        ".subckt M1 VDD\nR1 VDD 0 1k\n.ends M1\n"
        ".subckt M2 VDD1V8\nR2 VDD1V8 0 1k\n.ends M2\n"
    )
    netmap = build_netmap(circuit, {"U1": ("VDD",), "U2": ("VDD1V8",)})
    project = NeutralProject(supply_domains={"RAIL": "3V3"})
    findings = of_code(checks(circuit, netmap, project=project, pins=pins), CHECK_CODES[8])
    shorted = [f for f in findings if f.detail.get("reason") == "domains_shorted"]
    assert len(shorted) == 1
    assert shorted[0].nets == ["RAIL"]
    assert shorted[0].detail["domains"] == "1V8, 3V3"
    assert "U1" in shorted[0].detail["pins"] and "U2" in shorted[0].detail["pins"]
    # U1's pin matches the net's declared domain; U2's does not.
    mismatches = [f for f in findings if f.detail.get("reason") == "domain_mismatch"]
    assert [f.refdes for f in mismatches] == ["U2"]
    assert mismatches[0].detail["observed_domain"] == "3V3"
    assert mismatches[0].detail["expected_domain"] == "1V8"


def test_sc009_reports_an_undeclared_net_domain_as_unknown() -> None:
    """Nothing is claimed about a pin whose net has no declared domain."""
    pins = {"U1": [pin("PG", "PG", "output", "3V3", mapped="PG")]}
    circuit = parse_netlist("* pullup\nXU1 PG_3V3 M1\n.subckt M1 PG\nR1 PG 0 10k\n.ends M1\n")
    netmap = build_netmap(circuit, {"U1": ("PG",)})
    project = NeutralProject(supply_domains={"3V3": "3V3"})
    findings = of_code(checks(circuit, netmap, project=project, pins=pins), CHECK_CODES[8])
    assert [f.status for f in findings] == [Status.UNKNOWN]
    assert findings[0].detail["reason"] == "net_domain_undeclared"
    assert findings[0].detail["observed_net"] == "PG_3V3"
    assert findings[0].detail["expected_domain"] == "3V3"


def test_sc009_flags_an_ltspice_nc_node_as_disconnected() -> None:
    pins = {"U1": [pin("VDD", "VDD", "power", "3V3", mapped="VDD")]}
    circuit = parse_netlist("* nc\nXU1 NC_01 M1\n.subckt M1 VDD\nR1 VDD 0 1k\n.ends M1\n")
    netmap = build_netmap(circuit, {"U1": ("VDD",)})
    project = NeutralProject(supply_domains={"3V3": "3V3"})
    findings = of_code(checks(circuit, netmap, project=project, pins=pins), CHECK_CODES[8])
    assert findings[0].detail["reason"] == "disconnected"
    assert findings[0].detail["observed_net"] == "NC_01"
    assert findings[0].nets == ["NC_01"]


def test_sc009_flags_the_plain_nc_marker_as_disconnected() -> None:
    """A netlist builder that has no connection writes the node ``NC``."""
    pins = {"U1": [pin("VDD", "VDD", "power", "3V3", mapped="VDD")]}
    circuit = parse_netlist("* nc\nXU1 NC M1\n.subckt M1 VDD\nR1 VDD 0 1k\n.ends M1\n")
    netmap = build_netmap(circuit, {"U1": ("VDD",)})
    project = NeutralProject(supply_domains={"3V3": "3V3"})
    findings = of_code(checks(circuit, netmap, project=project, pins=pins), CHECK_CODES[8])
    assert [f.status for f in findings] == [Status.FAIL]
    assert findings[0].detail["reason"] == "disconnected"
    assert findings[0].detail["observed_net"] == "NC"


# --------------------------------------------------------------------------- #
# SC010


def test_sc010_passes_for_a_boundary_on_an_existing_part() -> None:
    circuit = parse_netlist(SUB_DECK)
    project = NeutralProject(
        abstractions=[
            AbstractionBoundary(
                refdes="U5",
                omitted_pins=["a"],
                preserved_connectivity=True,
                reason="b is not modeled",
            )
        ]
    )
    findings = of_code(
        checks(
            circuit,
            project=project,
            pins={"U5": [pin("1", "a", "input", None, mapped="a")]},
            symbol_pins={"U5": ("a", "b")},
        ),
        CHECK_CODES[9],
    )
    assert findings[0].status is Status.PASS


def test_sc010_accepts_a_component_refdes_for_an_x_instance() -> None:
    """Neutral data names ``U5``; the netlist token is ``XU5``."""
    circuit = parse_netlist(SUB_DECK)
    netmap = build_netmap(circuit)
    assert "XU5" in circuit.devices
    assert netmap.resolve_refdes("U5") == "XU5"
    project = NeutralProject(
        abstractions=[
            AbstractionBoundary(
                refdes="U5", omitted_pins=[], preserved_connectivity=True, reason="nothing omitted"
            )
        ]
    )
    findings = of_code(checks(circuit, netmap, project=project), CHECK_CODES[9])
    assert [f.status for f in findings] == [Status.PASS]


def test_sc010_finds_the_instance_written_as_xu5() -> None:
    circuit = parse_netlist(
        "* subcircuit instance\n.subckt PART a b\nR1 a b 1k\n.ends PART\nXU5 n1 n2 PART\n"
    )
    netmap = build_netmap(circuit)
    assert netmap.has_refdes("U5") is True
    project = NeutralProject(
        abstractions=[
            AbstractionBoundary(
                refdes="U5",
                omitted_pins=["a"],
                preserved_connectivity=True,
                reason="a is not modeled",
            )
        ]
    )
    findings = of_code(
        checks(circuit, netmap, project=project, symbol_pins={"U5": ("a", "b")}), CHECK_CODES[9]
    )
    assert [f.status for f in findings] == [Status.PASS]


def test_sc010_fails_for_a_refdes_in_neither_form() -> None:
    circuit = parse_netlist(SUB_DECK)
    project = NeutralProject(
        abstractions=[
            AbstractionBoundary(
                refdes="U9", omitted_pins=[], preserved_connectivity=True, reason="typo"
            )
        ]
    )
    findings = of_code(checks(circuit, project=project), CHECK_CODES[9])
    assert [f.status for f in findings] == [Status.FAIL]
    assert findings[0].refdes == "U9"
    assert findings[0].detail["reason"] == "refdes_not_in_circuit"


def test_sc010_flags_an_omitted_pin_that_is_not_a_pin() -> None:
    circuit = parse_netlist(SUB_DECK)
    project = NeutralProject(
        abstractions=[
            AbstractionBoundary(
                refdes="U5",
                omitted_pins=["SW"],
                preserved_connectivity=True,
                reason="SW is not modeled",
            )
        ]
    )
    findings = of_code(
        checks(circuit, project=project, symbol_pins={"U5": ("a", "b")}), CHECK_CODES[9]
    )
    assert findings[0].detail["reason"] == "omitted_pin_not_defined"
    assert findings[0].detail["omitted_pin"] == "SW"


def test_sc010_reports_a_boundary_that_does_not_preserve_connectivity_as_unknown() -> None:
    circuit = parse_netlist(SUB_DECK)
    project = NeutralProject(
        abstractions=[
            AbstractionBoundary(
                refdes="U5",
                omitted_pins=[],
                preserved_connectivity=False,
                reason="lanes are not modeled",
            )
        ]
    )
    findings = of_code(checks(circuit, project=project), CHECK_CODES[9])
    assert findings[0].status is Status.UNKNOWN
    assert findings[0].detail["boundary_reason"] == "lanes are not modeled"
    assert findings[0].detail["preserved_connectivity"] == "false"


def test_sc010_is_not_applicable_without_boundaries() -> None:
    findings = of_code(checks(parse_netlist(SUB_DECK), project=NeutralProject()), CHECK_CODES[9])
    assert findings[0].status is Status.NOT_APPLICABLE


# --------------------------------------------------------------------------- #
# all ten codes always answer


def test_every_code_answers_exactly_once_when_nothing_is_supplied() -> None:
    circuit = parse_netlist(SUB_DECK)
    findings = checks(circuit)
    assert [f.code for f in findings] == list(CHECK_CODES)
    by_code = {f.code: f.status for f in findings}
    assert by_code[CHECK_CODES[0]] is Status.PASS
    assert by_code[CHECK_CODES[3]] is Status.NOT_APPLICABLE
    assert by_code[CHECK_CODES[4]] is Status.NOT_APPLICABLE
    assert by_code[CHECK_CODES[5]] is Status.NOT_APPLICABLE
    # A circuit parsed from text has no directory, so portability cannot be judged.
    assert by_code[CHECK_CODES[7]] is Status.NOT_APPLICABLE
    assert by_code[CHECK_CODES[8]] is Status.NOT_APPLICABLE
    assert by_code[CHECK_CODES[9]] is Status.NOT_APPLICABLE


@pytest.mark.parametrize("code", CHECK_CODES)
def test_findings_always_carry_a_stable_code(code: str) -> None:
    findings = checks(parse_netlist(SUB_DECK))
    assert code in {finding.code for finding in findings}
    assert all(finding.code in CHECK_CODES for finding in findings)
