"""Corners tests (Phase 1 step 8).

Refinement must catch a time-step-dependent result, sweeps must report every
corner they ran (including ones that failed to run), and temperature claims must
be refused when the model does not model temperature.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pytest

from boardmodeler.domain.enums import Status
from boardmodeler.domain.expressions import parse_expr
from boardmodeler.domain.records import Limit
from boardmodeler.pipeline.runner import RunArtifacts
from boardmodeler.simulation.limits import RunUsability
from boardmodeler.simulation.log import LogSummary
from boardmodeler.simulation.measures import RunDiagnostics
from boardmodeler.simulation.raw import RawFile
from boardmodeler.verification.corners import (
    enumerated_axes,
    events_from_expr,
    signals_from_expr,
    sweep,
    temperature_guard,
    timestep_refinement,
    worst_status,
)


def make_artifacts(
    *,
    rise_start: float = 5e-4,
    rise_time: float = 1e-4,
    steps: int = 2001,
    usable: bool = True,
    peak: float = 3.3,
) -> RunArtifacts:
    t = np.linspace(0.0, 1e-3, steps)
    out = np.clip((t - rise_start) / rise_time, 0.0, 1.0) * peak
    raw = RawFile(
        path=None,
        plotname="Transient Analysis",
        flags=["real"],
        variables=["time", "V(out)"],
        variable_types=["time", "voltage"],
        data=np.column_stack([t, out]),
        points_per_step=[t.size],
    )
    return RunArtifacts(
        run_id="run_ref",
        test_id="T_ref",
        scenario_id="nominal_startup",
        run_dir=Path("runs/run_ref"),
        deck_path=Path("runs/run_ref/deck.cir"),
        deck_text="* deck\n.end\n",
        deck_sha256="0" * 64,
        batch=None,
        log=LogSummary(path=None),
        raw=raw,
        raw_error=None,
        diagnostics=RunDiagnostics(completed=True, reached_s=1e-3, expected_stop_s=1e-3),
        usability=RunUsability(usable=usable, blocked_reason=None if usable else "sim_unusable"),
    )


DELAY_EXPR = parse_expr(
    {
        "op": "event_delay",
        "start": {"signal": "V(out)", "kind": "rise_above", "value": 1.65, "unit": "V"},
        "end": {"signal": "V(out)", "kind": "rise_above", "value": 3.0, "unit": "V"},
        "min_s": 0.0,
        "max_s": 1e-3,
    }
)


def test_expression_introspection_finds_signals_and_events() -> None:
    expr = parse_expr(
        {
            "op": "all_of",
            "items": [
                {"op": "gt", "signal": "V(a)", "value": 1.0, "unit": "V"},
                {
                    "op": "ordering",
                    "first": {"signal": "V(b)", "kind": "rise_above", "value": 1.0, "unit": "V"},
                    "then": {"signal": "V(c)", "kind": "fall_below", "value": 0.5, "unit": "V"},
                },
            ],
        }
    )
    assert signals_from_expr(expr) == ["V(a)", "V(b)", "V(c)"]
    events = events_from_expr(expr)
    assert [e.signal for e in events] == ["V(b)", "V(c)"]


def test_refinement_passes_when_the_result_is_stable() -> None:
    coarse = make_artifacts()
    fine = make_artifacts(rise_start=5e-4 + 1e-7)  # 0.01% of the window
    result = timestep_refinement(DELAY_EXPR, coarse, lambda _dt: fine)
    assert result.status is Status.PASS, result.detail
    assert result.time_deviation_pct is not None and result.time_deviation_pct < 2.0
    assert result.coarse_max_dt_s is not None and result.fine_max_dt_s is not None


def test_refinement_fails_when_the_result_depends_on_the_timestep() -> None:
    coarse = make_artifacts(rise_start=5e-4)
    fine = make_artifacts(rise_start=5.5e-4)  # 5% of the window
    result = timestep_refinement(DELAY_EXPR, coarse, lambda _dt: fine)
    assert result.status is Status.FAIL
    assert result.time_deviation_pct is not None and result.time_deviation_pct > 2.0
    assert result.per_event


def test_refinement_is_unknown_when_an_event_only_appears_in_one_run() -> None:
    coarse = make_artifacts()
    # The fine run never reaches 3.0 V, so the end event is absent.
    result = timestep_refinement(DELAY_EXPR, coarse, lambda _dt: make_artifacts(peak=2.0))
    assert result.status is Status.UNKNOWN
    assert "observed in only one of the two runs" in result.detail


def test_refinement_blocks_when_a_run_is_unusable() -> None:
    coarse = make_artifacts(usable=False)
    assert (
        timestep_refinement(DELAY_EXPR, coarse, lambda _dt: make_artifacts()).status
        is Status.BLOCKED
    )
    result = timestep_refinement(
        DELAY_EXPR, make_artifacts(), lambda _dt: make_artifacts(usable=False)
    )
    assert result.status is Status.BLOCKED


def test_refinement_rejects_a_bad_ratio() -> None:
    with pytest.raises(ValueError):
        timestep_refinement(DELAY_EXPR, make_artifacts(), lambda _dt: make_artifacts(), ratio=1.5)


# --------------------------------------------------------------------------- #
# enumerated sweeps


def test_enumerated_axes_visits_documented_values_one_axis_at_a_time() -> None:
    limits = {
        "r_upper": Limit(min=26.0e3, typ=26.364e3, max=27.0e3, unit="ohm"),
        "temperature_c": Limit(min=-40.0, typ=25.0, max=125.0, unit="C"),
    }
    corners = enumerated_axes(limits)
    assert len(corners) == 4  # min+max for each axis; typ is the baseline
    for corner in corners:
        # Only one axis deviates from its typ value at a time.
        changed = [name for name, value in corner.items() if value != limits[name].typ]
        assert len(changed) == 1
    values = {frozenset(c.items()) for c in corners}
    assert len(values) == 4


def test_enumerated_axes_skips_missing_and_typ_values() -> None:
    corners = enumerated_axes({"vin": Limit(min=None, typ=12.0, max=12.0, unit="V")})
    assert corners == []


def test_sweep_reports_worst_status_and_keeps_every_corner() -> None:
    corners = [{"r": 26.0e3}, {"r": 26.364e3}, {"r": 27.0e3}]
    statuses = [Status.PASS, Status.PASS, Status.FAIL]

    def run_at(parameters: Mapping[str, float]):
        index = corners.index(parameters)
        return statuses[index], {"vout": 3.3 - index * 0.1}, f"corner {index}"

    result = sweep("r", corners, run_at)
    assert result.worst is Status.FAIL
    assert len(result.points) == 3
    assert len(result.failures) == 1
    assert "r=27000" in result.failures[0].corner_id


def test_sweep_marks_a_raising_corner_as_blocked_not_skipped() -> None:
    def run_at(parameters: Mapping[str, float]):
        if parameters["r"] == 27.0e3:
            raise RuntimeError("deck could not be built")
        return Status.PASS, {"vout": 3.3}, "ok"

    result = sweep("r", [{"r": 26.0e3}, {"r": 27.0e3}], run_at)
    assert result.worst is Status.BLOCKED
    assert len(result.points) == 2
    assert "did not run" in result.points[1].detail


def test_worst_status_ordering() -> None:
    assert worst_status([Status.PASS, Status.FAIL]) is Status.FAIL
    assert worst_status([Status.FAIL, Status.BLOCKED]) is Status.BLOCKED
    assert worst_status([Status.PASS, Status.UNKNOWN]) is Status.UNKNOWN
    assert worst_status([Status.NOT_APPLICABLE, Status.PASS]) is Status.NOT_APPLICABLE
    assert worst_status([]) is Status.UNKNOWN


def test_temperature_guard_refuses_unless_the_model_models_temperature() -> None:
    assert temperature_guard({}) is not None
    reason = temperature_guard({"thermal_dependence": "unsupported"})
    assert reason is not None and reason.startswith("temperature_validation_unavailable")
    assert temperature_guard({"thermal_dependence": "supported"}) is None
