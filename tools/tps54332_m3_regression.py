r"""Rerun the frozen 19-row TPS54332 harness after the M3 pad change.

The script requires explicit local input and simulator paths. It creates a fresh
template model, performs no inference/network request, and compares each observed
row with the committed checkpoint. It records file hashes before removing raw
waveforms from the ignored runs directory. A comparison is evidence, not an
automatic system-verified verdict.
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

from boardmodeler.authoring.harness import judge_characteristic, run_harness
from boardmodeler.authoring.spec import load_tps54320_spec
from boardmodeler.models.buck_switching import seed_from_spec

REPO = Path(__file__).resolve().parents[1]
PART = "TPS54332DDA"
# Frozen-input identities recorded in the committed M1 evidence report and the
# TPS54332 template row verdicts. Row IDs alone cannot guard changed limits.
FROZEN_REQUIREMENTS_SHA256 = "3884fd76976e071cd2ea81d5db64cb1bde17aecbaccc8f87399e7448646cc060"
FROZEN_BINDINGS_SHA256 = "1b3d45e8977948ce2ed7f1079db2aaa598b543cd65a83b697af8455d6345ad57"
FROZEN_SPEC_DIGEST = "499ed2442781bd4cad22041e7cb028bb6af9ec3df7bd34fe053750b9782b14d7"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ltspice-exe", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument("--keep-raw", action="store_true")
    return parser.parse_args()


def _inputs(args: argparse.Namespace) -> tuple[Path, Path, Path, Path, Path]:
    exe = args.ltspice_exe.resolve(strict=True)
    requirements = args.requirements.resolve(strict=True)
    bindings = args.bindings.resolve(strict=True)
    baseline = args.baseline.resolve(strict=True)
    out = args.out.resolve()
    if not all(path.is_file() for path in (exe, requirements, bindings, baseline)):
        raise ValueError("all input paths must name existing files")
    if not out.is_relative_to((REPO / "runs").resolve()):
        raise ValueError("output must be inside this repository's ignored runs/ directory")
    if len(str(out)) > 130:
        raise ValueError("choose a shorter output path for LTspice on Windows")
    if _sha256(requirements) != FROZEN_REQUIREMENTS_SHA256:
        raise ValueError("requirements differ from the frozen M1 input")
    if _sha256(bindings) != FROZEN_BINDINGS_SHA256:
        raise ValueError("bindings differ from the frozen M1 input")
    if not math.isfinite(args.timeout_s) or args.timeout_s <= 0:
        raise ValueError("timeout must be positive and finite")
    if out.exists() and any(out.iterdir()):
        raise ValueError("output directory must be new or empty to avoid stale artifacts")
    return exe, requirements, bindings, baseline, out


def _baseline_rows(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) != 19:
        raise ValueError("checkpoint does not contain exactly 19 verdict rows")
    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            raise ValueError("malformed checkpoint verdict row")
        identifier, status = row[:2]
        if identifier in result or status not in {"PASS", "FAIL", "UNKNOWN"}:
            raise ValueError("duplicate row or invalid checkpoint status")
        result[str(identifier)] = str(status)
    return result


def _artifact_hashes(out: Path) -> dict[str, str]:
    names = ("*.cir", "*.log", "*.raw", "*.lib")
    files = sorted({path for name in names for path in out.rglob(name) if path.is_file()})
    return {path.relative_to(out).as_posix(): _sha256(path) for path in files}


def _save(out: Path, payload: dict[str, Any]) -> None:
    (out / "verdicts.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    args = _args()
    exe, requirements, bindings, baseline_path, out = _inputs(args)
    checkpoint = _baseline_rows(baseline_path)
    spec = load_tps54320_spec(requirements, bindings, part=PART, subckt=PART)
    if spec.digest() != FROZEN_SPEC_DIGEST:
        raise ValueError("loaded specification differs from the frozen TPS54332 template")
    covered = spec.covered()
    if len(covered) != 19 or {row.char_id for row in covered} != set(checkpoint):
        raise ValueError("frozen 19-row spec differs from the checkpoint ID set")
    seed = seed_from_spec(spec)
    if seed is None or seed.part != PART:
        raise ValueError("frozen TPS54332 template did not render")
    out.mkdir(parents=True, exist_ok=True)
    model = seed.write(out / f"{PART}-fresh-m3.lib")
    started = time.perf_counter()
    report = run_harness(
        model_lib=model,
        subckt=PART,
        spec=spec,
        workdir=out / "harness",
        ltspice=exe,
        timeout_s=args.timeout_s,
    )
    rows = []
    for characteristic in covered:
        matches = [item for item in report.outcomes if characteristic.char_id in item.char_ids]
        if len(matches) != 1:
            raise ValueError(f"{characteristic.char_id}: expected one harness outcome")
        verdict, detail = judge_characteristic(characteristic, matches[0])
        rows.append(
            {
                "id": characteristic.char_id,
                "before": checkpoint[characteristic.char_id],
                "after": verdict,
                "changed": verdict != checkpoint[characteristic.char_id],
                "judged": matches[0].judged,
                "detail": detail,
                "unknown_reason": matches[0].unknown_reason,
                "artifacts": matches[0].artifacts,
            }
        )
    hashes = _artifact_hashes(out)
    result = {
        "schema_version": 1,
        "part": PART,
        "spec_digest": spec.digest(),
        "requirements_sha256": _sha256(requirements),
        "bindings_sha256": _sha256(bindings),
        "baseline_sha256": _sha256(baseline_path),
        "ltspice_exe_sha256": _sha256(exe),
        "model_sha256": _sha256(model),
        "script_sha256": _sha256(Path(__file__)),
        "elapsed_s": round(time.perf_counter() - started, 3),
        "counts": {
            name: sum(row["after"] == name for row in rows) for name in ("PASS", "FAIL", "UNKNOWN")
        },
        "changed_rows": [row["id"] for row in rows if row["changed"]],
        "rows": rows,
        "artifact_hashes": hashes,
        "raw_retained": args.keep_raw,
        "verdict": "OBSERVED; do not infer system verification from this regression",
    }
    if not args.keep_raw:
        for path in out.rglob("*.raw"):
            path.unlink()
    _save(out, result)
    print(
        json.dumps(
            {
                "counts": result["counts"],
                "changed_rows": result["changed_rows"],
                "elapsed_s": result["elapsed_s"],
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
