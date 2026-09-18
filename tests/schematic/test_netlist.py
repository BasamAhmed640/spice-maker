"""Netlist parsing and flattened connectivity.

Each test targets a behaviour that a plausible bug would break: a folded
continuation, a stripped inline comment, a wrong terminal count, a duplicate
refdes, a cycle that must not hang, and pin lookup through both a subcircuit
definition and a supplied symbol pin order.
"""

from __future__ import annotations

import pytest

from boardmodeler.schematic.netlist import (
    NetlistError,
    NetMap,
    build_netmap,
    parse_netlist,
    parse_netlist_file,
)
from boardmodeler.verification.assertions import Connectivity

DECK = """* BoardModeler parser fixture
V1 in 0 PULSE(0 1 0 1n 1n 1 2) ; stimulus
R1 in mid 1k tc=0.01
L1 mid out 10u
C1 out 0 1u ic=0
D1 out 0 DFAST
I1 0 out 1m
B1 sense 0 V=1.5*V(out)
E1 amp1 0 in 0 10
G1 amp2 0 in 0 1m
H1 amp3 0 V1 1
S1 out 0 ctrl 0 SW1
K1 L1 L2 0.98
Q1 c1 b1 e1 QMOD
M1 d1 g1 s1 b1 MMOD L=1u W=10u
M2 d2 g2 s2 MMOD3
J1 jd jg js JMOD
.subckt DIV A B
R10 A B 1k
.ends DIV
X1 in mid DIV
.subckt NEST P Q params: K1=2 K2=3
R20 P Q {K1}
.ends NEST
X2 out 0 NEST
.model DFAST D(IS=1e-14)
.model SW1 SW(RON=1 ROFF=1G VT=0.5)
.tran 1m
.end
"""


def test_parses_every_required_element_kind() -> None:
    circuit = parse_netlist(DECK)
    assert set(circuit.devices) == {
        "V1",
        "R1",
        "L1",
        "C1",
        "D1",
        "I1",
        "B1",
        "E1",
        "G1",
        "H1",
        "S1",
        "K1",
        "Q1",
        "M1",
        "M2",
        "J1",
        "X1",
        "X2",
    }
    kinds = {refdes: device.kind for refdes, device in circuit.devices.items()}
    assert kinds["M1"] == "M" and kinds["Q1"] == "Q" and kinds["X1"] == "X"


def test_two_terminal_values_nodes_and_params() -> None:
    circuit = parse_netlist(DECK)
    assert circuit.devices["V1"].nodes == ("in", "0")
    assert circuit.devices["V1"].value == "PULSE(0 1 0 1n 1n 1 2)"
    assert circuit.devices["R1"].nodes == ("in", "mid")
    assert circuit.devices["R1"].value == "1k"
    assert circuit.devices["R1"].params == {"tc": "0.01"}
    assert circuit.devices["C1"].params == {"ic": "0"}
    assert circuit.devices["B1"].value == "V=1.5*V(out)"


def test_four_terminal_and_model_devices() -> None:
    circuit = parse_netlist(DECK)
    assert circuit.devices["E1"].nodes == ("amp1", "0", "in", "0")
    assert circuit.devices["E1"].value == "10"
    assert circuit.devices["S1"].nodes == ("out", "0", "ctrl", "0")
    assert circuit.devices["S1"].value == "SW1"
    assert circuit.devices["M1"].nodes == ("d1", "g1", "s1", "b1")
    assert circuit.devices["M1"].params == {"L": "1u", "W": "10u"}
    # A three-terminal MOSFET keeps three nodes.
    assert circuit.devices["M2"].nodes == ("d2", "g2", "s2")
    assert circuit.devices["Q1"].nodes == ("c1", "b1", "e1")
    # A coupling refers to two inductors; those are not nodes.
    assert circuit.devices["K1"].nodes == ()
    assert circuit.devices["K1"].extra == ("L1", "L2")
    assert circuit.devices["K1"].value == "0.98"
    # An H element's control element is a name, not a node.
    assert circuit.devices["H1"].nodes == ("amp3", "0")
    assert circuit.devices["H1"].extra == ("V1",)


def test_subckts_ports_params_and_bodies() -> None:
    circuit = parse_netlist(DECK)
    assert circuit.subckts["DIV"].ports == ("A", "B")
    assert circuit.subckts["NEST"].ports == ("P", "Q")
    assert circuit.subckts["NEST"].params == {"K1": "2", "K2": "3"}
    assert list(circuit.subckts["NEST"].devices) == ["R20"]
    assert circuit.devices["X2"].subckt == "NEST"


def test_directives_and_includes_are_kept() -> None:
    circuit = parse_netlist(DECK + ".include models/extra.lib\n.lib cmp/lt.lib\n.backanno\n")
    assert circuit.includes == ["models/extra.lib", "cmp/lt.lib"]
    assert ".tran 1m" in circuit.directives
    assert any(line.startswith(".model DFAST") for line in circuit.directives)
    assert ".backanno" in circuit.directives
    assert ".end" not in circuit.directives


def test_continuation_lines_are_folded() -> None:
    circuit = parse_netlist(
        """* continued
.subckt BIG a b
+ c d
+ params: G=2
R1 a b 1k
.ends BIG
X1 n1 n2 n3 n4 BIG
"""
    )
    assert circuit.subckts["BIG"].ports == ("a", "b", "c", "d")
    assert circuit.subckts["BIG"].params == {"G": "2"}
    assert circuit.devices["X1"].nodes == ("n1", "n2", "n3", "n4")


def test_inline_comment_is_stripped_not_parsed() -> None:
    circuit = parse_netlist("* c\nR1 a b 1k ; this is a comment\n")
    assert circuit.devices["R1"].value == "1k"
    assert circuit.devices["R1"].nodes == ("a", "b")


def test_ltspice_instance_name_is_normalised() -> None:
    """LTspice writes ``X\u00a7U1`` for a schematic instance named ``U1``."""
    circuit = parse_netlist("* net\nX\u00a7U1 in 0 SUB\nX\u00a7X1 a b SUB2\n")
    assert set(circuit.devices) == {"U1", "X1"}
    assert circuit.devices["U1"].subckt == "SUB"


def test_unknown_element_letter_raises_with_line_number() -> None:
    with pytest.raises(NetlistError) as info:
        parse_netlist("* bad\nR1 a b 1k\nZ1 a b 5\n")
    assert info.value.line == 3
    assert "Z1" in info.value.text
    assert "unknown element letter" in str(info.value)


def test_wrong_node_count_raises_with_line_number() -> None:
    with pytest.raises(NetlistError) as info:
        parse_netlist("* bad\nR1 a\n")
    assert info.value.line == 2
    assert "expected" in str(info.value)


def test_missing_ends_raises_and_names_the_subckt_line() -> None:
    with pytest.raises(NetlistError) as info:
        parse_netlist("* bad\nR1 a b 1k\n.subckt OPEN a b\nR2 a b 2k\n")
    assert info.value.line == 3
    assert "never closed" in str(info.value)


def test_ends_without_subckt_raises() -> None:
    with pytest.raises(NetlistError) as info:
        parse_netlist("* bad\n.ends NOPE\n")
    assert info.value.line == 2


def test_duplicate_refdes_raises_with_the_first_definition_line() -> None:
    with pytest.raises(NetlistError) as info:
        parse_netlist("* bad\nR1 a b 1k\nR1 b c 2k\n")
    assert info.value.line == 3
    assert "duplicate refdes" in str(info.value)
    assert "line 2" in str(info.value)


def test_same_refdes_in_two_subckts_is_permitted() -> None:
    circuit = parse_netlist(
        "* scopes\n.subckt A p q\nR1 p q 1k\n.ends A\n.subckt B p q\nR1 p q 2k\n.ends B\nX1 a b A\nX2 b c B\n"
    )
    assert circuit.subckts["A"].devices["R1"].value == "1k"
    assert circuit.subckts["B"].devices["R1"].value == "2k"


def test_parse_netlist_file_reads_utf16_and_utf8(tmp_path) -> None:
    target = tmp_path / "deck.net"
    text = "* net\nX\u00a7U1 in 0 SUB\n"
    target.write_bytes(text.encode("utf-16-le"))
    circuit = parse_netlist_file(target)
    assert list(circuit.devices) == ["U1"]
    assert circuit.source_path == target
    assert circuit.title == "net"


def test_nodes_and_refdes_are_deterministic() -> None:
    circuit = parse_netlist(DECK)
    assert circuit.nodes()[0] == "in"
    assert set(circuit.nodes()) >= {"in", "mid", "out", "0", "sense"}
    assert circuit.refdes()[:3] == ["V1", "R1", "L1"]


# --------------------------------------------------------------------------- #
# NetMap


NESTED = """* nested
X1 in mid AMP
.subckt AMP a b
R9 a b 1k
X2 a b INNER
.ends AMP
.subckt INNER p q
R8 p q 3k
.ends INNER
"""


def test_netmap_resolves_position_and_port_name() -> None:
    netmap = build_netmap(parse_netlist(NESTED))
    assert netmap.node_of("X1", "1") == "in"
    assert netmap.node_of("X1", "2") == "mid"
    assert netmap.node_of("X1", "a") == "in"
    assert netmap.node_of("X1", "3") is None
    assert netmap.node_of("X1", "nope") is None


def test_netmap_reports_the_subcircuit_side_port_name() -> None:
    netmap = build_netmap(parse_netlist(NESTED))
    assert netmap.port_name("X1", "2") == "b"
    assert netmap.port_name("X1", "1") == "a"
    assert netmap.port_name("R9", "1") is None


def test_netmap_uses_a_supplied_symbol_pin_list() -> None:
    circuit = parse_netlist(NESTED)
    netmap = build_netmap(circuit, {"X1": ("VIN", "GND")})
    assert netmap.node_of("X1", "VIN") == "in"
    assert netmap.node_of("X1", "GND") == "mid"
    # The positional keys stay available for a caller that has no symbol.
    assert netmap.node_of("X1", "2") == "mid"


def test_netmap_flattens_nested_instances() -> None:
    netmap = build_netmap(parse_netlist(NESTED))
    assert netmap.node_of("X1.R9", "1") == "in"
    assert netmap.node_of("X1.R9", "2") == "mid"
    assert netmap.node_of("X1.X2", "1") == "in"
    assert netmap.node_of("X1.X2.R8", "2") == "mid"
    assert netmap.has_refdes("X1.X2.R8")
    assert netmap.has_refdes("R9") is True
    assert netmap.node_of("R9", "2") == "mid"


def test_bare_nested_refdes_is_refused_when_ambiguous() -> None:
    netmap = build_netmap(parse_netlist(NESTED + "X3 other 0 AMP\n"))
    assert netmap.has_refdes("R9") is True
    assert "R9" in netmap.ambiguous
    assert netmap.node_of("R9", "1") is None
    assert netmap.node_of("X1.R9", "1") == "in"
    assert netmap.node_of("X3.R9", "1") == "other"


def test_netmap_detects_a_subcircuit_cycle_and_terminates() -> None:
    netmap = build_netmap(
        parse_netlist(
            """* cycle
X1 a b A
.subckt A p q
X9 p q B
.ends A
.subckt B p q
X8 p q A
.ends B
"""
        )
    )
    assert netmap.cycles == ["X1.X9.X8:A"]
    assert netmap.node_of("X1.X9", "1") == "a"
    assert netmap.node_of("X1.X9.X8", "2") == "b"


def test_netmap_pins_on_ground_and_nc_markers() -> None:
    netmap = build_netmap(parse_netlist(NESTED + "V1 in 0 1\n"))
    pins = netmap.pins_on("in")
    assert ("V1", "+") in pins
    assert ("X1", "a") in pins
    assert ("X1.R9", "1") in pins
    assert netmap.is_ground("0") is True
    assert netmap.is_ground("in") is False
    assert netmap.is_connected_node("NC_01") is False
    assert netmap.is_connected_node("in") is True


def test_netmap_resolves_a_component_refdes_to_its_x_instance() -> None:
    """A neutral component ``U5`` finds the netlist's ``XU5`` instance token."""
    netmap = build_netmap(
        parse_netlist(".subckt PART a b\nR1 a b 1k\n.ends PART\nXU5 n1 n2 PART\n")
    )
    assert netmap.resolve_refdes("U5") == "XU5"
    assert netmap.resolve_refdes("XU5") == "XU5"
    assert netmap.has_refdes("U5") is True
    assert netmap.node_of("U5", "a") == "n1"
    assert netmap.node_of("U5", "2") == "n2"
    assert netmap.port_name("U5", "b") == "b"
    assert ("XU5", "a") in netmap.pins_on("n1")
    assert netmap.resolve_refdes("U9") is None


def test_exact_refdes_wins_over_the_x_prefixed_form() -> None:
    netmap = build_netmap(
        parse_netlist("* both\nR1 p q 1k\nXR1 m n SUB\n.subckt SUB a b\nR2 a b 2k\n.ends SUB\n")
    )
    assert netmap.resolve_refdes("R1") == "R1"
    assert netmap.node_of("R1", "1") == "p"
    assert netmap.node_of("XR1", "a") == "m"


def test_netmap_satisfies_the_connectivity_protocol() -> None:
    netmap: NetMap = build_netmap(parse_netlist(NESTED))
    assert isinstance(netmap, Connectivity)
    assert netmap.pins_on("no_such_net") == []
    assert netmap.has_refdes("nope") is False
    assert netmap.node_of("nope", "1") is None
