"""Constrained-expression evaluation and vacuous-pass guards (Phase 1 steps 4-5).

This module decides every PASS/FAIL/UNKNOWN in the product, so its rules are
stated explicitly:

**Comparison ops are universal.** ``gt``/``ge``/``lt``/``le``/``between`` assert
that *every saved sample* inside the interval satisfies the relation. The detail
reports the worst sample and where it occurred. An "eventually rises above"
requirement is expressed with ``rise_above`` (existential crossing), not by
loosening a comparison.

**Missing data is never a pass.** A missing signal, an interval that the run did
not reach, too few samples, too coarse a time step, or an unusable run produce
UNKNOWN (or BLOCKED for a simulator failure) with every observed fact in the
detail.

**"Must not occur" is guarded.** ``not`` inverts a verdict only when the inner
check was computed over a window the run actually covered; otherwise the result
is UNKNOWN. Absence of a crossing on its own is never a pass.

**Tolerances only widen the acceptance band**, never narrow it, and the measured
value is always reported so a relaxed bound is visible in the result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol, runtime_checkable

import numpy as np

from boardmodeler.domain.enums import Status
from boardmodeler.domain.expressions import EventRef, Expr, IntervalSpec
from boardmodeler.simulation.limits import (
    DEFAULT_MAX_DT_RATIO,
    DEFAULT_MIN_POINTS,
    RunUsability,
    check_window,
)
from boardmodeler.simulation.raw import RawFile

__all__ = [
    "Connectivity",
    "EvalContext",
    "Verdict",
    "evaluate",
    "find_crossing",
]


@runtime_checkable
class Connectivity(Protocol):
    """Netlist-level connectivity, as needed by the connectivity ops."""

    def node_of(self, refdes: str, pin: str) -> str | None:
        """Net name attached to ``refdes.pin``, or ``None`` when absent."""

    def pins_on(self, net: str) -> list[tuple[str, str]]:
        """Every ``(refdes, pin)`` attached to ``net``."""

    def has_refdes(self, refdes: str) -> bool:
        """Whether the netlist contains this reference designator at all."""


@dataclass(frozen=True)
class Verdict:
    """Outcome of evaluating one expression node."""

    status: Status
    detail: str
    measured: dict[str, float | str] = field(default_factory=dict)
    unknown_reason: str | None = None
    blocked_reason: str | None = None
    op: str = ""
    events: dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status is Status.PASS

    def as_row(self) -> dict[str, object]:
        return {
            "op": self.op,
            "status": self.status.value,
            "detail": self.detail,
            "measured": dict(self.measured),
            "unknown_reason": self.unknown_reason,
            "blocked_reason": self.blocked_reason,
        }


def _pass(
    op: str, detail: str, measured: Mapping[str, float | str] = {}, **events: float
) -> Verdict:
    return Verdict(
        status=Status.PASS, detail=detail, measured=dict(measured), op=op, events=dict(events)
    )


def _fail(
    op: str, detail: str, measured: Mapping[str, float | str] = {}, **events: float
) -> Verdict:
    return Verdict(
        status=Status.FAIL, detail=detail, measured=dict(measured), op=op, events=dict(events)
    )


def _unknown(
    op: str, reason: str, detail: str, measured: Mapping[str, float | str] = {}
) -> Verdict:
    return Verdict(
        status=Status.UNKNOWN,
        detail=detail,
        measured=dict(measured),
        unknown_reason=reason,
        op=op,
    )


def _blocked(op: str, reason: str, detail: str) -> Verdict:
    return Verdict(status=Status.BLOCKED, detail=detail, blocked_reason=reason, op=op)


def _na(op: str, detail: str, measured: Mapping[str, float | str] = {}) -> Verdict:
    return Verdict(status=Status.NOT_APPLICABLE, detail=detail, measured=dict(measured), op=op)


@dataclass
class EvalContext:
    """Everything an evaluator may use. Nothing is looked up implicitly."""

    raw: RawFile | None = None
    usability: RunUsability | None = None
    connectivity: Connectivity | None = None
    supply_domains: Mapping[str, str] = field(default_factory=dict)
    default_window: IntervalSpec | None = None
    tolerances: Mapping[str, float] = field(default_factory=dict)
    max_dt_ratio: float = DEFAULT_MAX_DT_RATIO
    min_points: int = DEFAULT_MIN_POINTS

    def blocked(self, op: str) -> Verdict | None:
        """A BLOCKED verdict when the run itself failed, else ``None``."""
        if self.usability is not None and not self.usability.usable:
            return _blocked(
                op,
                self.usability.blocked_reason or "sim_unusable",
                f"the run cannot be evaluated: {self.usability.blocked_reason} ({self.usability.detail})",
            )
        if self.raw is None:
            return _blocked(op, "sim_output_missing", "no waveform data was loaded for this run")
        return None

    def value_tolerance(self) -> float:
        """Relative widening for comparison bounds (0 by default)."""
        return float(self.tolerances.get("value_pct", 0.0)) / 100.0

    def hold_tolerance(self, value: float) -> float:
        pct = float(self.tolerances.get("hold_pct", 2.0)) / 100.0
        absolute = float(self.tolerances.get("hold_abs", 0.0))
        return max(abs(value) * pct, absolute)


# --------------------------------------------------------------------------- #
# waveform helpers


def find_crossing(
    time_axis: np.ndarray,
    signal: np.ndarray,
    *,
    kind: str,
    value: float | None,
    qualifier: str = "first",
) -> float | None:
    """Interpolated time of the first/last crossing, or ``None`` if there is none.

    ``rise_above``/``fall_below`` are true crossings (a bracketing pair with the
    right sign change); ``eq_window`` is the start of the saved window, which
    lets a requirement refer to "from the beginning".
    """
    if kind == "eq_window":
        return float(time_axis[0]) if time_axis.size else None
    if value is None or time_axis.size < 2:
        return None

    deltas = signal - float(value)
    if kind == "rise_above":
        candidates = np.flatnonzero((deltas[:-1] <= 0.0) & (deltas[1:] > 0.0))
    elif kind == "fall_below":
        candidates = np.flatnonzero((deltas[:-1] >= 0.0) & (deltas[1:] < 0.0))
    else:
        raise ValueError(f"unsupported event kind {kind!r}")

    if candidates.size == 0:
        return None
    index = int(candidates[0] if qualifier == "first" else candidates[-1])
    t0, t1 = float(time_axis[index]), float(time_axis[index + 1])
    d0, d1 = float(deltas[index]), float(deltas[index + 1])
    if d1 == d0:
        return t0
    fraction = (0.0 - d0) / (d1 - d0)
    return t0 + fraction * (t1 - t0)


def _signal(
    ctx: EvalContext, name: str
) -> tuple[np.ndarray | None, np.ndarray | None, Verdict | None]:
    """Return ``(time, values, failure_verdict)`` for a signal."""
    if ctx.raw is None:
        return (
            None,
            None,
            _blocked("signal", "sim_output_missing", "no waveform data was loaded for this run"),
        )
    if not ctx.raw.has(name):
        return (
            None,
            None,
            _unknown(
                "signal",
                "signal_not_saved",
                f"{name} is not in the saved waveform; saved variables are {ctx.raw.variables}",
            ),
        )
    time_axis = ctx.raw.time_column()
    if time_axis is None:
        return None, None, _unknown("signal", "no_time_axis", "the saved plot has no time axis")
    return time_axis, ctx.raw.column(name), None


def _bounds(ctx: EvalContext, interval: IntervalSpec | None) -> tuple[float | None, float | None]:
    chosen = interval or ctx.default_window
    if chosen is None:
        return None, None
    return float(chosen.start_s), float(chosen.end_s)


# --------------------------------------------------------------------------- #
# op handlers


def _eval_comparison(expr: Expr, ctx: EvalContext) -> Verdict:
    op = str(expr.op)
    time_axis, values, failure = _signal(ctx, expr.signal)  # type: ignore[union-attr]
    if failure is not None:
        return replace(failure, op=op)
    assert time_axis is not None and values is not None
    interval = getattr(expr, "interval", None)
    blocked = ctx.blocked(op)
    if blocked is not None:
        return blocked
    start_s, end_s = _bounds(ctx, interval)
    check = check_window(
        ctx.raw,
        start_s,
        end_s,
        signals=[expr.signal],
        max_dt_ratio=ctx.max_dt_ratio,
        min_points=ctx.min_points,
    )  # type: ignore[arg-type]
    if not check.ok:
        return _unknown(op, f"window_unusable:{check.reason}", check.detail)

    low = float(time_axis[0]) if start_s is None else start_s
    high = float(time_axis[-1]) if end_s is None else end_s
    mask = (time_axis >= low) & (time_axis <= high)
    window_t = time_axis[mask]
    window_v = values[mask]

    target = float(expr.value)  # type: ignore[union-attr]
    rel = ctx.value_tolerance()
    if op in ("gt", "ge"):
        bound = target * (1.0 - rel)
        worst_index = int(np.argmin(window_v))
        worst = float(window_v[worst_index])
        worst_t = float(window_t[worst_index])
        ok = bool(np.all(window_v > bound if op == "gt" else window_v >= bound))
        measured = {
            f"min({expr.signal})": worst,  # type: ignore[union-attr]
            "at_s": worst_t,
            "bound": bound,
            "samples": float(window_v.size),
        }
        detail = (
            f"min({expr.signal}) over [{low:g}, {high:g}] s = {worst:.6g} {expr.unit} at {worst_t:g} s "  # type: ignore[union-attr]
            f"(required {'> ' if op == 'gt' else '>= '}{bound:.6g}{' [tolerance-widened]' if rel else ''})"
        )
        return _pass(op, detail, measured) if ok else _fail(op, detail, measured)

    bound = target * (1.0 + rel)
    worst_index = int(np.argmax(window_v))
    worst = float(window_v[worst_index])
    worst_t = float(window_t[worst_index])
    ok = bool(np.all(window_v < bound if op == "lt" else window_v <= bound))
    measured = {
        f"max({expr.signal})": worst,  # type: ignore[union-attr]
        "at_s": worst_t,
        "bound": bound,
        "samples": float(window_v.size),
    }
    detail = (
        f"max({expr.signal}) over [{low:g}, {high:g}] s = {worst:.6g} {expr.unit} at {worst_t:g} s "  # type: ignore[union-attr]
        f"(required {'< ' if op == 'lt' else '<= '}{bound:.6g}{' [tolerance-widened]' if rel else ''})"
    )
    return _pass(op, detail, measured) if ok else _fail(op, detail, measured)


def _eval_between(expr: Expr, ctx: EvalContext) -> Verdict:
    op = "between"
    time_axis, values, failure = _signal(ctx, expr.signal)  # type: ignore[union-attr]
    if failure is not None:
        return failure
    assert time_axis is not None and values is not None
    blocked = ctx.blocked(op)
    if blocked is not None:
        return blocked
    interval = getattr(expr, "interval", None)
    start_s, end_s = _bounds(ctx, interval)
    check = check_window(
        ctx.raw,
        start_s,
        end_s,
        signals=[expr.signal],
        max_dt_ratio=ctx.max_dt_ratio,
        min_points=ctx.min_points,
    )  # type: ignore[arg-type]
    if not check.ok:
        return _unknown(op, f"window_unusable:{check.reason}", check.detail)

    low = float(time_axis[0]) if start_s is None else start_s
    high = float(time_axis[-1]) if end_s is None else end_s
    mask = (time_axis >= low) & (time_axis <= high)
    window_t = time_axis[mask]
    window_v = values[mask]

    limit_low = float(expr.low)  # type: ignore[union-attr]
    limit_high = float(expr.high)  # type: ignore[union-attr]
    measured = {
        f"min({expr.signal})": float(np.min(window_v)),  # type: ignore[union-attr]
        f"max({expr.signal})": float(np.max(window_v)),  # type: ignore[union-attr]
        "lower": limit_low,
        "upper": limit_high,
        "samples": float(window_v.size),
    }
    below = window_v < limit_low
    above = window_v > limit_high
    ok = not bool(np.any(below) or np.any(above))
    if ok:
        detail = (
            f"{expr.signal} stayed within [{limit_low:.6g}, {limit_high:.6g}] {expr.unit} over "  # type: ignore[union-attr]
            f"[{low:g}, {high:g}] s (observed min {measured[f'min({expr.signal})']:.6g}, "
            f"max {measured[f'max({expr.signal})']:.6g})"
        )
        return _pass(op, detail, measured)
    if np.any(below):
        index = int(np.argmax(below))
        measured["worst_at_s"] = float(window_t[index])
        detail = (
            f"{expr.signal} fell to {float(window_v[index]):.6g} {expr.unit} at "  # type: ignore[union-attr]
            f"{float(window_t[index]):g} s, below the lower limit {limit_low:.6g}"
        )
    else:
        index = int(np.argmax(above))
        measured["worst_at_s"] = float(window_t[index])
        detail = (
            f"{expr.signal} rose to {float(window_v[index]):.6g} {expr.unit} at "  # type: ignore[union-attr]
            f"{float(window_t[index]):g} s, above the upper limit {limit_high:.6g}"
        )
    return _fail(op, detail, measured)


def _event_time(
    ctx: EvalContext, event: EventRef
) -> tuple[float | None, Verdict | None, np.ndarray | None, np.ndarray | None]:
    time_axis, values, failure = _signal(ctx, event.signal)
    if failure is not None:
        return None, failure, None, None
    assert time_axis is not None and values is not None
    crossing = find_crossing(
        time_axis, values, kind=event.kind, value=event.value, qualifier=event.qualifier
    )
    return crossing, None, time_axis, values


def _eval_event_window(expr: Expr, ctx: EvalContext, *, op: str, quantity: str) -> Verdict:
    blocked = ctx.blocked(op)
    if blocked is not None:
        return blocked
    assert ctx.raw is not None
    time_axis = ctx.raw.time_column()
    if time_axis is None:
        return _unknown(op, "no_time_axis", "the saved plot has no time axis")

    start_time, failure, _, _ = _event_time(ctx, expr.start)  # type: ignore[union-attr]
    if failure is not None:
        return failure
    end_time, failure, _, _ = _event_time(ctx, expr.end)  # type: ignore[union-attr]
    if failure is not None:
        return failure

    max_s = float(expr.max_s)  # type: ignore[union-attr]
    min_s = float(expr.min_s)  # type: ignore[union-attr]
    if start_time is None:
        # The trigger never happened. That is only conclusive when the run
        # actually covered a window in which it could have happened.
        diag = ctx.usability
        if diag is not None and diag.warnings and any("stopped at" in w for w in diag.warnings):
            return _unknown(
                op,
                "start_event_not_observed_truncated_run",
                f"{expr.start.signal} never crossed {expr.start.value} {expr.start.unit} "  # type: ignore[union-attr]
                f"({expr.start.kind}) and the run did not reach its stop time",  # type: ignore[union-attr]
            )
        return _fail(
            op,
            f"the start event ({expr.start.signal} {expr.start.kind} "  # type: ignore[union-attr]
            f"{expr.start.value} {expr.start.unit}) was never observed in the saved window "  # type: ignore[union-attr]
            f"[{float(time_axis[0]):g}, {float(time_axis[-1]):g}] s",
            {
                f"expected_start({expr.start.signal})": float(expr.start.value or 0.0),  # type: ignore[union-attr]
            },
        )
    requirement_end = start_time + max_s
    covers_requirement = requirement_end <= float(time_axis[-1]) * (1.0 - 1e-3)
    if end_time is None:
        if not covers_requirement:
            # Nothing was observed *and* the run stops before the allowed maximum,
            # so the delay is genuinely undecidable: the end event could still be
            # coming. This is the only case the coverage guard protects.
            return _unknown(
                op,
                "window_does_not_cover_max",
                f"the start event occurred at {start_time:g} s and the requirement allows up to "
                f"{max_s:g} s, but the run only reaches {float(time_axis[-1]):g} s",
            )
        measured = {
            f"{quantity}_s": f"not observed within {max_s:g} s",
            "start_s": start_time,
            "expected_min_s": min_s,
            "expected_max_s": max_s,
        }
        return _fail(
            op,
            f"the end event ({expr.end.signal} {expr.end.kind} {expr.end.value} "  # type: ignore[union-attr]
            f"{expr.end.unit}) was never observed within {max_s:g} s of the start at "  # type: ignore[union-attr]
            f"{start_time:g} s",
            measured,
        )

    # The end event was observed, so the quantity is measured and the verdict is
    # decidable even when the run stops before ``start + max_s``: a produced
    # observation must not be downgraded to UNKNOWN for want of extra window.
    measured_value = end_time - start_time
    measured = {
        f"{quantity}_s": measured_value,
        "start_s": start_time,
        "end_s": end_time,
        "expected_min_s": min_s,
        "expected_max_s": max_s,
    }
    detail = (
        f"{quantity} = {measured_value:.6g} s (start {start_time:.6g} s, end {end_time:.6g} s); "
        f"required [{min_s:g}, {max_s:g}] s"
    )
    if min_s <= measured_value <= max_s:
        return _pass(op, detail, measured, start_s=start_time, end_s=end_time)
    return _fail(op, detail, measured, start_s=start_time, end_s=end_time)


def _eval_event_delay(expr: Expr, ctx: EvalContext) -> Verdict:
    return _eval_event_window(expr, ctx, op="event_delay", quantity="delay")


def _eval_pulse_width(expr: Expr, ctx: EvalContext) -> Verdict:
    return _eval_event_window(expr, ctx, op="pulse_width", quantity="width")


def _eval_ordering(expr: Expr, ctx: EvalContext) -> Verdict:
    op = "ordering"
    blocked = ctx.blocked(op)
    if blocked is not None:
        return blocked
    first_time, failure, _, _ = _event_time(ctx, expr.first)  # type: ignore[union-attr]
    if failure is not None:
        return failure
    then_time, failure, _, _ = _event_time(ctx, expr.then)  # type: ignore[union-attr]
    if failure is not None:
        return failure
    assert ctx.raw is not None
    time_axis = ctx.raw.time_column()
    assert time_axis is not None
    measured: dict[str, float | str] = {
        "first_s": first_time if first_time is not None else "not observed",
        "then_s": then_time if then_time is not None else "not observed",
    }
    if first_time is None or then_time is None:
        missing = "first" if first_time is None else "then"
        event = expr.first if first_time is None else expr.then  # type: ignore[union-attr]
        if ctx.usability is not None and any("stopped at" in w for w in ctx.usability.warnings):
            return _unknown(
                op,
                "event_not_observed_truncated_run",
                f"the {missing} event ({event.signal} {event.kind} {event.value} {event.unit}) "
                "was not observed and the run did not reach its stop time",
            )
        return _fail(
            op,
            f"the {missing} event ({event.signal} {event.kind} {event.value} {event.unit}) was "
            f"never observed in the saved window [{float(time_axis[0]):g}, {float(time_axis[-1]):g}] s",
            measured,
        )
    order_ok = first_time < then_time
    detail = (
        f"{expr.first.signal} crossed at {first_time:.6g} s, {expr.then.signal} at "  # type: ignore[union-attr]
        f"{then_time:.6g} s (delta {then_time - first_time:.6g} s)"
    )
    measured["delta_s"] = then_time - first_time
    if order_ok:
        return _pass(op, detail, measured, first_s=first_time, then_s=then_time)
    return _fail(op, detail, measured, first_s=first_time, then_s=then_time)


def _eval_hold(expr: Expr, ctx: EvalContext) -> Verdict:
    op = "hold"
    time_axis, values, failure = _signal(ctx, expr.signal)  # type: ignore[union-attr]
    if failure is not None:
        return failure
    assert time_axis is not None and values is not None
    blocked = ctx.blocked(op)
    if blocked is not None:
        return blocked
    interval = getattr(expr, "interval", None)
    start_s, end_s = _bounds(ctx, interval)
    check = check_window(
        ctx.raw,
        start_s,
        end_s,
        signals=[expr.signal],
        max_dt_ratio=ctx.max_dt_ratio,
        min_points=ctx.min_points,
    )  # type: ignore[arg-type]
    if not check.ok:
        return _unknown(op, f"window_unusable:{check.reason}", check.detail)

    low = float(time_axis[0]) if start_s is None else start_s
    high = float(time_axis[-1]) if end_s is None else end_s
    mask = (time_axis >= low) & (time_axis <= high)
    window_t = time_axis[mask]
    window_v = values[mask]

    target = float(expr.value)  # type: ignore[union-attr]
    tolerance = ctx.hold_tolerance(target)
    deviation = np.abs(window_v - target)
    measured = {
        f"max_deviation({expr.signal})": float(np.max(deviation)),  # type: ignore[union-attr]
        "target": target,
        "tolerance": tolerance,
        "samples": float(window_v.size),
    }
    if expr.stable:  # type: ignore[union-attr]
        index = int(np.argmax(deviation))
        ok = bool(np.all(deviation <= tolerance))
        detail = (
            f"{expr.signal} deviated from {target:.6g} {expr.unit} by at most "  # type: ignore[union-attr]
            f"{float(np.max(deviation)):.6g} over [{low:g}, {high:g}] s "
            f"(tolerance {tolerance:.6g}; worst at {float(window_t[index]):g} s)"
        )
        return _pass(op, detail, measured) if ok else _fail(op, detail, measured)

    ok = bool(np.any(deviation <= tolerance))
    detail = (
        f"{expr.signal} came within {tolerance:.6g} of {target:.6g} {expr.unit} "  # type: ignore[union-attr]
        f"over [{low:g}, {high:g}] s: {ok} (closest {float(np.min(deviation)):.6g})"
    )
    return _pass(op, detail, measured) if ok else _fail(op, detail, measured)


def _eval_state_dependent(expr: Expr, ctx: EvalContext) -> Verdict:
    op = "state_dependent"
    blocked = ctx.blocked(op)
    if blocked is not None:
        return blocked
    when_time, failure, _, _ = _event_time(ctx, expr.when)  # type: ignore[union-attr]
    if failure is not None:
        return failure
    if when_time is None:
        # The precondition was never exercised: the dependent behaviour is not
        # applicable. Reported as such (and surfaced as a coverage gap), never as
        # a pass.
        return _na(
            op,
            f"the precondition ({expr.when.signal} {expr.when.kind} {expr.when.value} "  # type: ignore[union-attr]
            f"{expr.when.unit}) was never observed, so the dependent behaviour was not exercised",  # type: ignore[union-attr]
        )
    inner_window: IntervalSpec | None = None
    assert ctx.raw is not None
    time_axis = ctx.raw.time_column()
    if ctx.default_window is not None:
        if ctx.default_window.end_s > when_time:
            inner_window = IntervalSpec(start_s=when_time, end_s=ctx.default_window.end_s)
    elif time_axis is not None and float(time_axis[-1]) > when_time:
        # Gate the whole saved tail: the dependent behaviour is only required
        # once its precondition has actually happened.
        inner_window = IntervalSpec(start_s=when_time, end_s=float(time_axis[-1]))
    inner_ctx = EvalContext(
        raw=ctx.raw,
        usability=ctx.usability,
        connectivity=ctx.connectivity,
        supply_domains=ctx.supply_domains,
        default_window=inner_window,
        tolerances=ctx.tolerances,
        max_dt_ratio=ctx.max_dt_ratio,
        min_points=ctx.min_points,
    )
    inner = evaluate(expr.then, inner_ctx)  # type: ignore[union-attr]
    return Verdict(
        status=inner.status,
        detail=f"gated on {expr.when.signal} crossing at {when_time:.6g} s: {inner.detail}",  # type: ignore[union-attr]
        measured={**inner.measured, "gate_time_s": when_time},
        unknown_reason=inner.unknown_reason,
        blocked_reason=inner.blocked_reason,
        op=op,
    )


def _combine_all(items: Sequence[Verdict], op: str) -> Verdict:
    measured: dict[str, float | str] = {}
    for i, verdict in enumerate(items):
        for key, value in verdict.measured.items():
            measured[f"item{i}:{key}"] = value
    failures = [v for v in items if v.status is Status.FAIL]
    unknowns = [v for v in items if v.status is Status.UNKNOWN]
    blockeds = [v for v in items if v.status is Status.BLOCKED]
    detail = "; ".join(v.detail for v in items)
    if failures:
        return _fail(op, f"{len(failures)} of {len(items)} failed: {failures[0].detail}", measured)
    if blockeds:
        return _blocked(op, blockeds[0].blocked_reason or "blocked", blockeds[0].detail)
    if unknowns:
        return _unknown(op, unknowns[0].unknown_reason or "unknown", unknowns[0].detail, measured)
    return _pass(op, f"all {len(items)} conditions hold: {detail}", measured)


def _eval_all_of(expr: Expr, ctx: EvalContext) -> Verdict:
    return _combine_all([evaluate(item, ctx) for item in expr.items], "all_of")  # type: ignore[union-attr]


def _eval_any_of(expr: Expr, ctx: EvalContext) -> Verdict:
    op = "any_of"
    verdicts = [evaluate(item, ctx) for item in expr.items]  # type: ignore[union-attr]
    measured: dict[str, float | str] = {}
    for i, verdict in enumerate(verdicts):
        for key, value in verdict.measured.items():
            measured[f"item{i}:{key}"] = value
    passing = [v for v in verdicts if v.status is Status.PASS]
    if passing:
        return _pass(
            op, f"{len(passing)} of {len(verdicts)} conditions hold: {passing[0].detail}", measured
        )
    blockeds = [v for v in verdicts if v.status is Status.BLOCKED]
    if blockeds:
        return _blocked(op, blockeds[0].blocked_reason or "blocked", blockeds[0].detail)
    unknowns = [v for v in verdicts if v.status is Status.UNKNOWN]
    if unknowns:
        return _unknown(
            op,
            unknowns[0].unknown_reason or "unknown",
            f"no condition could be satisfied and {len(unknowns)} could not be evaluated: "
            f"{unknowns[0].detail}",
            measured,
        )
    return _fail(op, f"none of {len(verdicts)} conditions hold: {verdicts[0].detail}", measured)


def _eval_not(expr: Expr, ctx: EvalContext) -> Verdict:
    op = "not"
    inner = evaluate(expr.item, ctx)  # type: ignore[union-attr]
    if inner.status is Status.PASS:
        return _fail(
            op,
            f"the prohibited condition occurred: {inner.detail}",
            dict(inner.measured),
        )
    if inner.status is Status.FAIL:
        return _pass(
            op,
            f"the prohibited condition did not occur ({inner.detail})",
            dict(inner.measured),
        )
    return Verdict(
        status=inner.status,
        detail=f"cannot conclude the prohibited condition is absent: {inner.detail}",
        measured=dict(inner.measured),
        unknown_reason=inner.unknown_reason if inner.status is Status.UNKNOWN else None,
        blocked_reason=inner.blocked_reason if inner.status is Status.BLOCKED else None,
        op=op,
    )


# --------------------------------------------------------------------------- #
# connectivity ops


def _connectivity(ctx: EvalContext, op: str) -> Verdict | None:
    if ctx.connectivity is None:
        return _unknown(
            op,
            "connectivity_unavailable",
            "no netlist/connectivity model was provided for this evaluation",
        )
    return None


def _eval_net_equals(expr: Expr, ctx: EvalContext, *, expect_equal: bool) -> Verdict:
    op = "net_equals" if expect_equal else "net_not_equals"
    failure = _connectivity(ctx, op)
    if failure is not None:
        return failure
    assert ctx.connectivity is not None
    if not ctx.connectivity.has_refdes(expr.refdes):  # type: ignore[union-attr]
        return _unknown(
            op,
            "refdes_not_in_netlist",
            f"{expr.refdes} is not present in the netlist, so its pin {expr.pin} cannot be resolved",  # type: ignore[union-attr]
        )
    node = ctx.connectivity.node_of(expr.refdes, expr.pin)  # type: ignore[union-attr]
    if node is None:
        return _unknown(
            op,
            "pin_not_in_netlist",
            f"{expr.refdes}.{expr.pin} is not present in the netlist",  # type: ignore[union-attr]
        )
    measured = {
        "observed_net": node,
        "expected_net": expr.net,
        "refdes": expr.refdes,
        "pin": expr.pin,
    }  # type: ignore[union-attr]
    detail = f"{expr.refdes}.{expr.pin} is on net {node!r} (expected {expr.net!r})"  # type: ignore[union-attr]
    same = node == expr.net
    ok = same if expect_equal else not same
    return _pass(op, detail, measured) if ok else _fail(op, detail, measured)


def _eval_pullup_domain(expr: Expr, ctx: EvalContext) -> Verdict:
    op = "pullup_domain"
    failure = _connectivity(ctx, op)
    if failure is not None:
        return failure
    assert ctx.connectivity is not None
    if not ctx.connectivity.has_refdes(expr.refdes):  # type: ignore[union-attr]
        return _unknown(op, "refdes_not_in_netlist", f"{expr.refdes} is not in the netlist")  # type: ignore[union-attr]
    node = ctx.connectivity.node_of(expr.refdes, expr.pin)  # type: ignore[union-attr]
    if node is None:
        return _unknown(
            op,
            "pin_not_in_netlist",
            f"{expr.refdes}.{expr.pin} is not present in the netlist",  # type: ignore[union-attr]
        )
    if node != expr.net:  # type: ignore[union-attr]
        return _fail(
            op,
            f"{expr.refdes}.{expr.pin} is on net {node!r}, not the expected pull-up net {expr.net!r}",  # type: ignore[union-attr]
            {"observed_net": node, "expected_net": expr.net, "expected_domain": expr.domain},  # type: ignore[union-attr]
        )
    observed_domain = ctx.supply_domains.get(node)
    if observed_domain is None:
        return _unknown(
            op,
            "domain_unknown",
            f"no supply domain is declared for net {node!r}; known domains: "
            f"{sorted(ctx.supply_domains)}",
            {"observed_net": node},
        )
    measured = {
        "observed_net": node,
        "observed_domain": observed_domain,
        "expected_domain": expr.domain,  # type: ignore[union-attr]
        "pins_on_net": ", ".join(f"{r}.{p}" for r, p in ctx.connectivity.pins_on(node)),
    }
    detail = (
        f"{expr.refdes}.{expr.pin} is pulled up on {node!r}, whose domain is {observed_domain!r} "  # type: ignore[union-attr]
        f"(expected {expr.domain!r})"  # type: ignore[union-attr]
    )
    return (
        _pass(op, detail, measured)
        if observed_domain == expr.domain
        else _fail(op, detail, measured)
    )  # type: ignore[union-attr]


def _eval_pin_connected(expr: Expr, ctx: EvalContext, *, expect_connected: bool) -> Verdict:
    op = "pin_connected" if expect_connected else "pin_open"
    failure = _connectivity(ctx, op)
    if failure is not None:
        return failure
    assert ctx.connectivity is not None
    refdes, pin = expr.refdes, expr.pin  # type: ignore[union-attr]
    if not ctx.connectivity.has_refdes(refdes):
        return _unknown(op, "refdes_not_in_netlist", f"{refdes} is not present in the netlist")
    node = ctx.connectivity.node_of(refdes, pin)
    if node is None:
        # The part exists but the pin does not appear: that is an open pin for
        # a part whose pin list came from the netlist itself.
        measured = {"refdes": refdes, "pin": pin, "attached_net": "none"}
        if expect_connected:
            return _fail(
                op,
                f"{refdes}.{pin} is not attached to any net in the netlist",
                measured,
            )
        return _pass(op, f"{refdes}.{pin} is not attached to any net", measured)

    pins = [p for p in ctx.connectivity.pins_on(node) if p != (refdes, pin)]
    measured = {
        "refdes": refdes,
        "pin": pin,
        "attached_net": node,
        "other_pins": ", ".join(f"{r}.{p}" for r, p in pins),
    }
    connected = bool(pins)
    detail = (
        f"{refdes}.{pin} sits on net {node!r} with {len(pins)} other pin(s)"
        if connected
        else f"{refdes}.{pin} sits on net {node!r} alone, so it is effectively open"
    )
    ok = connected if expect_connected else not connected
    return _pass(op, detail, measured) if ok else _fail(op, detail, measured)


# --------------------------------------------------------------------------- #
# dispatch


def evaluate(expr: Expr, ctx: EvalContext) -> Verdict:
    """Evaluate one expression node. Unknown ``op`` values cannot occur (the AST
    is a closed discriminated union), but an unimplemented branch raises rather
    than returning a verdict."""
    op = str(expr.op)
    if op in ("lt", "le", "gt", "ge"):
        return _eval_comparison(expr, ctx)
    if op == "between":
        return _eval_between(expr, ctx)
    if op in ("rise_above", "fall_below"):
        # Existential: the signal must cross the value within the window.
        return _eval_crossing(expr, ctx)
    if op == "event_delay":
        return _eval_event_delay(expr, ctx)
    if op == "pulse_width":
        return _eval_pulse_width(expr, ctx)
    if op == "ordering":
        return _eval_ordering(expr, ctx)
    if op == "hold":
        return _eval_hold(expr, ctx)
    if op == "state_dependent":
        return _eval_state_dependent(expr, ctx)
    if op == "all_of":
        return _eval_all_of(expr, ctx)
    if op == "any_of":
        return _eval_any_of(expr, ctx)
    if op == "not":
        return _eval_not(expr, ctx)
    if op == "net_equals":
        return _eval_net_equals(expr, ctx, expect_equal=True)
    if op == "net_not_equals":
        return _eval_net_equals(expr, ctx, expect_equal=False)
    if op == "pullup_domain":
        return _eval_pullup_domain(expr, ctx)
    if op == "pin_connected":
        return _eval_pin_connected(expr, ctx, expect_connected=True)
    if op == "pin_open":
        return _eval_pin_connected(expr, ctx, expect_connected=False)
    raise NotImplementedError(f"no evaluator for op {op!r}")


def _eval_crossing(expr: Expr, ctx: EvalContext) -> Verdict:
    """``rise_above``/``fall_below``: a crossing must exist inside the window."""
    op = str(expr.op)
    time_axis, values, failure = _signal(ctx, expr.signal)
    if failure is not None:
        return failure
    assert time_axis is not None and values is not None
    blocked = ctx.blocked(op)
    if blocked is not None:
        return blocked
    interval = getattr(expr, "interval", None)
    start_s, end_s = _bounds(ctx, interval)
    check = check_window(
        ctx.raw,
        start_s,
        end_s,
        signals=[expr.signal],
        max_dt_ratio=ctx.max_dt_ratio,
        min_points=ctx.min_points,
    )  # type: ignore[arg-type]
    if not check.ok:
        return _unknown(op, f"window_unusable:{check.reason}", check.detail)

    low = float(time_axis[0]) if start_s is None else start_s
    high = float(time_axis[-1]) if end_s is None else end_s
    mask = (time_axis >= low) & (time_axis <= high)
    window_t = time_axis[mask]
    window_v = values[mask]
    crossing = find_crossing(
        window_t, window_v, kind=op, value=float(expr.value), qualifier="first"
    )
    measured = {
        f"{'max' if op == 'rise_above' else 'min'}({expr.signal})": float(
            np.max(window_v) if op == "rise_above" else np.min(window_v)
        ),
        "threshold": float(expr.value),
    }
    if crossing is not None:
        measured["crossing_s"] = crossing
        return _pass(
            op,
            f"{expr.signal} {op.replace('_', ' ')} {float(expr.value):.6g} {expr.unit} at "
            f"{crossing:.6g} s",
            measured,
            crossing_s=crossing,
        )
    extreme_key = f"{'max' if op == 'rise_above' else 'min'}({expr.signal})"
    return _fail(
        op,
        f"{expr.signal} never {op.replace('_', ' ')} {float(expr.value):.6g} {expr.unit} within "
        f"[{low:g}, {high:g}] s (observed extreme {measured[extreme_key]:.6g} over "
        f"{int(window_v.size)} samples)",
        measured,
    )
