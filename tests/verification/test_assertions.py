"""Evaluation tests (Phase 1 steps 4-5).

Every op in the D5 union is exercised with synthetic waveforms whose answers are
known by construction, together with the guards that stop a vacuous pass. A bug
in interpolation, first/last selection, gating, or window validation fails here.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from boardmodeler.domain.enums import Status
from boardmodeler.domain.expressions import parse_expr
from boardmodeler.simulation.limits import RunUsability
from boardmodeler.simulation.raw import RawFile
from boardmodeler.verification.assertions import EvalContext, evaluate, find_crossing


def make_raw(t: np.ndarray, **signals: np.ndarray) -> RawFile:
    """Build a RawFile from a time axis and named signals."""
    variables = ["time", *signals]
    data = np.column_stack([t, *(np.asarray(v, dtype=float) for v in signals.values())])
    types = ["time"] + ["voltage"] * len(signals)
    return RawFile(
        path=None,
        plotname="Transient Analysis",
        flags=["real", "forward"],
        variables=variables,
        variable_types=types,
        data=data,
        points_per_step=[int(t.size)],
    )


def good_raw() -> RawFile:
    """A 1 ms run with a 0->1 V ramp (1 ms) and a 0.5 ms-delayed follower."""
    t = np.linspace(0.0, 1e-3, 2001)
    ramp = np.clip(t / 1e-3, 0.0, 1.0)
    delayed = np.clip((t - 5e-4) / 5e-4, 0.0, 1.0)
    return make_raw(t, **{"V(in)": ramp, "V(out)": delayed, "V(3V3)": np.full_like(t, 3.3)})


def ctx(raw: RawFile | None = None, **kwargs: object) -> EvalContext:
    usability = kwargs.pop("usability", RunUsability(usable=True))
    return EvalContext(raw=raw, usability=usability, **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# interpolation and crossing selection


def test_find_crossing_interpolates_exactly() -> None:
    t = np.array([0.0, 1e-3])
    v = np.array([0.0, 1.0])
    assert find_crossing(t, v, kind="rise_above", value=0.5) == pytest.approx(5e-4, abs=1e-15)
    assert find_crossing(t, v, kind="fall_below", value=0.5) is None


def test_find_crossing_first_and_last_selection() -> None:
    t = np.array([0.0, 1e-3, 2e-3, 3e-3])
    v = np.array([0.0, 1.0, 0.0, 1.0])
    assert find_crossing(t, v, kind="rise_above", value=0.5, qualifier="first") == pytest.approx(
        5e-4
    )
    assert find_crossing(t, v, kind="rise_above", value=0.5, qualifier="last") == pytest.approx(
        2.5e-3
    )
    assert find_crossing(t, v, kind="fall_below", value=0.5, qualifier="first") == pytest.approx(
        1.5e-3
    )
    assert find_crossing(t, v, kind="fall_below", value=0.5, qualifier="last") == pytest.approx(
        1.5e-3
    )


def test_find_crossing_eq_window_is_the_window_start() -> None:
    t = np.array([1e-4, 2e-4, 3e-4])
    assert find_crossing(t, np.array([0.0, 0.0, 0.0]), kind="eq_window", value=None) == 1e-4


def test_find_crossing_ignores_equality_at_the_start() -> None:
    """Merely sitting at the threshold is not a crossing."""
    t = np.array([0.0, 1e-3, 2e-3])
    assert find_crossing(t, np.array([0.5, 0.5, 0.5]), kind="rise_above", value=0.5) is None
    assert find_crossing(t, np.array([0.5, 0.5, 0.5]), kind="fall_below", value=0.5) is None
    # Exceeding it does start the rising segment at the last at-threshold sample.
    assert find_crossing(
        t, np.array([0.5, 0.5, 0.6]), kind="rise_above", value=0.5
    ) == pytest.approx(1e-3)
    assert find_crossing(t, np.array([0.5, 0.5, 0.6]), kind="rise_above", value=0.6) is None


# --------------------------------------------------------------------------- #
# comparisons are universal over the interval


def test_gt_passes_and_reports_the_worst_sample() -> None:
    raw = make_raw(np.linspace(0, 1e-3, 100), **{"V(out)": np.full(100, 3.3)})
    verdict = evaluate(
        parse_expr({"op": "gt", "signal": "V(out)", "value": 3.0, "unit": "V"}), ctx(raw)
    )
    assert verdict.status is Status.PASS
    assert verdict.measured["min(V(out))"] == pytest.approx(3.3)


def test_gt_fails_when_one_sample_dips_below() -> None:
    t = np.linspace(0, 1e-3, 100)
    signal = np.full(100, 3.3)
    signal[57] = 2.9
    verdict = evaluate(
        parse_expr({"op": "gt", "signal": "V(out)", "value": 3.0, "unit": "V"}),
        ctx(make_raw(t, **{"V(out)": signal})),
    )
    assert verdict.status is Status.FAIL
    assert verdict.measured["min(V(out))"] == pytest.approx(2.9)
    assert verdict.measured["at_s"] == pytest.approx(t[57])
    assert "2.9" in verdict.detail


def test_le_fails_on_a_single_excursion() -> None:
    t = np.linspace(0, 1e-3, 50)
    signal = np.full(50, 0.3)
    signal[10] = 0.45
    verdict = evaluate(
        parse_expr({"op": "le", "signal": "V(pg)", "value": 0.4, "unit": "V"}),
        ctx(make_raw(t, **{"V(pg)": signal})),
    )
    assert verdict.status is Status.FAIL
    assert verdict.measured["max(V(pg))"] == pytest.approx(0.45)


def test_between_checks_both_limits() -> None:
    t = np.linspace(0, 1e-3, 100)
    inside = np.full(100, 3.3)
    verdict = evaluate(
        parse_expr({"op": "between", "signal": "V(3V3)", "low": 3.234, "high": 3.366, "unit": "V"}),
        ctx(make_raw(t, **{"V(3V3)": inside})),
    )
    assert verdict.status is Status.PASS

    high = inside.copy()
    high[-1] = 3.4
    verdict = evaluate(
        parse_expr({"op": "between", "signal": "V(3V3)", "low": 3.234, "high": 3.366, "unit": "V"}),
        ctx(make_raw(t, **{"V(3V3)": high})),
    )
    assert verdict.status is Status.FAIL
    assert "above the upper limit" in verdict.detail


def test_interval_restricts_the_comparison_window() -> None:
    t = np.linspace(0, 1e-3, 100)
    signal = np.concatenate([np.zeros(20), np.full(80, 3.3)])
    raw = make_raw(t, **{"V(out)": signal})
    # Over the whole run the startup ramp violates a 3.0 V minimum...
    whole = evaluate(
        parse_expr({"op": "gt", "signal": "V(out)", "value": 3.0, "unit": "V"}), ctx(raw)
    )
    assert whole.status is Status.FAIL
    # ...but after 300 us it holds.
    windowed = evaluate(
        parse_expr(
            {
                "op": "gt",
                "signal": "V(out)",
                "value": 3.0,
                "unit": "V",
                "interval": {"start_s": 3e-4, "end_s": 1e-3},
            }
        ),
        ctx(raw),
    )
    assert windowed.status is Status.PASS


def test_value_tolerance_widens_only_and_is_reported() -> None:
    t = np.linspace(0, 1e-3, 100)
    raw = make_raw(t, **{"V(out)": np.full(100, 2.97)})
    strict = evaluate(
        parse_expr({"op": "gt", "signal": "V(out)", "value": 3.0, "unit": "V"}), ctx(raw)
    )
    assert strict.status is Status.FAIL
    relaxed = evaluate(
        parse_expr({"op": "gt", "signal": "V(out)", "value": 3.0, "unit": "V"}),
        ctx(raw, tolerances={"value_pct": 2.0}),
    )
    assert relaxed.status is Status.PASS
    assert relaxed.measured["bound"] == pytest.approx(2.94)
    assert relaxed.measured["min(V(out))"] == pytest.approx(2.97)


# --------------------------------------------------------------------------- #
# events


def test_rise_above_reports_the_interpolated_crossing() -> None:
    verdict = evaluate(
        parse_expr({"op": "rise_above", "signal": "V(in)", "value": 0.5, "unit": "V"}),
        ctx(good_raw()),
    )
    assert verdict.status is Status.PASS
    assert verdict.measured["crossing_s"] == pytest.approx(5e-4, rel=1e-3)


def test_rise_above_fails_when_the_level_is_never_reached() -> None:
    t = np.linspace(0, 1e-3, 100)
    verdict = evaluate(
        parse_expr({"op": "rise_above", "signal": "V(out)", "value": 5.0, "unit": "V"}),
        ctx(make_raw(t, **{"V(out)": np.full(100, 3.3)})),
    )
    assert verdict.status is Status.FAIL
    assert verdict.measured["max(V(out))"] == pytest.approx(3.3)


def test_fall_below_reports_the_crossing() -> None:
    t = np.linspace(0, 1e-3, 100)
    signal = np.where(t < 5e-4, 3.3, 0.0)
    verdict = evaluate(
        parse_expr({"op": "fall_below", "signal": "V(pg)", "value": 0.4, "unit": "V"}),
        ctx(make_raw(t, **{"V(pg)": signal})),
    )
    assert verdict.status is Status.PASS
    assert verdict.measured["crossing_s"] == pytest.approx(5e-4, abs=2e-5)


def test_event_delay_measures_the_interval_between_crossings() -> None:
    expr = parse_expr(
        {
            "op": "event_delay",
            "start": {"signal": "V(in)", "kind": "rise_above", "value": 0.5, "unit": "V"},
            "end": {"signal": "V(out)", "kind": "rise_above", "value": 0.5, "unit": "V"},
            "min_s": 1e-4,
            "max_s": 4e-4,
        }
    )
    verdict = evaluate(expr, ctx(good_raw()))
    # V(out) crosses 0.5 V at 750 us; V(in) at 500 us => 250 us.
    assert verdict.status is Status.PASS
    assert verdict.measured["delay_s"] == pytest.approx(2.5e-4, rel=1e-2)


def test_event_delay_fails_when_the_delay_is_out_of_range() -> None:
    expr = parse_expr(
        {
            "op": "event_delay",
            "start": {"signal": "V(in)", "kind": "rise_above", "value": 0.5, "unit": "V"},
            "end": {"signal": "V(out)", "kind": "rise_above", "value": 0.5, "unit": "V"},
            "min_s": 3e-4,
            "max_s": 4e-4,
        }
    )
    verdict = evaluate(expr, ctx(good_raw()))
    assert verdict.status is Status.FAIL
    assert verdict.measured["delay_s"] == pytest.approx(2.5e-4, rel=1e-2)


def test_event_delay_fails_when_the_end_event_never_occurs() -> None:
    expr = parse_expr(
        {
            "op": "event_delay",
            "start": {"signal": "V(in)", "kind": "rise_above", "value": 0.5, "unit": "V"},
            "end": {"signal": "V(out)", "kind": "rise_above", "value": 2.0, "unit": "V"},
            "min_s": 0.0,
            "max_s": 4e-4,
        }
    )
    verdict = evaluate(expr, ctx(good_raw()))
    assert verdict.status is Status.FAIL
    assert "never observed within" in verdict.detail


def test_event_delay_is_unknown_when_nothing_is_observed_and_the_window_is_short() -> None:
    """The coverage guard protects an *unobserved* end event, and only that.

    With the end event never occurring and the run stopping before ``start + max_s``
    the delay is genuinely undecidable: the event could still be coming. When the
    end event *is* observed the quantity is measured, and a produced observation
    must not be downgraded to UNKNOWN for want of extra window.
    """
    t = np.linspace(0, 6e-4, 601)
    ramp = np.clip(t / 2e-4, 0.0, 1.0)
    expr = parse_expr(
        {
            "op": "event_delay",
            "start": {"signal": "V(in)", "kind": "rise_above", "value": 0.5, "unit": "V"},
            "end": {"signal": "V(out)", "kind": "rise_above", "value": 0.5, "unit": "V"},
            "min_s": 0.0,
            "max_s": 5e-3,
        }
    )
    never = np.zeros_like(t)
    verdict = evaluate(expr, ctx(make_raw(t, **{"V(in)": ramp, "V(out)": never})))
    assert verdict.status is Status.UNKNOWN
    assert verdict.unknown_reason == "window_does_not_cover_max"

    # Same window, but now the end event is observed: the delay is decided.
    observed = evaluate(expr, ctx(make_raw(t, **{"V(in)": ramp, "V(out)": ramp})))
    assert observed.status is Status.PASS
    assert observed.measured["delay_s"] == pytest.approx(0.0, abs=1e-9)


def test_pulse_width_measures_the_high_time() -> None:
    t = np.linspace(0, 1e-3, 2001)
    pulse = np.where((t >= 2e-4) & (t < 6e-4), 3.3, 0.0)
    expr = parse_expr(
        {
            "op": "pulse_width",
            "start": {"signal": "V(x)", "kind": "rise_above", "value": 1.6, "unit": "V"},
            "end": {"signal": "V(x)", "kind": "fall_below", "value": 1.6, "unit": "V"},
            "min_s": 3e-4,
            "max_s": 5e-4,
        }
    )
    verdict = evaluate(expr, ctx(make_raw(t, **{"V(x)": pulse})))
    assert verdict.status is Status.PASS
    assert verdict.measured["width_s"] == pytest.approx(4e-4, rel=5e-3)


def test_ordering_passes_and_fails_correctly() -> None:
    ok = parse_expr(
        {
            "op": "ordering",
            "first": {"signal": "V(in)", "kind": "rise_above", "value": 0.5, "unit": "V"},
            "then": {"signal": "V(out)", "kind": "rise_above", "value": 0.5, "unit": "V"},
        }
    )
    assert evaluate(ok, ctx(good_raw())).status is Status.PASS

    reversed_expr = parse_expr(
        {
            "op": "ordering",
            "first": {"signal": "V(out)", "kind": "rise_above", "value": 0.5, "unit": "V"},
            "then": {"signal": "V(in)", "kind": "rise_above", "value": 0.5, "unit": "V"},
        }
    )
    verdict = evaluate(reversed_expr, ctx(good_raw()))
    assert verdict.status is Status.FAIL
    assert verdict.measured["delta_s"] == pytest.approx(-2.5e-4, rel=1e-2)


def test_ordering_fails_when_an_event_never_happens() -> None:
    expr = parse_expr(
        {
            "op": "ordering",
            "first": {"signal": "V(in)", "kind": "rise_above", "value": 0.5, "unit": "V"},
            "then": {"signal": "V(3V3)", "kind": "rise_above", "value": 5.0, "unit": "V"},
        }
    )
    verdict = evaluate(expr, ctx(good_raw()))
    assert verdict.status is Status.FAIL
    assert "then event" in verdict.detail


def test_ordering_uses_first_then_last_qualifiers() -> None:
    t = np.array([0.0, 1e-3, 2e-3, 3e-3])
    a = np.array([0.0, 1.0, 0.0, 1.0])
    b = np.array([0.0, 0.0, 1.0, 0.0])
    raw = make_raw(t, **{"V(a)": a, "V(b)": b})
    # V(a) last crosses at 2.5 ms, V(b) first at 1.5 ms: the required order fails.
    fails = parse_expr(
        {
            "op": "ordering",
            "first": {
                "signal": "V(a)",
                "kind": "rise_above",
                "value": 0.5,
                "unit": "V",
                "qualifier": "last",
            },
            "then": {
                "signal": "V(b)",
                "kind": "rise_above",
                "value": 0.5,
                "unit": "V",
                "qualifier": "first",
            },
        }
    )
    verdict = evaluate(fails, ctx(raw))
    assert verdict.status is Status.FAIL
    assert verdict.measured["first_s"] == pytest.approx(2.5e-3)
    assert verdict.measured["then_s"] == pytest.approx(1.5e-3)

    # V(a) first crosses at 0.5 ms, before V(b) at 1.5 ms.
    passes = parse_expr(
        {
            "op": "ordering",
            "first": {
                "signal": "V(a)",
                "kind": "rise_above",
                "value": 0.5,
                "unit": "V",
                "qualifier": "first",
            },
            "then": {
                "signal": "V(b)",
                "kind": "rise_above",
                "value": 0.5,
                "unit": "V",
                "qualifier": "last",
            },
        }
    )
    assert evaluate(passes, ctx(raw)).status is Status.PASS


# --------------------------------------------------------------------------- #
# hold


def test_hold_stable_passes_for_a_settled_signal() -> None:
    t = np.linspace(0, 1e-3, 200)
    expr = parse_expr(
        {
            "op": "hold",
            "signal": "V(3V3)",
            "value": 3.3,
            "unit": "V",
            "interval": {"start_s": 0.0, "end_s": 1e-3},
            "stable": True,
        }
    )
    verdict = evaluate(expr, ctx(make_raw(t, **{"V(3V3)": np.full(200, 3.298)})))
    assert verdict.status is Status.PASS
    assert verdict.measured["max_deviation(V(3V3))"] == pytest.approx(0.002, abs=1e-9)


def test_hold_stable_fails_on_a_glitch_beyond_tolerance() -> None:
    t = np.linspace(0, 1e-3, 200)
    signal = np.full(200, 3.3)
    signal[100] = 3.9
    expr = parse_expr(
        {
            "op": "hold",
            "signal": "V(3V3)",
            "value": 3.3,
            "unit": "V",
            "interval": {"start_s": 0.0, "end_s": 1e-3},
            "stable": True,
        }
    )
    verdict = evaluate(expr, ctx(make_raw(t, **{"V(3V3)": signal})))
    assert verdict.status is Status.FAIL
    assert verdict.measured["max_deviation(V(3V3))"] == pytest.approx(0.6, abs=1e-9)


def test_hold_not_stable_requires_reaching_the_value() -> None:
    t = np.linspace(0, 1e-3, 200)
    expr = parse_expr({"op": "hold", "signal": "V(x)", "value": 3.3, "unit": "V", "stable": False})
    reached = evaluate(expr, ctx(make_raw(t, **{"V(x)": np.linspace(0, 3.3, 200)})))
    assert reached.status is Status.PASS
    never = evaluate(expr, ctx(make_raw(t, **{"V(x)": np.linspace(0, 2.0, 200)})))
    assert never.status is Status.FAIL


# --------------------------------------------------------------------------- #
# composition and gating


def test_state_dependent_gates_on_its_precondition() -> None:
    t = np.linspace(0, 1e-3, 1001)
    en = np.where(t >= 2e-4, 1.2, 0.0)
    out = np.where(t >= 3e-4, 3.3, 0.0)
    raw = make_raw(t, **{"V(en)": en, "V(out)": out})
    expr = parse_expr(
        {
            "op": "state_dependent",
            "when": {"signal": "V(en)", "kind": "rise_above", "value": 1.0, "unit": "V"},
            "then": {"op": "rise_above", "signal": "V(out)", "value": 3.0, "unit": "V"},
        }
    )
    verdict = evaluate(expr, ctx(raw))
    assert verdict.status is Status.PASS
    assert verdict.measured["gate_time_s"] == pytest.approx(2e-4, rel=1e-2)
    # The gate is exclusive: a universal consequence starting at the gate would
    # (correctly) fail here, because V(out) is still 0 V in the first 100 us
    # after EN rises.
    universal = parse_expr(
        {
            "op": "state_dependent",
            "when": {"signal": "V(en)", "kind": "rise_above", "value": 1.0, "unit": "V"},
            "then": {"op": "gt", "signal": "V(out)", "value": 3.0, "unit": "V"},
        }
    )
    gated_fail = evaluate(universal, ctx(raw))
    assert gated_fail.status is Status.FAIL
    assert gated_fail.measured["min(V(out))"] == pytest.approx(0.0)


def test_state_dependent_is_not_applicable_when_the_precondition_never_occurs() -> None:
    t = np.linspace(0, 1e-3, 1001)
    expr = parse_expr(
        {
            "op": "state_dependent",
            "when": {"signal": "V(en)", "kind": "rise_above", "value": 1.0, "unit": "V"},
            "then": {"op": "gt", "signal": "V(out)", "value": 3.0, "unit": "V"},
        }
    )
    verdict = evaluate(
        expr, ctx(make_raw(t, **{"V(en)": np.zeros(1001), "V(out)": np.full(1001, 3.3)}))
    )
    assert verdict.status is Status.NOT_APPLICABLE
    assert "precondition" in verdict.detail


def test_state_dependent_fails_when_gated_behaviour_is_violated() -> None:
    t = np.linspace(0, 1e-3, 1001)
    en = np.where(t >= 2e-4, 1.2, 0.0)
    expr = parse_expr(
        {
            "op": "state_dependent",
            "when": {"signal": "V(en)", "kind": "rise_above", "value": 1.0, "unit": "V"},
            "then": {"op": "gt", "signal": "V(out)", "value": 3.0, "unit": "V"},
        }
    )
    verdict = evaluate(expr, ctx(make_raw(t, **{"V(en)": en, "V(out)": np.zeros(1001)})))
    assert verdict.status is Status.FAIL
    assert "gated on" in verdict.detail


def test_all_of_any_of_follow_failure_precedence() -> None:
    raw = good_raw()
    ok = {"op": "gt", "signal": "V(3V3)", "value": 3.0, "unit": "V"}
    bad = {"op": "gt", "signal": "V(3V3)", "value": 5.0, "unit": "V"}
    assert evaluate(parse_expr({"op": "all_of", "items": [ok, ok]}), ctx(raw)).status is Status.PASS
    assert (
        evaluate(parse_expr({"op": "all_of", "items": [ok, bad]}), ctx(raw)).status is Status.FAIL
    )
    assert (
        evaluate(parse_expr({"op": "any_of", "items": [bad, ok]}), ctx(raw)).status is Status.PASS
    )
    assert (
        evaluate(parse_expr({"op": "any_of", "items": [bad, bad]}), ctx(raw)).status is Status.FAIL
    )


def test_not_inverts_and_passes_through_unknown() -> None:
    raw = good_raw()
    holds = {"op": "gt", "signal": "V(3V3)", "value": 3.0, "unit": "V"}
    assert evaluate(parse_expr({"op": "not", "item": holds}), ctx(raw)).status is Status.FAIL
    violated = {"op": "gt", "signal": "V(3V3)", "value": 5.0, "unit": "V"}
    assert evaluate(parse_expr({"op": "not", "item": violated}), ctx(raw)).status is Status.PASS
    missing = {"op": "gt", "signal": "V(nope)", "value": 1.0, "unit": "V"}
    verdict = evaluate(parse_expr({"op": "not", "item": missing}), ctx(raw))
    assert verdict.status is Status.UNKNOWN
    assert verdict.unknown_reason == "signal_not_saved"


# --------------------------------------------------------------------------- #
# vacuous-pass guards


def test_missing_signal_is_unknown_not_a_pass() -> None:
    verdict = evaluate(
        parse_expr({"op": "gt", "signal": "V(absent)", "value": 1.0, "unit": "V"}),
        ctx(good_raw()),
    )
    assert verdict.status is Status.UNKNOWN
    assert verdict.unknown_reason == "signal_not_saved"
    assert "saved variables" in verdict.detail


def test_window_beyond_the_saved_data_is_unknown() -> None:
    verdict = evaluate(
        parse_expr(
            {
                "op": "gt",
                "signal": "V(3V3)",
                "value": 3.0,
                "unit": "V",
                "interval": {"start_s": 5e-4, "end_s": 5e-3},
            }
        ),
        ctx(good_raw()),
    )
    assert verdict.status is Status.UNKNOWN
    assert verdict.unknown_reason == "window_unusable:window_not_covered"


def test_too_few_samples_is_unknown() -> None:
    t = np.array([0.0, 2e-4, 4e-4, 6e-4, 8e-4, 1e-3])
    verdict = evaluate(
        parse_expr({"op": "gt", "signal": "V(x)", "value": 0.0, "unit": "V"}),
        ctx(make_raw(t, **{"V(x)": np.ones(6)})),
    )
    assert verdict.status is Status.UNKNOWN
    assert verdict.unknown_reason == "window_unusable:insufficient_points"


def test_too_coarse_time_grid_is_unknown() -> None:
    t = np.concatenate([np.arange(0, 10) * 1e-7, [1e-3]])
    verdict = evaluate(
        parse_expr({"op": "gt", "signal": "V(x)", "value": 0.0, "unit": "V"}),
        ctx(make_raw(t, **{"V(x)": np.ones(t.size)})),
    )
    assert verdict.status is Status.UNKNOWN
    assert verdict.unknown_reason == "window_unusable:too_coarse"


def test_non_finite_data_is_unknown() -> None:
    verdict = evaluate(
        parse_expr({"op": "gt", "signal": "V(x)", "value": 0.0, "unit": "V"}),
        ctx(make_raw(np.linspace(0, 1e-3, 50), **{"V(x)": np.full(50, np.nan)})),
    )
    assert verdict.status is Status.UNKNOWN
    assert verdict.unknown_reason == "window_unusable:non_finite"


def test_unusable_run_blocks_every_comparison_and_the_negation() -> None:
    blocked = EvalContext(
        raw=good_raw(),
        usability=RunUsability(usable=False, blocked_reason="sim_convergence_failure"),
    )
    for payload in (
        {"op": "gt", "signal": "V(3V3)", "value": 3.0, "unit": "V"},
        {"op": "not", "item": {"op": "gt", "signal": "V(3V3)", "value": 3.0, "unit": "V"}},
        {"op": "between", "signal": "V(3V3)", "low": 3.0, "high": 4.0, "unit": "V"},
    ):
        verdict = evaluate(parse_expr(payload), blocked)
        assert verdict.status is Status.BLOCKED, payload
        assert verdict.blocked_reason == "sim_convergence_failure"


def test_no_raw_at_all_is_blocked() -> None:
    verdict = evaluate(
        parse_expr({"op": "gt", "signal": "V(x)", "value": 0.0, "unit": "V"}), EvalContext()
    )
    assert verdict.status is Status.BLOCKED
    assert verdict.blocked_reason == "sim_output_missing"


# --------------------------------------------------------------------------- #
# connectivity ops


@dataclass
class FakeConnectivity:
    nodes: dict[tuple[str, str], str]
    nets: dict[str, list[tuple[str, str]]]

    def node_of(self, refdes: str, pin: str) -> str | None:
        return self.nodes.get((refdes, pin))

    def pins_on(self, net: str) -> list[tuple[str, str]]:
        return list(self.nets.get(net, []))

    def has_refdes(self, refdes: str) -> bool:
        return any(key[0] == refdes for key in self.nodes)


def fake_connectivity() -> FakeConnectivity:
    nodes = {
        ("U1", "PERST"): "PERST_N",
        ("U1", "PG"): "1V8_PG",
        ("U1", "SMB_CLK"): "SMB_CLK",
        ("R7", "T"): "1V8_PG",
        ("R7", "B"): "1V8",
        ("U2", "EN"): "EN_U2",
    }
    nets = {
        "PERST_N": [("U1", "PERST"), ("RST", "out")],
        "1V8_PG": [("U1", "PG"), ("R7", "T")],
        "SMB_CLK": [("U1", "SMB_CLK")],
        "EN_U2": [("U2", "EN")],
    }
    return FakeConnectivity(nodes=nodes, nets=nets)


def run_usable_ok() -> RunUsability:
    """A usable run: the simulator produced data this run can be judged on."""
    return RunUsability(usable=True)


def conn_ctx() -> EvalContext:
    return EvalContext(
        raw=good_raw(),
        usability=run_usable_ok(),
        connectivity=fake_connectivity(),
        supply_domains={"PERST_N": "3V3", "1V8_PG": "1V8", "EN_U2": "3V3"},
    )


def test_net_equals_and_not_equals() -> None:
    assert (
        evaluate(
            parse_expr({"op": "net_equals", "refdes": "U1", "pin": "PERST", "net": "PERST_N"}),
            conn_ctx(),
        ).status
        is Status.PASS
    )
    wrong = evaluate(
        parse_expr({"op": "net_equals", "refdes": "U1", "pin": "PERST", "net": "GND"}),
        conn_ctx(),
    )
    assert wrong.status is Status.FAIL
    assert wrong.measured["observed_net"] == "PERST_N"
    assert (
        evaluate(
            parse_expr({"op": "net_not_equals", "refdes": "U1", "pin": "PERST", "net": "GND"}),
            conn_ctx(),
        ).status
        is Status.PASS
    )


def test_connectivity_ops_are_unknown_without_a_netlist() -> None:
    for payload in (
        {"op": "net_equals", "refdes": "U1", "pin": "PERST", "net": "PERST_N"},
        {"op": "pin_connected", "refdes": "U1", "pin": "PERST"},
        {"op": "pullup_domain", "refdes": "U1", "pin": "PG", "net": "1V8_PG", "domain": "1V8"},
    ):
        verdict = evaluate(parse_expr(payload), EvalContext(raw=good_raw()))
        assert verdict.status is Status.UNKNOWN
        assert verdict.unknown_reason == "connectivity_unavailable"


def test_unknown_refdes_and_pin_are_unknown() -> None:
    refdes = evaluate(
        parse_expr({"op": "net_equals", "refdes": "U9", "pin": "A", "net": "X"}), conn_ctx()
    )
    assert refdes.unknown_reason == "refdes_not_in_netlist"
    pin = evaluate(
        parse_expr({"op": "net_equals", "refdes": "U1", "pin": "NOPE", "net": "X"}), conn_ctx()
    )
    assert pin.unknown_reason == "pin_not_in_netlist"


def test_pullup_domain_checks_the_domain_of_the_pull_up_net() -> None:
    ok = evaluate(
        parse_expr(
            {"op": "pullup_domain", "refdes": "U1", "pin": "PG", "net": "1V8_PG", "domain": "1V8"}
        ),
        conn_ctx(),
    )
    assert ok.status is Status.PASS
    assert "R7.T" in str(ok.measured["pins_on_net"])

    wrong_domain = evaluate(
        parse_expr(
            {"op": "pullup_domain", "refdes": "U1", "pin": "PG", "net": "1V8_PG", "domain": "3V3"}
        ),
        conn_ctx(),
    )
    assert wrong_domain.status is Status.FAIL
    assert wrong_domain.measured["observed_domain"] == "1V8"

    wrong_net = evaluate(
        parse_expr(
            {"op": "pullup_domain", "refdes": "U1", "pin": "PG", "net": "3V3", "domain": "3V3"}
        ),
        conn_ctx(),
    )
    assert wrong_net.status is Status.FAIL


def test_pin_connected_and_open() -> None:
    assert (
        evaluate(
            parse_expr({"op": "pin_connected", "refdes": "U1", "pin": "PERST"}), conn_ctx()
        ).status
        is Status.PASS
    )
    # A pin alone on its net is effectively open.
    alone = evaluate(
        parse_expr({"op": "pin_connected", "refdes": "U1", "pin": "SMB_CLK"}), conn_ctx()
    )
    assert alone.status is Status.FAIL
    assert (
        evaluate(
            parse_expr({"op": "pin_open", "refdes": "U1", "pin": "SMB_CLK"}), conn_ctx()
        ).status
        is Status.PASS
    )
    # A pin absent from the netlist entirely.
    absent = evaluate(parse_expr({"op": "pin_open", "refdes": "U1", "pin": "GHOST"}), conn_ctx())
    assert absent.status is Status.PASS
    assert (
        evaluate(
            parse_expr({"op": "pin_connected", "refdes": "U1", "pin": "GHOST"}), conn_ctx()
        ).status
        is Status.FAIL
    )


def RunUsable_ok() -> RunUsability:
    return RunUsability(usable=True)
