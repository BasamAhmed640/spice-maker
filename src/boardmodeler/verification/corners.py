"""Corners: timestep refinement and enumerated-axis sweeps (Phase 1 step 8).

Two honesty rules drive this module:

* **No statistical inference from min/max.** A sweep visits the documented corner
  values (min/typ/max) one axis at a time and reports what each corner produced.
  Nothing is extrapolated, no distribution is assumed, and no "worst case" is
  fabricated beyond the corners that were actually run.
* **No temperature validation without modelled temperature dependence.** The
  capability probe decides; when the behaviour is not modelled, the temperature
  request is reported as UNKNOWN with the capability reason instead of running
  decks that cannot answer the question.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from boardmodeler.domain.enums import Status
from boardmodeler.domain.expressions import Expr, IntervalSpec
from boardmodeler.domain.records import Limit
from boardmodeler.pipeline.runner import RunArtifacts
from boardmodeler.verification.assertions import find_crossing

__all__ = [
    "RefinementResult",
    "SweepPoint",
    "SweepResult",
    "enumerated_axes",
    "events_from_expr",
    "signals_from_expr",
    "temperature_guard",
    "timestep_refinement",
    "worst_status",
]

_STATUS_SEVERITY: dict[Status, int] = {
    Status.BLOCKED: 4,
    Status.FAIL: 3,
    Status.UNKNOWN: 2,
    Status.NOT_APPLICABLE: 1,
    Status.PASS: 0,
}


def worst_status(statuses: Sequence[Status]) -> Status:
    """The most severe status present (FAIL beats UNKNOWN, BLOCKED beats FAIL)."""
    if not statuses:
        return Status.UNKNOWN
    return max(statuses, key=lambda status: _STATUS_SEVERITY[status])


# --------------------------------------------------------------------------- #
# expression introspection


def _walk(expr: Any) -> list[Any]:
    nodes: list[Any] = [expr]
    for attr in ("then", "item"):
        child = getattr(expr, attr, None)
        if child is not None and hasattr(child, "op"):
            nodes.extend(_walk(child))
    for child in getattr(expr, "items", []) or []:
        if hasattr(child, "op"):
            nodes.extend(_walk(child))
    return nodes


def signals_from_expr(expr: Expr) -> list[str]:
    """Every signal named by an expression, in first-seen order."""
    seen: list[str] = []
    for node in _walk(expr):
        for attr in ("signal", "start", "end", "when", "first", "then"):
            value = getattr(node, attr, None)
            name = value if isinstance(value, str) else getattr(value, "signal", None)
            if isinstance(name, str) and name not in seen:
                seen.append(name)
    return seen


def events_from_expr(expr: Expr) -> list[Any]:
    """Every event reference in an expression, in first-seen order."""
    events: list[Any] = []
    for node in _walk(expr):
        for attr in ("start", "end", "when", "first", "then"):
            event = getattr(node, attr, None)
            if (
                event is not None
                and hasattr(event, "kind")
                and hasattr(event, "signal")
                and event not in events
            ):
                events.append(event)
    return events


def _window_of(expr: Expr) -> IntervalSpec | None:
    for node in _walk(expr):
        interval = getattr(node, "interval", None)
        if interval is not None:
            return interval
    return None


# --------------------------------------------------------------------------- #
# timestep refinement


@dataclass(frozen=True)
class RefinementResult:
    """Whether a result survives a finer time step."""

    status: Status
    detail: str
    ratio: float
    coarse_max_dt_s: float | None = None
    fine_max_dt_s: float | None = None
    time_deviation_pct: float | None = None
    value_deviation_pct: float | None = None
    per_event: dict[str, float] = field(default_factory=dict)
    per_signal: dict[str, float] = field(default_factory=dict)

    def as_row(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "detail": self.detail,
            "ratio": self.ratio,
            "coarse_max_dt_s": self.coarse_max_dt_s,
            "fine_max_dt_s": self.fine_max_dt_s,
            "time_deviation_pct": self.time_deviation_pct,
            "value_deviation_pct": self.value_deviation_pct,
        }


def timestep_refinement(
    expr: Expr,
    coarse: RunArtifacts,
    rerun: Callable[[float], RunArtifacts],
    *,
    ratio: float = 0.25,
    tol_time_pct: float = 2.0,
    tol_value_pct: float = 1.0,
) -> RefinementResult:
    """Re-run with a smaller maximum time step and compare events and signals.

    ``rerun(max_timestep_s)`` must produce a fresh run of the same case with the
    given maximum time step. PASS means the quantities the requirement depends on
    moved by less than the declared tolerances — i.e. the result is not an
    artifact of the time step. UNKNOWN means the comparison could not be made
    (missing signals/events or an unusable run); FAIL means the result is
    time-step dependent.
    """
    if not 0.0 < ratio < 1.0:
        raise ValueError("ratio must be in (0, 1)")

    if not coarse.usability.usable or coarse.raw is None:
        return RefinementResult(
            status=Status.BLOCKED,
            detail=f"the coarse run is unusable: {coarse.blocked_reason or 'no waveform'}",
            ratio=ratio,
        )

    coarse_dt = _max_dt(coarse)
    if coarse_dt is None:
        return RefinementResult(
            status=Status.UNKNOWN,
            detail="the coarse run has no time axis, so no time step could be refined",
            ratio=ratio,
        )

    fine = rerun(coarse_dt * ratio)
    if not fine.usability.usable or fine.raw is None:
        return RefinementResult(
            status=Status.BLOCKED,
            detail=f"the refined run is unusable: {fine.blocked_reason or 'no waveform'}",
            ratio=ratio,
            coarse_max_dt_s=coarse_dt,
        )
    fine_dt = _max_dt(fine)

    event_deviations: dict[str, float] = {}
    signal_deviations: dict[str, float] = {}
    window = _window_of(expr)
    span = (window.end_s - window.start_s) if window is not None else None

    assert coarse.raw is not None and fine.raw is not None
    for event in events_from_expr(expr):
        name = f"{event.signal}:{event.kind}:{event.value}"
        if not (coarse.raw.has(event.signal) and fine.raw.has(event.signal)):
            return RefinementResult(
                status=Status.UNKNOWN,
                detail=(
                    f"{event.signal} is not saved in both runs, so the event "
                    f"{name!r} could not be compared"
                ),
                ratio=ratio,
                coarse_max_dt_s=coarse_dt,
                fine_max_dt_s=fine_dt,
            )
        coarse_t = coarse.raw.time_column()
        fine_t = fine.raw.time_column()
        if coarse_t is None or fine_t is None:
            return RefinementResult(
                status=Status.UNKNOWN,
                detail="one of the runs has no time axis",
                ratio=ratio,
                coarse_max_dt_s=coarse_dt,
                fine_max_dt_s=fine_dt,
            )
        t_coarse = find_crossing(
            coarse_t,
            coarse.raw.column(event.signal),
            kind=event.kind,
            value=event.value,
            qualifier=event.qualifier,
        )
        t_fine = find_crossing(
            fine_t,
            fine.raw.column(event.signal),
            kind=event.kind,
            value=event.value,
            qualifier=event.qualifier,
        )
        if t_coarse is None or t_fine is None:
            return RefinementResult(
                status=Status.UNKNOWN,
                detail=(
                    f"the event {name!r} was observed in only one of the two runs "
                    f"(coarse={t_coarse}, fine={t_fine}); the result is not comparable"
                ),
                ratio=ratio,
                coarse_max_dt_s=coarse_dt,
                fine_max_dt_s=fine_dt,
            )
        scale = span if span else float(coarse_t[-1])
        deviation = abs(t_fine - t_coarse) / scale * 100.0 if scale else 0.0
        event_deviations[name] = deviation

    for signal in signals_from_expr(expr):
        if not (coarse.raw.has(signal) and fine.raw.has(signal)):
            return RefinementResult(
                status=Status.UNKNOWN,
                detail=f"{signal} is not saved in both runs, so values could not be compared",
                ratio=ratio,
                coarse_max_dt_s=coarse_dt,
                fine_max_dt_s=fine_dt,
            )
        coarse_t = coarse.raw.time_column()
        fine_t = fine.raw.time_column()
        assert coarse_t is not None and fine_t is not None
        probe = coarse_t
        interpolated = np.interp(probe, fine_t, fine.raw.column(signal))
        reference = coarse.raw.column(signal)
        scale = max(float(np.max(np.abs(reference))), 1e-12)
        signal_deviations[signal] = float(np.max(np.abs(interpolated - reference))) / scale * 100.0

    worst_time = max(event_deviations.values(), default=0.0)
    worst_value = max(signal_deviations.values(), default=0.0)
    detail = (
        f"re-ran with max timestep {coarse_dt * ratio:.3e} s (ratio {ratio}); "
        f"max event-time deviation {worst_time:.3f}% (limit {tol_time_pct}%), "
        f"max signal deviation {worst_value:.3f}% (limit {tol_value_pct}%)"
    )
    ok = worst_time <= tol_time_pct and worst_value <= tol_value_pct
    return RefinementResult(
        status=Status.PASS if ok else Status.FAIL,
        detail=detail,
        ratio=ratio,
        coarse_max_dt_s=coarse_dt,
        fine_max_dt_s=fine_dt,
        time_deviation_pct=worst_time,
        value_deviation_pct=worst_value,
        per_event=event_deviations,
        per_signal=signal_deviations,
    )


def _max_dt(artifacts: RunArtifacts) -> float | None:
    if artifacts.raw is None:
        return None
    time_axis = artifacts.raw.time_column()
    if time_axis is None or time_axis.size < 2:
        return None
    return float(np.max(np.diff(time_axis)))


# --------------------------------------------------------------------------- #
# enumerated-axis sweeps


@dataclass(frozen=True)
class SweepPoint:
    """One corner of a sweep and what it produced."""

    corner_id: str
    parameters: dict[str, float]
    status: Status
    measured: dict[str, float | str] = field(default_factory=dict)
    detail: str = ""


@dataclass(frozen=True)
class SweepResult:
    """All corners of one axis, with the worst status observed."""

    axis: str
    points: list[SweepPoint]
    worst: Status
    detail: str

    @property
    def failures(self) -> list[SweepPoint]:
        return [p for p in self.points if p.status is not Status.PASS]


def enumerated_axes(limits: Mapping[str, Limit]) -> list[dict[str, float]]:
    """Corner values from documented limits: one axis at a time, others at ``typ``.

    Only the values the document actually states are used (``min``/``typ``/``max``).
    No distribution, no interpolation, and no combination of two axes unless the
    caller asks for it explicitly.
    """
    corners: list[dict[str, float]] = []
    baseline: dict[str, float] = {}
    for name, limit in limits.items():
        centre = (
            limit.typ
            if limit.typ is not None
            else limit.min
            if limit.min is not None
            else limit.max
        )
        if centre is not None:
            baseline[name] = float(centre)

    for name, limit in limits.items():
        centre = baseline.get(name)
        for value in (limit.min, limit.typ, limit.max):
            if value is None or (centre is not None and value == centre):
                continue
            point = dict(baseline)
            point[name] = float(value)
            corners.append(point)
    return corners


def sweep(
    axis: str,
    corners: Sequence[Mapping[str, float]],
    run_at: Callable[[Mapping[str, float]], tuple[Status, Mapping[str, float | str], str]],
) -> SweepResult:
    """Run each corner and report every result. The worst status is summarised.

    ``run_at`` returns ``(status, measured, detail)`` for one corner; it is the
    caller's job to actually run something. A corner that could not run reports
    BLOCKED and blocks the sweep summary rather than being dropped.
    """
    points: list[SweepPoint] = []
    for index, parameters in enumerate(corners):
        corner_id = ",".join(f"{name}={value:g}" for name, value in sorted(parameters.items()))
        try:
            status, measured, detail = run_at(parameters)
        except Exception as exc:  # a corner that raises cannot be counted as a pass
            points.append(
                SweepPoint(
                    corner_id=corner_id or f"{axis}[{index}]",
                    parameters=dict(parameters),
                    status=Status.BLOCKED,
                    detail=f"the corner did not run: {type(exc).__name__}: {exc}",
                )
            )
            continue
        points.append(
            SweepPoint(
                corner_id=corner_id or f"{axis}[{index}]",
                parameters=dict(parameters),
                status=status,
                measured=dict(measured),
                detail=detail,
            )
        )

    worst = worst_status([point.status for point in points])
    failures = [p for p in points if p.status is not Status.PASS]
    detail = f"{len(points)} {axis} corner(s) evaluated; worst status {worst.value}" + (
        f"; non-passing: {', '.join(p.corner_id for p in failures[:4])}" if failures else ""
    )
    return SweepResult(axis=axis, points=points, worst=worst, detail=detail)


def temperature_guard(behaviors: Mapping[str, str]) -> str | None:
    """Why temperature corners cannot be claimed, or ``None`` when they can.

    Returns a reason unless the model's capability record says temperature
    dependence is ``supported`` — the guard exists so that a request for
    temperature validation produces an explicit UNKNOWN instead of a silent
    PASS (plan A12).
    """
    state = behaviors.get("thermal_dependence", "unknown")
    if state == "supported":
        return None
    return (
        "temperature_validation_unavailable: the model's capability record reports "
        f"thermal_dependence={state!r}, so no temperature corner can be claimed"
    )
