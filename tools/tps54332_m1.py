r"""Code-built TPS54332 side-by-side experiment for the M1 evidence milestone.

Run from the general-edition repository with explicit, local LTspice and TI-library
paths. The TI library is included by absolute path in local decks; it is never copied
into this repository. Results are observations, not a product verification verdict.

Example (PowerShell)::

    .\.venv\Scripts\python.exe tools\tps54332_m1.py --ltspice-exe "C:\\path\\to\\LTspice.exe" --ti-lib "C:\\path\\to\\TPS54332_TRANS.LIB" --out "C:\\short\\m1"

Use --models ours for a short circuit check before expensive vendor runs. Every
case/model writes its own JSON so completed measurements survive an interrupted run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import numpy as np

from boardmodeler.authoring.spec import load_tps54320_spec
from boardmodeler.models.buck_switching import seed_from_spec
from boardmodeler.simulation.ltspice import run_batch
from boardmodeler.simulation.raw import read_raw

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "models" / "T1-tps54332" / "spec"
PART = "TPS54332DDA"
TI_SUBCKT = "TPS54332_TRANS"
CASES = ("reference", "gain", "overload", "short", "load_step")
MODELS = ("ours", "ti")
GAIN_LEVELS = tuple(round(0.5 + 0.05 * i, 2) for i in range(13))
GAIN_START_S = 7e-3
GAIN_HOLD_S = 500e-6
GAIN_WINDOW_OFFSET_S = 350e-6
LOAD_SWITCH_S = 8e-3
STEADY_START_S = 8.6e-3
STEADY_END_S = 9.6e-3
VIN_V = 12.0
VREF_V = 0.8
VOUT_TARGET_V = VREF_V * (1 + 10.2 / 4.75)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _spice_time(seconds: float) -> str:
    return f"{seconds * 1e3:.9g}m"


def _gain_pwl() -> str:
    # Hold each point for 500 us, with a 1 us transition. The earlier and later
    # windows provide a measured settling check; each spans >100 nominal cycles.
    points = [(0.0, GAIN_LEVELS[0])]
    for index, level in enumerate(GAIN_LEVELS):
        start = GAIN_START_S + index * GAIN_HOLD_S
        if index == 0:
            points.append((start, level))
        else:
            points.append((start, GAIN_LEVELS[index - 1]))
            points.append((start + 1e-6, level))
        points.append((start + GAIN_HOLD_S - 1e-6, level))
    values = " ".join(f"{_spice_time(t)} {v:.2f}" for t, v in points)
    return f"Vforce comp 0 PWL({values})"


def _case_parts(case: str) -> tuple[list[str], str, float]:
    """Return only shared circuit changes, transient command and measurement stop."""
    # LTspice rebases the saved raw time axis to zero for a nonzero .tran Tstart.
    # Keep Tstart at zero so the post-soft-start measurement windows stay absolute.
    if case == "reference":
        return [], ".tran 0 10m 0 50n", 10e-3
    if case == "gain":
        return (
            ["Rgain out 0 0.625", "Vfb vsense 0 0.8", _gain_pwl()],
            ".tran 0 13.55m 0 50n",
            13.55e-3,
        )
    if case == "overload":
        # 2.5 || 0.30 ohm draws >9 A at the nominal 2.52 V output.
        added = ["Rfault out fault 0.30", *_load_switch_parts()]
        return added, ".tran 0 10m 0 50n", 10e-3
    if case == "short":
        added = ["Rfault out fault 0.01", *_load_switch_parts()]
        return added, ".tran 0 10m 0 50n", 10e-3
    if case == "load_step":
        # The TI reference's 2.5-ohm resistor is ~1 A at its 2.52 V target.
        # Adding 1.25 ohm in parallel raises the target to ~3 A.
        added = ["Rfault out fault 1.25", *_load_switch_parts()]
        return added, ".tran 0 10m 0 50n", 10e-3
    raise ValueError(f"unknown case: {case}")


def _load_switch_parts() -> list[str]:
    return [
        "Sfault fault 0 fault_ctl 0 SFAULT",
        f"Vfault fault_ctl 0 PULSE(0 1 {_spice_time(LOAD_SWITCH_S)} 1n 1n 20m 40m)",
        ".model SFAULT SW(Ron=1m Roff=1G Vt=0.5 Vh=0)",
    ]


def _common_circuit(case: str) -> str:
    """The saved TI deck's passive network, unchanged for both DUTs."""
    added, transient, _ = _case_parts(case)
    lines = [
        f"* M1 {case}: shared TPS54332 TI-reference passives",
        "Vin vin 0 PULSE(0 12 0 1u 1u 30m 60m)",
        "Cin vin 0 10u",
        "Ruvhi vin en 150k",
        "Ruvlo en 0 48.7k",
        "Css ss 0 15n",
        "Cboot boot ph 100n",
        "Dcatch 0 ph Dcatch",
        ".model Dcatch D(Is=1e-8 N=1.1 Rs=0.04 Cjo=300p Bv=40 Ibv=1m)",
        "Lout ph out 2.5u Rser=10m",
        "Cout1 out 0 47u Rser=3m",
        "Cout2 out 0 47u Rser=3m",
        "Rload out 0 2.5",
        "Rfbhi out vsense 10.2k",
        "Rfblo vsense 0 4.75k",
        "Rcomp comp compmid 75k",
        "Ccomp compmid 0 180p",
        "Chf comp 0 10p",
        ".temp 25",
        *added,
        transient,
        ".save V(ph) V(out) I(Lout) V(comp) V(vsense) V(ss)",
    ]
    return "\n".join(lines) + "\n"


def _deck(case: str, model: str, library: Path) -> tuple[str, str]:
    common = _common_circuit(case)
    include = f'.include "{library.resolve().as_posix()}"'
    instance = (
        f"XU1 boot vin en ss vsense comp 0 ph 0 {TI_SUBCKT}"
        if model == "ti"
        else f"XU1 boot vin en ss vsense comp 0 ph 0 {PART}"
    )
    common_hash = hashlib.sha256(common.encode("utf-8")).hexdigest()
    head, tail = common.split("Vin vin 0", 1)
    body = head + include + "\n" + "Vin vin 0" + tail
    body = body.replace("\n.tran ", "\n" + instance + "\n.tran ", 1)
    return body + ".end\n", common_hash


def _crossings(t: np.ndarray, values: np.ndarray, threshold: float, rising: bool) -> np.ndarray:
    if rising:
        hits = np.flatnonzero((values[:-1] < threshold) & (values[1:] >= threshold))
    else:
        hits = np.flatnonzero((values[:-1] > threshold) & (values[1:] <= threshold))
    if len(hits) == 0:
        return np.array([], dtype=float)
    left = values[hits]
    right = values[hits + 1]
    denominator = right - left
    frac = np.divide(threshold - left, denominator, out=np.zeros_like(left), where=denominator != 0)
    return t[hits] + frac * (t[hits + 1] - t[hits])


def _range(t: np.ndarray, start: float, stop: float) -> slice:
    return slice(int(np.searchsorted(t, start)), int(np.searchsorted(t, stop)))


def _frequency(
    t: np.ndarray, ph: np.ndarray, start: float, stop: float
) -> dict[str, float | int | None]:
    window = _range(t, start, stop)
    edges = _crossings(t[window], ph[window], VIN_V / 2, True)
    periods = np.diff(edges)
    periods = periods[periods > 0]
    return {
        "rising_edges": len(edges),
        "frequency_hz": float(1 / np.median(periods)) if len(periods) >= 2 else None,
        "period_min_s": float(np.min(periods)) if len(periods) else None,
        "period_max_s": float(np.max(periods)) if len(periods) else None,
    }


def _cycle_peaks(
    t: np.ndarray, ph: np.ndarray, current: np.ndarray, start: float, stop: float
) -> dict[str, float | int | None]:
    window = _range(t, start, stop)
    tt, vv, ii = t[window], ph[window], current[window]
    edges = _crossings(tt, vv, VIN_V / 2, True)
    peaks: list[float] = []
    for left, right in pairwise(edges):
        section = _range(tt, float(left), float(right))
        if section.stop - section.start >= 2:
            peaks.append(float(np.max(ii[section])))
    return {
        "cycles": len(peaks),
        "peak_a": float(np.median(peaks)) if peaks else None,
        "peak_max_a": float(np.max(peaks)) if peaks else None,
        "peak_spread_a": float(np.ptp(peaks)) if peaks else None,
        "average_a": float(np.mean(ii)) if len(ii) else None,
    }


def _edge_times(t: np.ndarray, ph: np.ndarray, start: float, stop: float) -> dict[str, object]:
    """10-90% widths; require interior samples to avoid invented sub-step edges."""
    window = _range(t, start, stop)
    tt, vv = t[window], ph[window]
    widths: dict[str, list[float]] = {"rise": [], "fall": []}
    interiors: dict[str, list[int]] = {"rise": [], "fall": []}
    for name, rising in (("rise", True), ("fall", False)):
        low = 0.1 * VIN_V if rising else 0.9 * VIN_V
        high = 0.9 * VIN_V if rising else 0.1 * VIN_V
        starts = _crossings(tt, vv, low, rising)
        ends = _crossings(tt, vv, high, rising)
        for first in starts:
            after = ends[(ends >= first) & (ends <= first + 1e-6)]
            if not len(after):
                continue
            last = float(after[0])
            section = _range(tt, float(first), last)
            inside = vv[section]
            interior = int(np.count_nonzero((inside > 0.1 * VIN_V) & (inside < 0.9 * VIN_V)))
            widths[name].append(last - first)
            interiors[name].append(interior)
    return (
        {
            f"{name}_10_90_s": (
                float(
                    np.median(
                        [w for w, n in zip(widths[name], interiors[name], strict=True) if n >= 2]
                    )
                )
                if any(n >= 2 for n in interiors[name])
                else None
            )
            for name in ("rise", "fall")
        }
        | {f"{name}_transitions": len(widths[name]) for name in ("rise", "fall")}
        | {
            f"{name}_resolved_transitions": sum(n >= 2 for n in interiors[name])
            for name in ("rise", "fall")
        }
    )


def _startup(t: np.ndarray, out: np.ndarray) -> dict[str, object]:
    steady = out[_range(t, 8.6e-3, 9.6e-3)]
    if not len(steady):
        return {"reason": "missing steady-state window"}
    final = float(np.mean(steady))
    if final <= 0:
        return {"reason": "output did not start", "final_v": final}
    first10 = _crossings(t, out, 0.1 * final, True)
    first90 = _crossings(t, out, 0.9 * final, True)
    ascent_bins: list[tuple[float, float]] = []
    if len(first10) and len(first90):
        for bin_start in np.arange(float(first10[0]), float(first90[0]), 100e-6):
            samples = out[_range(t, float(bin_start), float(bin_start + 100e-6))]
            if len(samples):
                ascent_bins.append((float(bin_start), float(np.mean(samples))))
    descent_steps = [
        prior[1] - later[1]
        for prior, later in pairwise(ascent_bins)
        if prior[1] - later[1] > 0.02 * final
    ]
    early_ring = (
        out[_range(t, float(first90[0]), float(first90[0] + 0.4e-3))]
        if len(first90)
        else np.array([])
    )
    late_ring = out[_range(t, 8.6e-3, 9.6e-3)]
    checkpoints = {}
    for ms in range(0, 10):
        around = out[_range(t, ms * 1e-3, ms * 1e-3 + 50e-6)]
        checkpoints[str(ms)] = float(np.mean(around)) if len(around) else None
    return {
        "final_v": final,
        "max_v": float(np.max(out)),
        "overshoot_v": float(np.max(out) - final),
        "rise_10_90_s": float(first90[0] - first10[0]) if len(first10) and len(first90) else None,
        "first_10pct_s": float(first10[0]) if len(first10) else None,
        "first_90pct_s": float(first90[0]) if len(first90) else None,
        "ascent_100us_bins": len(ascent_bins),
        "monotonicity_drops_over_2pct": len(descent_steps),
        "largest_ascent_drop_v": max(descent_steps, default=0.0),
        "early_postrise_pp_v": float(np.ptp(early_ring)) if len(early_ring) else None,
        "late_steady_pp_v": float(np.ptp(late_ring)) if len(late_ring) else None,
        "vout_by_ms": checkpoints,
    }


def _gain(t: np.ndarray, ph: np.ndarray, current: np.ndarray) -> dict[str, object]:
    points: list[dict[str, object]] = []
    for index, level in enumerate(GAIN_LEVELS):
        plateau_start = GAIN_START_S + index * GAIN_HOLD_S
        start = GAIN_START_S + index * GAIN_HOLD_S + GAIN_WINDOW_OFFSET_S
        stop = GAIN_START_S + (index + 1) * GAIN_HOLD_S - 5e-6
        early = _cycle_peaks(t, ph, current, plateau_start + 150e-6, plateau_start + 300e-6)
        measured = _cycle_peaks(t, ph, current, start, stop)
        early_peak = early["peak_a"]
        late_peak = measured["peak_a"]
        delta = (
            abs(float(late_peak) - float(early_peak))
            if early_peak is not None and late_peak is not None
            else None
        )
        stable = (
            delta is not None
            and delta <= max(0.05, 0.05 * abs(float(late_peak)))
            and int(early["cycles"]) >= 10
            and int(measured["cycles"]) >= 10
        )
        points.append(
            {
                "comp_v": level,
                "early_window_s": [plateau_start + 150e-6, plateau_start + 300e-6],
                "window_s": [start, stop],
                "early_peak_a": early_peak,
                "stability_delta_a": delta,
                "stable": stable,
                **measured,
            }
        )

    # A plateau must be measured. Two adjacent increments under 0.12 A,
    # after current exceeds 1 A, define the saturated tail. The 0.50 V
    # threshold point is recorded but never enters the fit.
    plateau_at: int | None = None
    for index in range(3, len(points)):
        values = [points[j]["peak_a"] for j in (index - 2, index - 1, index)]
        if any(value is None for value in values):
            continue
        a, b, c = (float(value) for value in values)
        if a > 1 and 0 <= b - a < 0.12 and 0 <= c - b < 0.12:
            plateau_at = index - 2
            break

    fit_points = [
        point
        for index, point in enumerate(points)
        if 0.5 < float(point["comp_v"]) <= 0.9
        and (plateau_at is None or index < plateau_at)
        and point["peak_a"] is not None
        and point["stable"]
        and int(point["cycles"]) >= 10
    ]
    result: dict[str, object] = {
        "points": points,
        "threshold_point_excluded": True,
        "plateau_detected": plateau_at is not None,
        "plateau_from_comp_v": GAIN_LEVELS[plateau_at] if plateau_at is not None else None,
        "fit_comp_v": [point["comp_v"] for point in fit_points],
        "fit_rule": (
            "0.50<V(COMP)<=0.90; >=10 cycles in early and late windows; "
            "early/late peaks differ <=max(0.05 A,5%); exclude detected saturation plateau"
        ),
    }
    if len(fit_points) < 3:
        result["reason"] = "fewer than three unsaturated active points with measured cycles"
        return result
    x = np.array([float(point["comp_v"]) for point in fit_points])
    y = np.array([float(point["peak_a"]) for point in fit_points])
    slope, intercept = np.polyfit(x, y, 1)
    residual = y - (slope * x + intercept)
    result.update(
        {
            "slope_a_per_v": float(slope),
            "intercept_a": float(intercept),
            "fit_max_abs_residual_a": float(np.max(np.abs(residual))),
            "fit_points": len(fit_points),
            "high_comp_peak_a": points[-1]["peak_a"],
            "candidate_current_limit_plateau": plateau_at is not None,
            "limit_cross_check": "Compare plateau peak with independently measured overload/short current",
        }
    )
    return result


def _load_step(t: np.ndarray, out: np.ndarray) -> dict[str, object]:
    before = out[_range(t, 7.7e-3, 7.95e-3)]
    after = out[_range(t, 9.2e-3, 9.6e-3)]
    during = out[_range(t, LOAD_SWITCH_S, 9.6e-3)]
    if not len(before) or not len(after) or not len(during):
        return {"reason": "load-step windows missing"}
    before_v = float(np.mean(before))
    after_v = float(np.mean(after))
    dip_v = float(np.min(during))
    # Recovery means entering the final-value ±2% band and staying there
    # for the remainder of the observed window. A late crossing does not count.
    tt = t[_range(t, LOAD_SWITCH_S, 9.6e-3)]
    band = 0.02 * max(abs(after_v), 1e-6)
    inside = np.abs(during - after_v) <= band
    stable_suffix = np.logical_and.accumulate(inside[::-1])[::-1]
    recovered = np.flatnonzero(stable_suffix)
    half_a = out[_range(t, 9.2e-3, 9.4e-3)]
    half_b = out[_range(t, 9.4e-3, 9.6e-3)]
    final_stable = (
        len(half_a) > 0
        and len(half_b) > 0
        and abs(float(np.mean(half_a)) - float(np.mean(half_b))) <= band
    )
    early_ring = out[_range(t, 8.05e-3, 8.5e-3)]
    late_ring = out[_range(t, 9.2e-3, 9.6e-3)]
    return {
        "pre_step_v": before_v,
        "post_step_v": after_v,
        "min_after_step_v": dip_v,
        "dip_v": before_v - dip_v,
        "recovery_s": (
            float(tt[recovered[0]] - LOAD_SWITCH_S) if len(recovered) and final_stable else None
        ),
        "final_window_stable": final_stable,
        "final_half_mean_difference_v": (
            abs(float(np.mean(half_a)) - float(np.mean(half_b)))
            if len(half_a) and len(half_b)
            else None
        ),
        "early_poststep_pp_v": float(np.ptp(early_ring)) if len(early_ring) else None,
        "late_poststep_pp_v": float(np.ptp(late_ring)) if len(late_ring) else None,
        "recovery_band_v": band,
        "nominal_load_step_a": [1.0, 3.0],
        "load_at_pre_step_output_a": [before_v / 2.5, before_v / 2.5 + before_v / 1.25],
    }


def _measure(case: str, raw_path: Path) -> dict[str, object]:
    raw = read_raw(raw_path)
    t = np.asarray(raw.time_column(), dtype=float)
    ph = np.asarray(raw.column("V(ph)"), dtype=float)
    out = np.asarray(raw.column("V(out)"), dtype=float)
    current = np.asarray(raw.column("I(Lout)"), dtype=float)
    vsense = np.asarray(raw.column("V(vsense)"), dtype=float)
    if not len(t) or not all(np.all(np.isfinite(x)) for x in (t, ph, out, current, vsense)):
        raise ValueError("raw waveform is empty or nonfinite")
    observed: dict[str, object] = {
        "raw_points": int(raw.npoints),
        "raw_variables": raw.variables,
        "time_span_s": [float(t[0]), float(t[-1])],
    }
    if case == "gain":
        observed["gain"] = _gain(t, ph, current)
    elif case == "reference":
        window = _range(t, STEADY_START_S, STEADY_END_S)
        if window.stop <= window.start:
            raise ValueError("reference steady-state window missing")
        observed["steady"] = {
            "window_s": [STEADY_START_S, STEADY_END_S],
            "vout_mean_v": float(np.mean(out[window])),
            "vout_ripple_pp_v": float(np.ptp(out[window])),
            "inductor_peak_a": float(np.max(current[window])),
            "inductor_average_a": float(np.mean(current[window])),
            **_frequency(t, ph, STEADY_START_S, STEADY_END_S),
            **_edge_times(t, ph, STEADY_START_S, STEADY_END_S),
        }
        observed["startup"] = _startup(t, out)
    elif case in {"overload", "short"}:
        window = _range(t, STEADY_START_S, STEADY_END_S)
        observed["limit"] = {
            "window_s": [STEADY_START_S, STEADY_END_S],
            "vsense_mean_v": float(np.mean(vsense[window])),
            "vsense_min_v": float(np.min(vsense[window])),
            "vsense_max_v": float(np.max(vsense[window])),
            "all_saved_v0p2_foldback_band": bool(np.max(vsense[window]) < 0.2),
            **_cycle_peaks(t, ph, current, STEADY_START_S, STEADY_END_S),
            **_frequency(t, ph, STEADY_START_S, STEADY_END_S),
        }
    elif case == "load_step":
        observed["load_step"] = _load_step(t, out)
    return observed


def _trace(case: str, raw_path: Path, destination: Path) -> str | None:
    """Save a small derived trace before deleting a large raw waveform."""
    if case not in {"reference", "load_step"}:
        return None
    raw = read_raw(raw_path)
    t = np.asarray(raw.time_column(), dtype=float)
    out = np.asarray(raw.column("V(out)"), dtype=float)
    if case == "load_step":
        section = _range(t, 7.5e-3, 9.6e-3)
        t, out = t[section], out[section]
    if not len(t):
        return None
    targets = np.linspace(float(t[0]), float(t[-1]), 1001)
    values = np.interp(targets, t, out)
    lines = [
        "time_s,vout_v",
        *(f"{a:.9g},{b:.9g}" for a, b in zip(targets, values, strict=True)),
    ]
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return sha256(destination)


def _run_case(
    case: str,
    model: str,
    *,
    exe: Path,
    ours_lib: Path,
    ti_lib: Path,
    out_dir: Path,
    keep_raw: bool,
    timeout_s: float,
) -> dict[str, object]:
    lib = ours_lib if model == "ours" else ti_lib
    folder = out_dir / f"{case}-{model}"
    folder.mkdir(parents=True, exist_ok=True)
    deck_text, common_hash = _deck(case, model, lib)
    deck = folder / f"{case}-{model}.cir"
    deck.write_text(deck_text, encoding="utf-8", newline="\n")
    result: dict[str, object] = {
        "case": case,
        "model": model,
        "recorded_utc": datetime.now(UTC).isoformat(),
        "deck": str(deck),
        "deck_sha256": sha256(deck),
        "common_circuit_sha256": common_hash,
        "model_sha256": sha256(lib),
        "script_sha256": sha256(Path(__file__)),
        "ltspice_exe": str(exe),
        "ltspice_exe_sha256": sha256(exe),
        "status": "RUN_FAILED",
    }
    started = time.perf_counter()
    run = None
    try:
        run = run_batch(exe, deck, folder, timeout_s=timeout_s)
        result.update(
            {
                "ltspice_wall_s": round(run.wall_s, 3),
                "elapsed_s": round(time.perf_counter() - started, 3),
                "exit_code": run.exit_code,
                "timed_out": run.timed_out,
                "simulator_observed": run.observed(),
                "log_sha256": sha256(run.log_path) if run.log_path else None,
                "raw_sha256": sha256(run.raw_path) if run.raw_path else None,
                "raw_bytes": run.raw_path.stat().st_size if run.raw_path else None,
                "op_raw_sha256": sha256(run.op_raw_path) if run.op_raw_path else None,
                "op_raw_bytes": run.op_raw_path.stat().st_size if run.op_raw_path else None,
            }
        )
        if run.ok and run.raw_path is not None:
            result["measurements"] = _measure(case, run.raw_path)
            trace_path = folder / f"{case}-{model}-trace.csv"
            trace_hash = _trace(case, run.raw_path, trace_path)
            if trace_hash:
                result["trace_sha256"] = trace_hash
                result["trace"] = str(trace_path)
            result["status"] = "MEASURED"
        else:
            result["reason"] = "LTspice did not finish with a readable raw waveform"
    except Exception as exc:
        result["reason"] = f"{type(exc).__name__}: {exc}"
        result["elapsed_s"] = round(time.perf_counter() - started, 3)
    finally:
        if run is not None:
            if not keep_raw:
                for artifact in (run.raw_path, run.op_raw_path):
                    if artifact is not None:
                        artifact.unlink(missing_ok=True)
            result["raw_retained"] = keep_raw
    target = folder / f"{case}-{model}.json"
    target.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ltspice-exe", type=Path, required=True)
    parser.add_argument("--ti-lib", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cases", choices=CASES, nargs="+", default=list(CASES))
    parser.add_argument("--models", choices=MODELS, nargs="+", default=list(MODELS))
    parser.add_argument("--keep-raw", action="store_true")
    parser.add_argument(
        "--emit-only", action="store_true", help="Write decks and manifest; do not run LTspice"
    )
    parser.add_argument("--timeout-s", type=float, default=900.0)
    return parser.parse_args()


def _manifest(out: Path, provenance: dict[str, object]) -> None:
    """Collect independently completed runs without guessing at missing measurements."""
    decks: list[dict[str, object]] = []
    runs: list[dict[str, object]] = []
    for case in CASES:
        for model in MODELS:
            folder = out / f"{case}-{model}"
            deck = folder / f"{case}-{model}.cir"
            result = folder / f"{case}-{model}.json"
            if deck.is_file():
                decks.append(
                    {
                        "case": case,
                        "model": model,
                        "deck": str(deck),
                        "deck_sha256": sha256(deck),
                        "common_circuit_sha256": hashlib.sha256(
                            _common_circuit(case).encode("utf-8")
                        ).hexdigest(),
                    }
                )
            if result.is_file():
                entry = json.loads(result.read_text(encoding="utf-8"))
                expected = (
                    provenance["fresh_model_sha256"]
                    if model == "ours"
                    else provenance["ti_library_sha256"]
                )
                if (
                    entry.get("model_sha256") == expected
                    and entry.get("script_sha256") == provenance["script_sha256"]
                    and entry.get("deck_sha256") == (sha256(deck) if deck.is_file() else None)
                ):
                    runs.append(entry)
    payload = {
        "schema_version": 1,
        "provenance": provenance,
        "decks": decks,
        "runs": runs,
        "runs_measured": sum(run["status"] == "MEASURED" for run in runs),
        "expected_runs": len(CASES) * len(MODELS),
        "missing_runs": [
            f"{case}-{model}"
            for case in CASES
            for model in MODELS
            if not any(run["case"] == case and run["model"] == model for run in runs)
        ],
    }
    (out / "manifest.json").write_text(
        json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def main() -> int:
    args = _args()
    exe = args.ltspice_exe.resolve(strict=True)
    ti_lib = args.ti_lib.resolve(strict=True)
    out = args.out.resolve()
    if len(str(out)) > 145:
        raise ValueError("Choose a short --out path for LTspice on Windows")
    if math.isnan(args.timeout_s) or args.timeout_s <= 0:
        raise ValueError("--timeout-s must be positive")
    out.mkdir(parents=True, exist_ok=True)
    spec = load_tps54320_spec(
        SPEC / "requirements.json", SPEC / "bindings.json", part=PART, subckt=PART
    )
    seed = seed_from_spec(spec)
    if seed is None or seed.ports != (
        "BOOT",
        "VIN",
        "EN",
        "SS",
        "VSENSE",
        "COMP",
        "GND",
        "PH",
        "POWERPAD",
    ):
        raise ValueError("Frozen TPS54332 spec did not render the expected nine-port model")
    ours_lib = out / f"{PART}-fresh.lib"
    ours_lib.write_text(seed.library_text, encoding="utf-8", newline="\n")
    provenance = {
        "fresh_model_sha256": sha256(ours_lib),
        "fresh_model_spec_digest": seed.spec_digest,
        "frozen_requirements_sha256": sha256(SPEC / "requirements.json"),
        "frozen_bindings_sha256": sha256(SPEC / "bindings.json"),
        "ti_library_sha256": sha256(ti_lib),
        "script_sha256": sha256(Path(__file__)),
        "ti_reference_passives": "docs/evidence/2026-09-24/vendor-reference/tps54332/tps54332_ti_reference.cir",
        "vout_target_from_ti_divider_v": VOUT_TARGET_V,
        "soft_start_15nf_at_2ua_s": 15e-9 * VREF_V / 2e-6,
        "temperature_c": 25,
        "gain_fit_rule": (
            "Record 0.50-1.10 V at 0.05 V steps. Fit only 0.50<V(COMP)<=0.90, "
            ">=10 cycles in each of two windows, early/late peak difference "
            "<=max(0.05 A,5%), and points before a measured saturation plateau "
            "(two successive increments <0.12 A after current >1 A). At least three "
            "fit points are required. A plateau is only a candidate current limit until "
            "it agrees with independent overload/short measurements."
        ),
        "short_foldback_reference_hz": 125000,
        "short_foldback_basis": "VSENSE<0.2 V gives FSW/8 at nominal FSW=1 MHz (TI Table 7-2)",
        "verdict": "UNJUDGED; compare observed values to cited requirements and TI references",
    }
    (out / "provenance.json").write_text(
        json.dumps(provenance, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    failures = 0
    for case in args.cases:
        for model in args.models:
            if args.emit_only:
                folder = out / f"{case}-{model}"
                folder.mkdir(parents=True, exist_ok=True)
                deck = folder / f"{case}-{model}.cir"
                library = ours_lib if model == "ours" else ti_lib
                deck_text, _ = _deck(case, model, library)
                deck.write_text(deck_text, encoding="utf-8", newline="\n")
                print(
                    json.dumps(
                        {
                            "case": case,
                            "model": model,
                            "status": "DECK_ONLY",
                            "deck_sha256": sha256(deck),
                        }
                    ),
                    flush=True,
                )
                _manifest(out, provenance)
                continue
            result = _run_case(
                case,
                model,
                exe=exe,
                ours_lib=ours_lib,
                ti_lib=ti_lib,
                out_dir=out,
                keep_raw=args.keep_raw,
                timeout_s=args.timeout_s,
            )
            _manifest(out, provenance)
            print(
                json.dumps(
                    {
                        "case": case,
                        "model": model,
                        "status": result["status"],
                        "ltspice_wall_s": result.get("ltspice_wall_s"),
                        "reason": result.get("reason"),
                    }
                ),
                flush=True,
            )
            failures += result["status"] != "MEASURED"
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
