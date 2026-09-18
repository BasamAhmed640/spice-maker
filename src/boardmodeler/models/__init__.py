"""Model library: content-addressed vendor models and generated primitives (D8).

``primitives`` holds the parameterized behavioural primitive library that the
schematic-level decks are built from.
"""

from __future__ import annotations

from boardmodeler.models.primitives import (
    PRIMITIVE_PARAMS,
    PRIMITIVE_PORT_ORDER,
    instance_card,
    port_order_matches,
    primitive_text,
    write_primitive_library,
)

__all__ = [
    "PRIMITIVE_PARAMS",
    "PRIMITIVE_PORT_ORDER",
    "instance_card",
    "port_order_matches",
    "primitive_text",
    "write_primitive_library",
]
