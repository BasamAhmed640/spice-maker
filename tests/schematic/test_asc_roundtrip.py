"""`.asc` generation and the LTspice round-trip gate.

The round-trip tests are marked ``ltspice``: they write a generated schematic,
run the real ``LTspice.exe -netlist`` on it and compare the produced netlist with
an independently written ``.cir`` deck for the same circuit.  The generation,
geometry and collision tests run without the simulator (they only need symbol
files), so a machine without LTspice still checks determinism and the transforms.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from boardmodeler.schematic.asc import (
    AscFormatError,
    asy_attributes,
    asy_pins,
    parse_asc,
    pin_offsets,
    pin_position,
    read_asc,
    rotate_point,
)
from boardmodeler.schematic.ascgen import (
    GRID,
    CircuitSpec,
    PlacedComponent,
    SchematicBuilder,
    generate_asc,
    layout_series,
    write_asc,
)
from boardmodeler.schematic.netlist import parse_netlist, parse_netlist_file
from boardmodeler.simulation.ltspice import BatchResult, default_lib_dir, netlist_step

#: Minimal copies of the bundled ``res``/``voltage`` pin geometry, so the
#: simulator-free tests do not depend on an LTspice installation.
SYNTHETIC_SYMBOLS = {
    "res.asy": """Version 4
SymbolType CELL
RECTANGLE Normal 0 24 32 88
SYMATTR Prefix R
SYMATTR Value R
SYMATTR Description A resistor
PIN 16 16 NONE 0
PINATTR PinName A
PINATTR SpiceOrder 1
PIN 16 96 NONE 0
PINATTR PinName B
PINATTR SpiceOrder 2
""",
    "voltage.asy": """Version 4
SymbolType CELL
CIRCLE Normal -32 24 32 88
SYMATTR Prefix V
SYMATTR Value V
SYMATTR Description Voltage Source
PIN 0 16 NONE 0
PINATTR PinName +
PINATTR SpiceOrder 1
PIN 0 96 NONE 0
PINATTR PinName -
PINATTR SpiceOrder 2
""",
}

INDEPENDENT_CIR = """* independently written divider
V1 in 0 1
R1 in mid 1k
R2 mid 0 1k
.tran 1m
.end
"""

#: (rotation) -> (R1 anchor, R2 anchor); V1 is always at (112, 96).
ROTATION_LAYOUTS: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {
    "R0": ((240, 96), (240, 208)),
    "R90": ((240, 96), (240, 208)),
    "R180": ((240, 208), (400, 208)),
    "R270": ((240, 96), (240, 208)),
}


@pytest.fixture
def symbols_dir(tmp_path: Path) -> Path:
    target = tmp_path / "symbols"
    target.mkdir()
    for name, text in SYNTHETIC_SYMBOLS.items():
        (target / name).write_text(text, encoding="utf-8")
    return target


@pytest.fixture
def bundled_symbols(tmp_path: Path) -> Path:
    lib = default_lib_dir()
    if lib is None:
        pytest.skip("no LTspice library directory was found")
    target = tmp_path / "bundled"
    target.mkdir()
    for name in ("res.asy", "voltage.asy"):
        source = lib / "sym" / name
        if not source.is_file():
            pytest.skip(f"bundled symbol {name} is missing")
        shutil.copyfile(source, target / name)
    return target


def divider(symbol_dir: Path, rotation: str = "R0") -> CircuitSpec:
    """A 2-resistor divider across a 1 V source, wired through real pins."""
    r1_anchor, r2_anchor = ROTATION_LAYOUTS[rotation]
    builder = SchematicBuilder(symbol_dir)
    builder.add("V1", "voltage", "1", anchor=(112, 96))
    builder.add("R1", "res", "1k", anchor=r1_anchor, rotation=rotation)
    builder.add("R2", "res", "1k", anchor=r2_anchor)
    builder.connect("in", ("V1", "+"), ("R1", "A"))
    builder.connect("mid", ("R1", "B"), ("R2", "A"))
    builder.connect("0", ("V1", "-"), ("R2", "B"))
    builder.directive(".tran 1m")
    return builder.build()


def device_rows(circuit) -> dict[str, tuple[str, tuple[str, ...], str]]:
    return {
        refdes: (device.kind, device.nodes, device.value)
        for refdes, device in circuit.devices.items()
    }


def electrical_directives(circuit) -> list[str]:
    """Directives that describe the circuit, not LTspice's own bookkeeping."""
    return [line for line in circuit.directives if line.split()[0].lower() not in {".backanno"}]


# --------------------------------------------------------------------------- #
# determinism and file handling


def test_generate_is_byte_deterministic(symbols_dir: Path) -> None:
    first = generate_asc(divider(symbols_dir), symbol_dir=symbols_dir)
    second = generate_asc(divider(symbols_dir), symbol_dir=symbols_dir)
    assert first == second
    assert first.startswith("Version 4\nSHEET 1 880 680\n")
    assert first.endswith("\n")


def test_write_asc_is_byte_deterministic(symbols_dir: Path, tmp_path: Path) -> None:
    spec = divider(symbols_dir)
    one = write_asc(spec, tmp_path / "a" / "divider.asc", symbol_dir=symbols_dir)
    two = write_asc(divider(symbols_dir), tmp_path / "b" / "divider.asc", symbol_dir=symbols_dir)
    assert one.read_bytes() == two.read_bytes()


def test_symbols_are_copied_beside_the_schematic(symbols_dir: Path, tmp_path: Path) -> None:
    target = write_asc(
        divider(symbols_dir), tmp_path / "circuit" / "divider.asc", symbol_dir=symbols_dir
    )
    beside = sorted(path.name for path in target.parent.iterdir())
    assert beside == ["divider.asc", "res.asy", "voltage.asy"]


def test_symbols_carry_their_model_beside_the_schematic(tmp_path: Path) -> None:
    symbols = tmp_path / "symbols"
    symbols.mkdir()
    (symbols / "bmtest.asy").write_text(
        """Version 4
SymbolType CELL
RECTANGLE Normal -48 -32 48 32
SYMATTR Prefix X
SYMATTR Value BM_TEST
SYMATTR SpiceModel bm_test.lib
SYMATTR Value2 BM_TEST
PIN -64 0 LEFT 8
PINATTR PinName A
PINATTR SpiceOrder 1
PIN 64 0 RIGHT 8
PINATTR PinName B
PINATTR SpiceOrder 2
""",
        encoding="utf-8",
    )
    (symbols / "bm_test.lib").write_text(
        ".subckt BM_TEST a b\nR1 a b 2k\n.ends\n", encoding="utf-8"
    )
    builder = SchematicBuilder(symbols)
    builder.add("U1", "bmtest", anchor=(256, 128))
    target = write_asc(builder.build(), tmp_path / "circuit" / "sub.asc", symbol_dir=symbols)
    assert (target.parent / "bmtest.asy").is_file()
    assert (target.parent / "bm_test.lib").read_text(encoding="utf-8").startswith(".subckt")


def test_vendor_symbols_are_not_copied_out_of_the_installation(tmp_path: Path) -> None:
    lib = default_lib_dir()
    if lib is None or not (lib / "sym" / "res.asy").is_file():
        pytest.skip("no LTspice library directory was found")
    builder = SchematicBuilder(lib / "sym")
    builder.add("R1", "res", "1k", anchor=(240, 96))
    target = write_asc(builder.build(), tmp_path / "circuit" / "one.asc", symbol_dir=lib / "sym")
    assert sorted(path.name for path in target.parent.iterdir()) == ["one.asc"]


# --------------------------------------------------------------------------- #
# rejections


def test_mirrored_rotation_is_refused(symbols_dir: Path) -> None:
    spec = CircuitSpec(
        components=[PlacedComponent("R1", "res", "1k", (240, 96), "M90")],
    )
    with pytest.raises(ValueError, match="mirror"):
        generate_asc(spec, symbol_dir=symbols_dir)


def test_unknown_rotation_is_refused(symbols_dir: Path) -> None:
    spec = CircuitSpec(components=[PlacedComponent("R1", "res", "1k", (240, 96), "R45")])
    with pytest.raises(ValueError, match="unknown rotation"):
        generate_asc(spec, symbol_dir=symbols_dir)


def test_off_grid_anchor_is_refused(symbols_dir: Path) -> None:
    spec = CircuitSpec(components=[PlacedComponent("R1", "res", "1k", (245, 96))])
    with pytest.raises(ValueError, match="grid"):
        generate_asc(spec, symbol_dir=symbols_dir)


def test_missing_symbol_file_is_refused(symbols_dir: Path) -> None:
    spec = CircuitSpec(components=[PlacedComponent("U1", "nosuchpart", "", (240, 96))])
    with pytest.raises(FileNotFoundError, match="nosuchpart"):
        generate_asc(spec, symbol_dir=symbols_dir)


def test_diagonal_and_zero_length_wires_are_refused(symbols_dir: Path) -> None:
    diagonal = CircuitSpec(wires=[(0, 0, 16, 16)])
    with pytest.raises(ValueError, match="diagonal"):
        generate_asc(diagonal, symbol_dir=symbols_dir)
    degenerate = CircuitSpec(wires=[(16, 16, 16, 16)])
    with pytest.raises(ValueError, match="zero length"):
        generate_asc(degenerate, symbol_dir=symbols_dir)


def test_illegal_flag_name_is_refused(symbols_dir: Path) -> None:
    spec = CircuitSpec(flags=[(16, 16, "bad net")])
    with pytest.raises(ValueError, match="illegal net name"):
        generate_asc(spec, symbol_dir=symbols_dir)


# --------------------------------------------------------------------------- #
# geometry


def test_measured_transforms_place_every_resistor_pin() -> None:
    """Values measured on LTspice 26.0.0.3 for the bundled ``res`` symbol."""
    anchor = (240, 96)
    local_a = (16, 16)
    local_b = (16, 96)
    assert pin_position(anchor, local_a, "R0") == (256, 112)
    assert pin_position(anchor, local_b, "R0") == (256, 192)
    assert pin_position(anchor, local_a, "R90") == (224, 112)
    assert pin_position(anchor, local_b, "R90") == (144, 112)
    assert pin_position(anchor, local_a, "R180") == (224, 80)
    assert pin_position(anchor, local_b, "R180") == (224, 0)
    assert pin_position(anchor, local_a, "R270") == (256, 80)
    assert pin_position(anchor, local_b, "R270") == (336, 80)
    # A y-symmetric symbol (the voltage source) keeps its x offset at zero:
    # its pins land on the anchor's axis, one stub to either side of the body.
    assert pin_position((112, 96), (0, 16), "R90") == (96, 96)
    assert pin_position((112, 96), (0, 96), "R270") == (208, 96)


def test_rotate_point_is_linear_and_direction_preserving() -> None:
    assert rotate_point((1, 0), "R90") == (0, 1)
    assert rotate_point((0, 1), "R270") == (1, 0)
    assert rotate_point((16, 16), "R180") == (-16, -16)
    assert rotate_point((0, -1), "R90") == (1, 0)  # a pin pointing up now points right


def test_builder_pin_positions_and_order(symbols_dir: Path) -> None:
    builder = SchematicBuilder(symbols_dir)
    builder.add("R1", "res", "1k", anchor=(240, 96), rotation="R270")
    assert builder.pin("R1", "A") == (256, 80)
    assert builder.pin("R1", "B") == (336, 80)
    assert builder.pin_order("R1") == ("A", "B")
    assert builder.escape_direction("R1", "A") == (-1, 0)
    assert builder.escape_direction("R1", "B") == (1, 0)
    assert builder.symbols() == {"R1": ("A", "B")}
    with pytest.raises(KeyError, match="no pin"):
        builder.pin("R1", "C")
    with pytest.raises(ValueError, match="already placed"):
        builder.add("R1", "res", "1k")


def test_layout_series_is_on_the_grid() -> None:
    anchors = layout_series(["a", "b", "c"], start=(100, 99), spacing=96)
    assert anchors == [(96, 96), (192, 96), (288, 96)]
    assert all(x % GRID == 0 and y % GRID == 0 for x, y in anchors)
    assert layout_series(["a", "b"], start=(0, 0), spacing=64, axis="y") == [(0, 0), (0, 64)]
    with pytest.raises(ValueError, match="spacing"):
        layout_series(["a"], start=(0, 0), spacing=10)
    with pytest.raises(ValueError, match="axis"):
        layout_series(["a"], start=(0, 0), spacing=16, axis="z")


def test_generated_text_parses_back(symbols_dir: Path, tmp_path: Path) -> None:
    target = write_asc(divider(symbols_dir), tmp_path / "divider.asc", symbol_dir=symbols_dir)
    schematic = read_asc(target)
    assert schematic.version == 4
    assert schematic.sheet == (880, 680)
    assert schematic.refdes() == ["V1", "R1", "R2"]
    assert schematic.symbol_positions == {
        "V1": (112, 96, "R0"),
        "R1": (240, 96, "R0"),
        "R2": (240, 208, "R0"),
    }
    assert schematic.directives() == [".tran 1m"]
    assert set(schematic.nets()) == {"in", "mid", "0"}
    assert all(x1 == x2 or y1 == y2 for x1, y1, x2, y2 in schematic.wires)
    assert schematic.unparsed == []


def test_parse_asc_rejects_a_malformed_symbol_line() -> None:
    with pytest.raises(AscFormatError, match="SYMBOL"):
        parse_asc("Version 4\nSHEET 1 880 680\nSYMBOL res nope 96 R0\n")


def test_parse_asc_keeps_lines_it_does_not_model() -> None:
    schematic = parse_asc("Version 4\nSHEET 1 880 680\nIOPIN 16 16 Right\nWINDOW 0 24 16 Left 2\n")
    assert schematic.unparsed == ["IOPIN 16 16 Right"]
    assert schematic.windows[0].index == 0


def test_asy_pin_parsing_pairs_each_pin_with_its_attributes() -> None:
    pins = asy_pins(SYNTHETIC_SYMBOLS["res.asy"])
    assert sorted(pins) == ["A", "B"]
    assert pins["A"].offset == (16, 16)
    assert pins["A"].order == 1
    assert pins["B"].order == 2
    assert asy_attributes(SYNTHETIC_SYMBOLS["res.asy"])["Prefix"] == "R"
    assert pin_offsets(SYNTHETIC_SYMBOLS["res.asy"]) == {"A": (16, 16), "B": (16, 96)}


# --------------------------------------------------------------------------- #
# collision guard


def test_connect_refuses_to_route_across_another_net(symbols_dir: Path) -> None:
    builder = SchematicBuilder(symbols_dir)
    builder.add("V1", "voltage", "1", anchor=(112, 96))
    builder.add("R1", "res", "1k", anchor=(240, 96))
    # A barrier across the only straight corridor between the two stub ends.
    builder.wire((128, 208), (128, 320))
    with pytest.raises(ValueError, match="cannot route"):
        builder.connect("0", ("V1", "-"), ("R1", "B"))


def test_connect_refuses_a_stub_that_lands_on_another_net(symbols_dir: Path) -> None:
    builder = SchematicBuilder(symbols_dir)
    builder.add("V1", "voltage", "1", anchor=(112, 96))
    builder.add("R1", "res", "1k", anchor=(240, 96))
    builder.wire((96, 208), (400, 208))
    with pytest.raises(ValueError, match="already belongs to another net"):
        builder.connect("0", ("V1", "-"), ("R1", "B"))


def test_explicit_via_waypoints_are_honoured(symbols_dir: Path) -> None:
    builder = SchematicBuilder(symbols_dir)
    builder.add("V1", "voltage", "1", anchor=(112, 96))
    builder.add("R1", "res", "1k", anchor=(240, 96))
    builder.connect("0", ("V1", "-"), ("R1", "B"), via=[(112, 320), (256, 320)])
    points = set()
    for x1, y1, x2, y2 in builder.wires:
        points.update({(x1, y1), (x2, y2)})
    assert (112, 320) in points


# --------------------------------------------------------------------------- #
# the round-trip gate


class TestRoundTrip:
    pytestmark = pytest.mark.ltspice

    def _netlist(self, exe: Path, asc: Path) -> BatchResult:
        result = netlist_step(exe, asc, timeout_s=60)
        assert result.exit_code == 0, result.observed()
        assert result.net_path is not None and result.net_path.is_file()
        return result

    def test_divider_matches_an_independently_written_deck(
        self, ltspice_exe: Path, bundled_symbols: Path, tmp_path: Path
    ) -> None:
        asc = write_asc(
            divider(bundled_symbols), tmp_path / "divider.asc", symbol_dir=bundled_symbols
        )
        generated = parse_netlist_file(self._netlist(ltspice_exe, asc).net_path)
        independent = parse_netlist(INDEPENDENT_CIR)
        assert device_rows(generated) == device_rows(independent)
        assert set(generated.nodes()) == set(independent.nodes())
        assert electrical_directives(generated) == electrical_directives(independent)

    @pytest.mark.parametrize("rotation", sorted(ROTATION_LAYOUTS))
    def test_rotation_lands_pins_where_the_transform_says(
        self, ltspice_exe: Path, bundled_symbols: Path, tmp_path: Path, rotation: str
    ) -> None:
        asc = write_asc(
            divider(bundled_symbols, rotation),
            tmp_path / f"divider_{rotation}.asc",
            symbol_dir=bundled_symbols,
        )
        net = self._netlist(ltspice_exe, asc).net_path
        text = net.read_text(encoding="utf-8", errors="replace")
        # An unwired pin would be named NC_xx by LTspice.
        assert "NC_" not in text, text
        circuit = parse_netlist_file(net)
        assert device_rows(circuit) == device_rows(parse_netlist(INDEPENDENT_CIR))

    def test_generated_netlist_resolves_symbol_pin_names_as_nets(
        self, ltspice_exe: Path, bundled_symbols: Path, tmp_path: Path
    ) -> None:
        spec = divider(bundled_symbols)
        asc = write_asc(spec, tmp_path / "divider.asc", symbol_dir=bundled_symbols)
        circuit = parse_netlist_file(self._netlist(ltspice_exe, asc).net_path)
        from boardmodeler.schematic.netlist import build_netmap

        netmap = build_netmap(circuit, {"R1": ("A", "B"), "R2": ("A", "B")})
        assert netmap.node_of("R1", "A") == "in"
        assert netmap.node_of("R1", "B") == "mid"
        assert netmap.node_of("R2", "A") == "mid"
        assert netmap.node_of("R2", "B") == "0"
