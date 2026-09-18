"""Constrained expression AST (D5).

Requirements are expressed as data, never as code: there is no ``eval``, no
``exec``, and no code generation anywhere in this module. The op set below is
closed — an unknown ``op`` is a validation error, and a bare string payload is
rejected because every branch is discriminated by an explicit ``op`` field.

Semantics that the evaluator (``verification.assertions``) must preserve:

* ``interval`` of ``None`` means "the whole saved window"; a missing or only
  partially covered window makes the verdict UNKNOWN, never PASS.
* A "must not happen" requirement (``not`` / ``violate_detected``) additionally
  requires that the run reached the end of the window with the signal
  observable; absence of a crossing alone is not a pass.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

__all__ = [
    "ALL_OPS",
    "EventRef",
    "Expr",
    "ExprAdapter",
    "IntervalSpec",
    "expr_to_json",
    "parse_expr",
]


class _ExprNode(BaseModel):
    """Base for AST nodes: strict, immutable, no anonymous fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class IntervalSpec(_ExprNode):
    """Half-open assertion window in simulated seconds."""

    start_s: float
    end_s: float

    @model_validator(mode="after")
    def _check_order(self) -> IntervalSpec:
        if not self.start_s < self.end_s:
            raise ValueError(f"interval start_s ({self.start_s}) must be < end_s ({self.end_s})")
        return self


class EventRef(_ExprNode):
    """A crossing (or window entry) on a named signal."""

    signal: str
    kind: Literal["rise_above", "fall_below", "eq_window"]
    value: float | None = None
    unit: str
    qualifier: Literal["first", "last"] = "first"

    @model_validator(mode="after")
    def _check_value(self) -> EventRef:
        if self.kind != "eq_window" and self.value is None:
            raise ValueError(f"event kind {self.kind!r} requires a value")
        return self


# --------------------------------------------------------------------------- #
# signal comparisons


class _SignalValue(_ExprNode):
    signal: str
    value: float
    unit: str
    interval: IntervalSpec | None = None


class LtOp(_SignalValue):
    op: Literal["lt"]


class LeOp(_SignalValue):
    op: Literal["le"]


class GtOp(_SignalValue):
    op: Literal["gt"]


class GeOp(_SignalValue):
    op: Literal["ge"]


class RiseAboveOp(_SignalValue):
    op: Literal["rise_above"]


class FallBelowOp(_SignalValue):
    op: Literal["fall_below"]


class BetweenOp(_ExprNode):
    op: Literal["between"]
    signal: str
    low: float
    high: float
    unit: str
    interval: IntervalSpec | None = None


class HoldOp(_ExprNode):
    op: Literal["hold"]
    signal: str
    value: float
    unit: str
    interval: IntervalSpec | None = None
    stable: bool


# --------------------------------------------------------------------------- #
# event relations


class EventDelayOp(_ExprNode):
    op: Literal["event_delay"]
    start: EventRef
    end: EventRef
    min_s: float
    max_s: float


class PulseWidthOp(_ExprNode):
    op: Literal["pulse_width"]
    start: EventRef
    end: EventRef
    min_s: float
    max_s: float


class OrderingOp(_ExprNode):
    op: Literal["ordering"]
    first: EventRef
    then: EventRef


# --------------------------------------------------------------------------- #
# logical composition


class StateDependentOp(_ExprNode):
    op: Literal["state_dependent"]
    when: EventRef
    then: Expr


class AllOfOp(_ExprNode):
    op: Literal["all_of"]
    items: list[Expr]


class AnyOfOp(_ExprNode):
    op: Literal["any_of"]
    items: list[Expr]


class NotOp(_ExprNode):
    op: Literal["not"]
    item: Expr


# --------------------------------------------------------------------------- #
# connectivity (evaluated against the netlist, not the waveform data)


class NetEqualsOp(_ExprNode):
    op: Literal["net_equals"]
    refdes: str
    pin: str
    net: str


class NetNotEqualsOp(_ExprNode):
    op: Literal["net_not_equals"]
    refdes: str
    pin: str
    net: str


class PullupDomainOp(_ExprNode):
    op: Literal["pullup_domain"]
    refdes: str
    pin: str
    net: str
    domain: str


class PinConnectedOp(_ExprNode):
    op: Literal["pin_connected"]
    refdes: str
    pin: str


class PinOpenOp(_ExprNode):
    op: Literal["pin_open"]
    refdes: str
    pin: str


_ExprUnion = Union[  # noqa: UP007 - explicit Union is required inside Annotated
    LtOp,
    LeOp,
    GtOp,
    GeOp,
    BetweenOp,
    RiseAboveOp,
    FallBelowOp,
    EventDelayOp,
    PulseWidthOp,
    OrderingOp,
    HoldOp,
    StateDependentOp,
    AllOfOp,
    AnyOfOp,
    NotOp,
    NetEqualsOp,
    NetNotEqualsOp,
    PullupDomainOp,
    PinConnectedOp,
    PinOpenOp,
]

Expr = Annotated[_ExprUnion, Field(discriminator="op")]
"""Discriminated union over the closed op set."""

ExprAdapter: TypeAdapter[Expr] = TypeAdapter(Expr)

ALL_OPS: frozenset[str] = frozenset(
    {
        "lt",
        "le",
        "gt",
        "ge",
        "between",
        "rise_above",
        "fall_below",
        "event_delay",
        "pulse_width",
        "ordering",
        "hold",
        "state_dependent",
        "all_of",
        "any_of",
        "not",
        "net_equals",
        "net_not_equals",
        "pullup_domain",
        "pin_connected",
        "pin_open",
    }
)

# Recursive nodes need an explicit rebuild now that ``Expr`` exists.
for _node in (StateDependentOp, AllOfOp, AnyOfOp, NotOp):
    _node.model_rebuild(_types_namespace=globals())


def parse_expr(data: object) -> Expr:
    """Validate ``data`` as an expression node.

    Raises ``pydantic.ValidationError`` for an unknown ``op``, a missing field,
    an extra field, or a non-object payload such as a raw code string.
    """
    return ExprAdapter.validate_python(data)


def expr_to_json(expr: Expr) -> str:
    """Stable JSON for an expression node (used for hashing and reporting)."""
    return ExprAdapter.dump_json(expr, indent=2).decode("utf-8")
