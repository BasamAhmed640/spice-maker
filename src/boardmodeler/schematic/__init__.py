"""Schematic layer: `.asc`/netlist parsing, deterministic generation, static checks (D9).

* :mod:`boardmodeler.schematic.netlist` — SPICE parsing and flattened connectivity
* :mod:`boardmodeler.schematic.asc` — `.asc`/`.asy` parsing and the pin transforms
* :mod:`boardmodeler.schematic.ascgen` — deterministic `.asc` generation
* :mod:`boardmodeler.schematic.neutral` — the neutral CSV/JSON project model
* :mod:`boardmodeler.schematic.static_check` — SC001-SC010
"""

from __future__ import annotations

from boardmodeler.schematic.asc import (
    AscSchematic,
    asy_attributes,
    asy_pins,
    parse_asc,
    pin_offsets,
    pin_position,
    read_asc,
    rotate_point,
)
from boardmodeler.schematic.ascgen import (
    CircuitSpec,
    PlacedComponent,
    SchematicBuilder,
    generate_asc,
    layout_series,
    write_asc,
)
from boardmodeler.schematic.netlist import (
    Circuit,
    Device,
    NetlistError,
    NetMap,
    SubcktDef,
    build_netmap,
    parse_netlist,
    parse_netlist_file,
)
from boardmodeler.schematic.neutral import (
    ComponentRow,
    ConnectionRow,
    NeutralProject,
    read_neutral_project,
    to_circuit,
    validate_neutral,
)
from boardmodeler.schematic.static_check import CHECK_CODES, run_static_checks

__all__ = [
    "CHECK_CODES",
    "AscSchematic",
    "Circuit",
    "CircuitSpec",
    "ComponentRow",
    "ConnectionRow",
    "Device",
    "NetMap",
    "NetlistError",
    "NeutralProject",
    "PlacedComponent",
    "SchematicBuilder",
    "SubcktDef",
    "asy_attributes",
    "asy_pins",
    "build_netmap",
    "generate_asc",
    "layout_series",
    "parse_asc",
    "parse_netlist",
    "parse_netlist_file",
    "pin_offsets",
    "pin_position",
    "read_asc",
    "read_neutral_project",
    "rotate_point",
    "run_static_checks",
    "to_circuit",
    "validate_neutral",
    "write_asc",
]
