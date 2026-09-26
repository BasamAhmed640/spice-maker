r"""Rerun the frozen LM358 qualification through the current simulator harness.

The saved LM358 build has 19 probe cases covering 32 characteristic rows. This
runner requires the exact saved spec, model, and report, then compares both case
identities and each row's verdict. It makes no inference or network request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Any

os.environ["BOARDMODELER_NO_NETWORK"] = "1"
os.environ["BOARDMODELER_HARNESS_WORKERS"] = "1"

from boardmodeler.authoring.harness import HarnessReport, judge_characteristic, run_harness
from boardmodeler.authoring.spec import SpecSet

REPO = Path(__file__).resolve().parents[1]
PART = "LM358"
SPEC_SHA256 = "761c287251cf31a7e799aa0b3d65cb0bcd867015d8fcca252accd52abbed910a"
MODEL_SHA256 = "da0a66609466b92faf4bd338bb14f91905db5b3d53e90a3af8b0b0fde56426e3"
BASELINE_SHA256 = "bf28a11596928c6938f02701df516e978a2e831fbd53f39a4d081587059bd541"
SPEC_DIGEST = "0fb03ad8fd52101eb40d5d1420bd03421d96ab18e949c713f1062c9260f594cc"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ltspice-exe", type=Path, required=True)
    parser.add_argument("--characteristics", type=Path, required=True)
    parser.add_argument("--model-lib", type=Path, required=True)
    parser.add_argument("--baseline-report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument("--keep-raw", action="store_true")
    return parser.parse_args()


def _inputs(args: argparse.Namespace) -> tuple[Path, Path, Path, Path, Path]:
    exe = args.ltspice_exe.resolve(strict=True)
    characteristics = args.characteristics.resolve(strict=True)
    model = args.model_lib.resolve(strict=True)
    baseline = args.baseline_report.resolve(strict=True)
    out = args.out.resolve()
    if not all(path.is_file() for path in (exe, characteristics, model, baseline)):
        raise ValueError("LTspice, characteristics, model, and baseline must be files")
    if not out.is_relative_to((REPO / "runs").resolve()):
        raise ValueError("output must be inside this repository's ignored runs/ directory")
    if len(str(out)) > 130:
        raise ValueError("choose a shorter output path for LTspice on Windows")
    if not math.isfinite(args.timeout_s) or args.timeout_s <= 0:
        raise ValueError("timeout must be positive and finite")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError("output directory must be new or empty to avoid stale artifacts")
    return exe, characteristics, model, baseline, out


def _case_identity(outcome: Any) -> tuple[str, str, tuple[str, ...]]:
    return Path(outcome.run_dir).name, outcome.probe_id, outcome.char_ids


def _checked_baseline(
    spec: SpecSet, model_sha: str, baseline: HarnessReport
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    if spec.part != PART or spec.subckt != PART or spec.digest() != SPEC_DIGEST:
        raise ValueError("LM358 spec differs from the frozen baseline")
    if model_sha != MODEL_SHA256:
        raise ValueError("LM358 model differs from the frozen baseline")
    if (
        baseline.part != PART
        or baseline.spec_digest != SPEC_DIGEST
        or baseline.model_sha256 != MODEL_SHA256
    ):
        raise ValueError("saved LM358 report has a different spec or model identity")
    cases = tuple(
        (name, probe, tuple(char.char_id for char in chars)) for name, probe, chars in spec.cases()
    )
    if len(cases) != 19 or len(spec.covered()) != 32 or len(baseline.outcomes) != 19:
        raise ValueError("LM358 baseline must have 19 cases and 32 covered rows")
    if tuple(_case_identity(outcome) for outcome in baseline.outcomes) != cases:
        raise ValueError("saved LM358 case identities differ from the frozen spec")
    if any(outcome.status != "PASS" for outcome in baseline.outcomes):
        raise ValueError("saved LM358 baseline does not have 19 passing cases")
    for characteristic in spec.covered():
        matches = [
            outcome for outcome in baseline.outcomes if characteristic.char_id in outcome.char_ids
        ]
        if len(matches) != 1 or judge_characteristic(characteristic, matches[0])[0] != "PASS":
            raise ValueError(f"{characteristic.char_id}: baseline row is not uniquely passing")
    return cases


def _artifact_hashes(out: Path) -> dict[str, str]:
    patterns = ("*.cir", "*.log", "*.raw", "*.lib")
    files = sorted({path for pattern in patterns for path in out.rglob(pattern) if path.is_file()})
    return {path.relative_to(out).as_posix(): _sha256(path) for path in files}


def main() -> int:
    args = _args()
    exe, characteristics, model, baseline_path, out = _inputs(args)
    if _sha256(characteristics) != SPEC_SHA256:
        raise ValueError("characteristics file differs from the frozen LM358 input")
    model_sha = _sha256(model)
    if _sha256(baseline_path) != BASELINE_SHA256:
        raise ValueError("saved LM358 report differs from the frozen baseline")
    spec = SpecSet.from_json(characteristics.read_text(encoding="utf-8"))
    baseline = HarnessReport.from_json(baseline_path.read_text(encoding="utf-8"))
    cases = _checked_baseline(spec, model_sha, baseline)
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    report = run_harness(
        model_lib=model,
        subckt=spec.subckt,
        spec=spec,
        workdir=out / "harness",
        ltspice=exe,
        timeout_s=args.timeout_s,
    )
    if (
        report.part != PART
        or report.spec_digest != SPEC_DIGEST
        or report.model_sha256 != MODEL_SHA256
        or tuple(_case_identity(outcome) for outcome in report.outcomes) != cases
    ):
        raise ValueError("rerun report differs from the frozen LM358 case identities")
    rows = []
    for characteristic in spec.covered():
        before = next(
            outcome for outcome in baseline.outcomes if characteristic.char_id in outcome.char_ids
        )
        after = next(
            outcome for outcome in report.outcomes if characteristic.char_id in outcome.char_ids
        )
        before_status, _before_detail = judge_characteristic(characteristic, before)
        after_status, after_detail = judge_characteristic(characteristic, after)
        rows.append(
            {
                "id": characteristic.char_id,
                "before": before_status,
                "after": after_status,
                "changed": after_status != before_status,
                "detail": after_detail,
                "judged": after.judged,
                "unknown_reason": after.unknown_reason,
                "artifacts": after.artifacts,
            }
        )
    report_path = out / "harness-report.json"
    report_path.write_text(report.to_json(), encoding="utf-8", newline="\n")
    hashes = _artifact_hashes(out)
    result = {
        "schema_version": 1,
        "part": PART,
        "spec_digest": SPEC_DIGEST,
        "characteristics_sha256": SPEC_SHA256,
        "model_sha256": MODEL_SHA256,
        "baseline_report_sha256": BASELINE_SHA256,
        "ltspice_exe_sha256": _sha256(exe),
        "script_sha256": _sha256(Path(__file__)),
        "elapsed_s": round(time.perf_counter() - started, 3),
        "case_counts": report.counts(),
        "row_counts": {
            status: sum(row["after"] == status for row in rows)
            for status in ("PASS", "FAIL", "UNKNOWN", "BLOCKED")
        },
        "changed_rows": [row["id"] for row in rows if row["changed"]],
        "cases": [
            {"id": case_id, "probe": probe, "char_ids": list(char_ids)}
            for case_id, probe, char_ids in cases
        ],
        "rows": rows,
        "artifact_hashes": hashes,
        "raw_retained": args.keep_raw,
    }
    if not args.keep_raw:
        for path in out.rglob("*.raw"):
            path.unlink()
    (out / "verdicts.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "case_counts": result["case_counts"],
                "row_counts": result["row_counts"],
                "changed_rows": result["changed_rows"],
                "elapsed_s": result["elapsed_s"],
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
