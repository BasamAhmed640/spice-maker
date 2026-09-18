"""Pre-assertion validity checks (Phase 1 step 3).

Every check here exists to stop a *vacuous pass*: a verdict computed from data
that cannot support it. Failure of a check yields UNKNOWN (or BLOCKED for a
missing/failed simulation), never PASS.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from boardmodeler.domain.enums import Status
from boardmodeler.simulation.measures import RunDiagnostics
from boardmodeler.simulation.raw import RawFile

__all__ = [
    "DEFAULT_MAX_DT_RATIO",
    "DEFAULT_MIN_POINTS",
    "RunUsability",
    "WindowCheck",
    "check_run_usable",
    "check_window",
    "finite_columns",
    "missing_signals",
    "time_step_stats",
]

DEFAULT_MAX_DT_RATIO = 0.05
"""A window must contain at least ~20 solver steps for its timing to mean anything."""

DEFAULT_MIN_POINTS = 8


def missing_signals(raw: RawFile, signals: Sequence[str]) -> list[str]:
    """Signals a test needs but the saved waveform does not contain."""
    return [name for name in signals if not raw.has(name)]


def finite_columns(raw: RawFile) -> list[str]:
    """Names of columns containing non-finite values (empty when data is clean)."""
    bad: list[str] = []
    for i, name in enumerate(raw.variables):
        column = raw.data[:, i]
        if not np.all(np.isfinite(column)):
            bad.append(name)
    return bad


@dataclass(frozen=True)
class TimeStepStats:
    """Resolution of the saved waveform inside a window."""

    max_dt_s: float
    median_dt_s: float
    points: int
    span_s: float


def time_step_stats(time_axis: np.ndarray, start_s: float, end_s: float) -> TimeStepStats:
    """Time-step statistics for the samples inside ``[start_s, end_s]``."""
    mask = (time_axis >= start_s) & (time_axis <= end_s)
    window = time_axis[mask]
    if window.size == 0:
        return TimeStepStats(max_dt_s=0.0, median_dt_s=0.0, points=0, span_s=0.0)
    span = float(window[-1] - window[0])
    if window.size < 2:
        return TimeStepStats(max_dt_s=span, median_dt_s=span, points=1, span_s=span)
    deltas = np.diff(window)
    return TimeStepStats(
        max_dt_s=float(np.max(deltas)),
        median_dt_s=float(np.median(deltas)),
        points=int(window.size),
        span_s=span,
    )


@dataclass(frozen=True)
class WindowCheck:
    """Whether an assertion window can support a verdict."""

    ok: bool
    reason: Literal[
        "ok",
        "no_time_axis",
        "no_samples",
        "window_not_covered",
        "insufficient_points",
        "too_coarse",
        "missing_signals",
        "non_finite",
    ]
    detail: str
    coverage_fraction: float = 0.0
    max_dt_s: float | None = None
    points_in_window: int = 0

    @property
    def status(self) -> Status:
        """UNKNOWN for an unusable window; the caller decides PASS/FAIL only when ok."""
        return Status.PASS if self.ok else Status.UNKNOWN


def check_window(
    raw: RawFile,
    start_s: float | None,
    end_s: float | None,
    *,
    signals: Sequence[str] = (),
    max_dt_ratio: float = DEFAULT_MAX_DT_RATIO,
    min_points: int = DEFAULT_MIN_POINTS,
) -> WindowCheck:
    """Validate that ``[start_s, end_s]`` is covered with usable resolution.

    ``None`` bounds mean "the whole saved window". A window whose end exceeds the
    saved data is *not* covered (that is exactly the truncated-run case), and a
    window with too few samples is UNKNOWN rather than a lucky pass.
    """
    missing = missing_signals(raw, signals)
    if missing:
        return WindowCheck(
            ok=False,
            reason="missing_signals",
            detail=f"waveform does not contain {missing}; saved variables are {raw.variables}",
        )
    non_finite = finite_columns(raw)
    if non_finite:
        return WindowCheck(
            ok=False,
            reason="non_finite",
            detail=f"non-finite values in {non_finite}; the run cannot be evaluated",
        )

    time_axis = raw.time_column()
    if time_axis is None or time_axis.size == 0:
        return WindowCheck(
            ok=False, reason="no_time_axis", detail="the saved plot has no time axis"
        )

    window_start = float(time_axis[0]) if start_s is None else float(start_s)
    window_end = float(time_axis[-1]) if end_s is None else float(end_s)
    if window_end <= window_start:
        return WindowCheck(
            ok=False,
            reason="no_samples",
            detail=f"window [{window_start}, {window_end}] has no positive width",
        )

    stats = time_step_stats(time_axis, window_start, window_end)
    coverage = stats.span_s / (window_end - window_start) if window_end > window_start else 0.0
    if stats.points == 0:
        return WindowCheck(
            ok=False,
            reason="window_not_covered",
            detail=(
                f"no saved samples in [{window_start}, {window_end}]; "
                f"saved range is [{float(time_axis[0])}, {float(time_axis[-1])}]"
            ),
            coverage_fraction=0.0,
            points_in_window=0,
        )
    # Coverage is judged against the solver's own step size: the last sample
    # inside the window is normally up to one step short of the window end, which
    # is not a truncation. A window that reaches past the saved data is.
    slack = max(stats.max_dt_s, abs(window_end) * 1e-9, 1e-15) * 1.5
    mask = (time_axis >= window_start) & (time_axis <= window_end)
    inside = time_axis[mask]
    first_inside = float(inside[0])
    last_inside = float(inside[-1])
    start_gap = first_inside - window_start
    end_gap = window_end - last_inside
    if start_gap > slack or end_gap > slack:
        return WindowCheck(
            ok=False,
            reason="window_not_covered",
            detail=(
                f"the saved data covers [{float(time_axis[0])}, {float(time_axis[-1])}] s, which "
                f"does not cover the requested window [{window_start}, {window_end}] s "
                f"(start gap {start_gap:.3e} s, end gap {end_gap:.3e} s, step slack {slack:.3e} s)"
            ),
            coverage_fraction=coverage,
            max_dt_s=stats.max_dt_s,
            points_in_window=stats.points,
        )
    if stats.points < min_points:
        return WindowCheck(
            ok=False,
            reason="insufficient_points",
            detail=(
                f"only {stats.points} saved samples in [{window_start}, {window_end}]; "
                f"at least {min_points} are required"
            ),
            coverage_fraction=coverage,
            max_dt_s=stats.max_dt_s,
            points_in_window=stats.points,
        )
    allowed_dt = max_dt_ratio * (window_end - window_start)
    if stats.max_dt_s > allowed_dt:
        return WindowCheck(
            ok=False,
            reason="too_coarse",
            detail=(
                f"largest saved time step {stats.max_dt_s:.3e} s exceeds "
                f"{max_dt_ratio:.0%} of the window {window_end - window_start:.3e} s "
                f"(limit {allowed_dt:.3e} s); the timing assertion would be meaningless"
            ),
            coverage_fraction=coverage,
            max_dt_s=stats.max_dt_s,
            points_in_window=stats.points,
        )
    return WindowCheck(
        ok=True,
        reason="ok",
        detail=(
            f"{stats.points} samples over [{window_start}, {window_end}] s, "
            f"max dt {stats.max_dt_s:.3e} s (limit {allowed_dt:.3e} s)"
        ),
        coverage_fraction=coverage,
        max_dt_s=stats.max_dt_s,
        points_in_window=stats.points,
    )


@dataclass(frozen=True)
class RunUsability:
    """Whether a run may be evaluated at all, and if not, why."""

    usable: bool
    blocked_reason: str | None = None
    detail: str = ""
    warnings: list[str] = field(default_factory=list)


def check_run_usable(diagnostics: RunDiagnostics, raw: RawFile | None) -> RunUsability:
    """Simulator-level validity: BLOCKED when the tool, not the design, failed."""
    blocked = diagnostics.blocked_reason()
    if blocked:
        return RunUsability(usable=False, blocked_reason=blocked, detail=diagnostics.summary())
    if raw is None:
        return RunUsability(
            usable=False,
            blocked_reason="sim_output_missing: the run completed but produced no readable .raw",
            detail=diagnostics.summary(),
        )
    bad = finite_columns(raw)
    if bad:
        return RunUsability(
            usable=False,
            blocked_reason=f"sim_output_non_finite: {bad}",
            detail=diagnostics.summary(),
        )
    warnings = list(diagnostics.warnings)
    if diagnostics.truncated:
        warnings.append(
            f"the run stopped at {diagnostics.reached_s} s before the declared stop time "
            f"{diagnostics.expected_stop_s} s"
        )
    return RunUsability(usable=True, detail=diagnostics.summary(), warnings=warnings)
