r"""Secondary, deterministic fit of a measured M1 TPS54332 gain sweep.

This analyzes JSON emitted by ``tps54332_m1.py``. It never runs LTspice or
changes the measured records. The selected gain segment is a secondary
analysis, distinct from the acquisition script's predeclared fit.

Example::

    python tools/tps54332_m1_fit.py --gain-json C:\m1\gain-ti\gain-ti.json --overload-json C:\m1\overload-ti\overload-ti.json --short-json C:\m1\short-ti\short-ti.json --out C:\m1\gain-ti-fit.json

A flat high-current tail is only a candidate current-limit plateau. Even
when independent overload and short records are supplied, this tool reports
their measurements for review and never declares a current-limit value.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from itertools import pairwise
from pathlib import Path
from statistics import median
from typing import Any


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_record(path: Path, expected_case: str, model: str | None = None) -> dict[str, Any]:
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError(f"{path}: expected a JSON object")
    if record.get("case") != expected_case or record.get("status") != "MEASURED":
        raise ValueError(f"{path}: expected a MEASURED {expected_case} record")
    if model is not None and record.get("model") != model:
        raise ValueError(f"{path}: expected model {model!r}")
    return record


def _quality_reasons(point: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    comp = _finite_number(point.get("comp_v"))
    peak = _finite_number(point.get("peak_a"))
    early = _finite_number(point.get("early_peak_a"))
    delta = _finite_number(point.get("stability_delta_a"))
    cycles = _finite_number(point.get("cycles"))
    if comp is None:
        reasons.append("missing_or_nonfinite_comp_v")
    elif comp <= 0.5:
        reasons.append("threshold_comp_v_le_0p50")
    if peak is None:
        reasons.append("missing_or_nonfinite_peak_a")
    if cycles is None or cycles < 10:
        reasons.append("fewer_than_10_late_cycles")
    if point.get("stable") is not True:
        reasons.append("acquisition_marked_unstable_or_too_few_early_cycles")
    if early is None or delta is None or peak is None:
        reasons.append("missing_early_late_stability_measurement")
    elif delta > max(0.05, 0.05 * abs(peak)):
        reasons.append("early_late_peak_difference_exceeds_settling_tolerance")
    return reasons


def _linear_fit(values: list[tuple[float, float]]) -> tuple[float, float, float]:
    count = len(values)
    xmean = sum(x for x, _ in values) / count
    ymean = sum(y for _, y in values) / count
    denominator = sum((x - xmean) ** 2 for x, _ in values)
    if denominator <= 0:
        raise ValueError("duplicate or degenerate COMP levels")
    slope = sum((x - xmean) * (y - ymean) for x, y in values) / denominator
    intercept = ymean - slope * xmean
    residual = max(abs(y - (slope * x + intercept)) for x, y in values)
    return slope, intercept, residual


def _segment_fit(
    points: list[dict[str, Any]], start: int, stop: int
) -> tuple[float, float, float] | None:
    values = [(float(p["comp_v"]), float(p["peak_a"])) for p in points[start:stop]]
    slopes = [(y1 - y0) / (x1 - x0) for (x0, y0), (x1, y1) in pairwise(values)]
    middle = median(slopes)
    if middle <= 0 or any(s <= 0 or abs(s - middle) > 0.20 * middle for s in slopes):
        return None
    slope, intercept, residual = _linear_fit(values)
    span = max(y for _, y in values) - min(y for _, y in values)
    if slope <= 0 or residual > max(0.10, 0.05 * span):
        return None
    return slope, intercept, residual


def _fault_summary(path: Path | None, case: str, model: str) -> dict[str, Any]:
    if path is None:
        return {"status": "NOT_SUPPLIED"}
    record = _read_record(path, case, model)
    observed = record.get("measurements", {}).get("limit", {})
    if not isinstance(observed, dict):
        raise ValueError(f"{path}: missing limit measurements")
    cycles = _finite_number(observed.get("cycles"))
    peak = _finite_number(observed.get("peak_a"))
    if cycles is None or cycles < 10 or peak is None:
        raise ValueError(f"{path}: insufficient measured limit cycles or peak")
    return {
        "status": "MEASURED_FOR_REVIEW",
        "record_sha256": _sha256(path),
        "cycles": int(cycles),
        "peak_a": peak,
        "peak_spread_a": _finite_number(observed.get("peak_spread_a")),
        "frequency_hz": _finite_number(observed.get("frequency_hz")),
        "vsense_mean_v": _finite_number(observed.get("vsense_mean_v")),
    }


def analyze(
    gain_record: dict[str, Any],
    *,
    gain_sha256: str,
    overload: dict[str, Any],
    short: dict[str, Any],
) -> dict[str, Any]:
    """Choose the longest contiguous positive-slope, internally linear segment."""
    gain = gain_record.get("measurements", {}).get("gain", {})
    raw_points = gain.get("points") if isinstance(gain, dict) else None
    if not isinstance(raw_points, list) or not raw_points:
        raise ValueError("gain record has no measured points")
    if not all(isinstance(point, dict) for point in raw_points):
        raise ValueError("gain points must be JSON objects")
    points: list[dict[str, Any]] = raw_points
    levels = [_finite_number(p.get("comp_v")) for p in points]
    if any(a is not None and b is not None and a >= b for a, b in pairwise(levels)):
        raise ValueError("COMP levels must increase strictly")

    reasons = [_quality_reasons(point) for point in points]
    valid = [not reason for reason in reasons]
    measured_peaks = [float(points[i]["peak_a"]) for i in range(len(points)) if valid[i]]
    peak_max = max(measured_peaks) if measured_peaks else None

    # The candidate tail begins at the first of three adjacent, stable,
    # high-current points with two nearly flat successive increments.
    plateau_at: int | None = None
    if peak_max is not None and peak_max > 0:
        for start in range(len(points) - 2):
            if not all(valid[start : start + 3]):
                continue
            p = [float(points[i]["peak_a"]) for i in range(start, start + 3)]
            if min(p) >= 0.80 * peak_max and all(
                abs(next_peak - peak) < 0.12 for peak, next_peak in pairwise(p)
            ):
                plateau_at = start
                break
    if plateau_at is not None:
        for index in range(plateau_at, len(points)):
            if valid[index]:
                reasons[index].append("candidate_high_current_plateau_or_later")
                valid[index] = False

    best: tuple[int, int, float, float, float] | None = None
    for start in range(len(points)):
        if not valid[start]:
            continue
        for stop in range(start + 3, len(points) + 1):
            if not all(valid[start:stop]):
                break
            fitted = _segment_fit(points, start, stop)
            if fitted is None:
                continue
            if best is None or stop - start > best[1] - best[0]:
                best = (start, stop, *fitted)

    selected_indices = set(range(best[0], best[1])) if best else set()
    for index, is_valid in enumerate(valid):
        if is_valid and index not in selected_indices:
            reasons[index].append("outside_selected_contiguous_linear_segment")
    plateau = {
        "detected": plateau_at is not None,
        "from_comp_v": points[plateau_at]["comp_v"] if plateau_at is not None else None,
        "three_point_peak_a": (
            [points[i]["peak_a"] for i in range(plateau_at, plateau_at + 3)]
            if plateau_at is not None
            else None
        ),
        "maximum_stable_peak_a": peak_max,
        "status": "CANDIDATE_ONLY" if plateau_at is not None else "NOT_OBSERVED",
    }
    cross_check = {
        "overload": overload,
        "short": short,
        "status": (
            "BOTH_AVAILABLE_FOR_INDEPENDENT_REVIEW"
            if overload["status"] == short["status"] == "MEASURED_FOR_REVIEW"
            else "INCOMPLETE"
        ),
        "interpretation": (
            "Compare the candidate plateau with both independent fault waveforms; "
            "no automatic current-limit verdict is made."
        ),
    }
    result: dict[str, Any] = {
        "analysis": "SECONDARY_POST_ACQUISITION_GAIN_FIT",
        "gain_record_sha256": gain_sha256,
        "model": gain_record["model"],
        "rule": (
            "Exclude COMP<=0.50 V, unstable or <10-cycle points, and any high-current tail "
            "starting with three adjacent stable points >=80% of the maximum with two "
            "successive absolute peak changes <0.12 A. Among remaining contiguous segments "
            "of >=3 points, require positive step slopes within +/-20% of their median and "
            "maximum linear-regression residual <=max(0.10 A,5% of segment current span). "
            "Choose longest, then earliest."
        ),
        "excluded_points": [
            {"index": i, "comp_v": point.get("comp_v"), "reasons": reasons[i]}
            for i, point in enumerate(points)
            if reasons[i]
        ],
        "candidate_plateau": plateau,
        "independent_fault_cross_check": cross_check,
        "current_limit_verdict": "NOT_MADE",
    }
    if best is None:
        result.update(
            {
                "status": "NO_VALID_FIT",
                "reason": "No contiguous >=3-point segment satisfies the predeclared secondary rule",
                "selected_points": [],
            }
        )
    else:
        start, stop, slope, intercept, residual = best
        result.update(
            {
                "status": "FIT_MEASURED",
                "selected_points": [
                    {
                        "index": i,
                        "comp_v": points[i]["comp_v"],
                        "peak_a": points[i]["peak_a"],
                        "cycles": points[i]["cycles"],
                    }
                    for i in range(start, stop)
                ],
                "slope_a_per_v": slope,
                "intercept_a": intercept,
                "fit_max_abs_residual_a": residual,
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gain-json", type=Path, required=True)
    parser.add_argument("--overload-json", type=Path)
    parser.add_argument("--short-json", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    gain_record = _read_record(args.gain_json, "gain")
    model = gain_record.get("model")
    if model not in {"ours", "ti"}:
        raise ValueError("gain model must be 'ours' or 'ti'")
    result = analyze(
        gain_record,
        gain_sha256=_sha256(args.gain_json),
        overload=_fault_summary(args.overload_json, "overload", model),
        short=_fault_summary(args.short_json, "short", model),
    )
    result["fit_script_sha256"] = _sha256(Path(__file__))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
