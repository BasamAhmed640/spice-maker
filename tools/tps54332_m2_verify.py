r"""Run the code-built M2 TPS54332 benches with an explicitly chosen LTspice.

This script reads the already frozen local requirements and bindings, renders a
fresh model, and saves one independent record per bench. It makes no network or
AI request and never turns a generated deck into an electrical PASS. All outputs
must stay under this repository's ignored ``runs/`` directory.

Example (PowerShell)::

    .\.venv\Scripts\python.exe tools\tps54332_m2_verify.py --ltspice-exe "C:\path\to\LTspice.exe" --requirements models\T1-tps54332\spec\requirements.json --bindings models\T1-tps54332\spec\bindings.json --out runs\m2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# This verification entry point has no reason to contact an inference provider.
os.environ["BOARDMODELER_NO_NETWORK"] = "1"

from boardmodeler.authoring.buck_system_fixtures import (
    BuckBench,
    build_buck_system_benches,
    evaluate_waveform,
    render_deck,
)
from boardmodeler.authoring.spec import load_tps54320_spec
from boardmodeler.models.buck_switching import seed_from_spec
from boardmodeler.simulation.ltspice import run_batch
from boardmodeler.simulation.raw import read_raw

REPO = Path(__file__).resolve().parents[1]
PART = "TPS54332DDA"
CASES = ("gain", "limit_min", "limit_max", "ripple", "edge", "load_step", "startup")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ltspice-exe", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cases", choices=CASES, nargs="+", default=list(CASES))
    parser.add_argument("--timeout-s", type=float, default=300.0)
    parser.add_argument("--keep-raw", action="store_true")
    parser.add_argument("--emit-only", action="store_true", help="Write decks without simulation")
    return parser.parse_args()


def _validate(args: argparse.Namespace) -> tuple[Path, Path, Path, Path]:
    exe = args.ltspice_exe.resolve(strict=True)
    requirements = args.requirements.resolve(strict=True)
    bindings = args.bindings.resolve(strict=True)
    out = args.out.resolve()
    if not exe.is_file() or not requirements.is_file() or not bindings.is_file():
        raise ValueError("LTspice, requirements, and bindings must be explicit files")
    if not out.is_relative_to((REPO / "runs").resolve()):
        raise ValueError("--out must be inside this repository's ignored runs/ directory")
    if len(str(out)) > 145:
        raise ValueError("Choose a shorter --out path for LTspice on Windows")
    if not math.isfinite(args.timeout_s) or args.timeout_s <= 0:
        raise ValueError("--timeout-s must be a positive finite number")
    return exe, requirements, bindings, out


def _run_one(
    bench: BuckBench,
    *,
    exe: Path,
    model: Path,
    out: Path,
    timeout_s: float,
    keep_raw: bool,
    emit_only: bool,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    folder = out / bench.name
    folder.mkdir(parents=True, exist_ok=True)
    deck = folder / f"{bench.name}.cir"
    deck.write_text(render_deck(bench, model), encoding="utf-8", newline="\n")
    record: dict[str, Any] = {
        "schema_version": 1,
        "case": bench.name,
        "kind": bench.kind,
        "recorded_utc": datetime.now(UTC).isoformat(),
        "status": "RUN_FAILED",
        "verdict": "UNJUDGED",
        "deck": str(deck),
        "deck_sha256": _sha256(deck),
        "model_sha256": provenance["fresh_model_sha256"],
        "requirements_sha256": provenance["requirements_sha256"],
        "bindings_sha256": provenance["bindings_sha256"],
        "ltspice_exe": str(exe),
        "ltspice_exe_sha256": provenance["ltspice_exe_sha256"],
        "script_sha256": provenance["script_sha256"],
        "source_rows": [asdict(row) for row in bench.source_rows],
        "bench_metadata": bench.metadata,
        "passive_source": bench.passive_source,
        "window_s": bench.window_s,
        "max_step_s": bench.max_step_s,
    }
    if emit_only:
        record["status"] = "DECK_ONLY"
        record["reason"] = "No LTspice waveform was requested"
        _write_json(folder / f"{bench.name}.json", record)
        return record
    run = None
    started = time.perf_counter()
    try:
        run = run_batch(exe, deck, folder, timeout_s=timeout_s)
        record.update(
            ltspice_wall_s=round(run.wall_s, 3),
            elapsed_s=round(time.perf_counter() - started, 3),
            exit_code=run.exit_code,
            timed_out=run.timed_out,
            simulator_observed=run.observed(),
            log_sha256=_sha256(run.log_path) if run.log_path else None,
            raw_sha256=_sha256(run.raw_path) if run.raw_path else None,
            raw_bytes=run.raw_path.stat().st_size if run.raw_path else None,
            op_raw_sha256=_sha256(run.op_raw_path) if run.op_raw_path else None,
            op_raw_bytes=run.op_raw_path.stat().st_size if run.op_raw_path else None,
        )
        if run.ok and run.raw_path:
            observed = evaluate_waveform(bench, read_raw(run.raw_path))
            record["status"] = observed.status
            record["measurements"] = observed.metrics
            record["reason"] = observed.reason
        else:
            record["reason"] = "LTspice did not finish with a readable raw waveform"
    except Exception as exc:
        record["elapsed_s"] = round(time.perf_counter() - started, 3)
        record["reason"] = f"{type(exc).__name__}: {exc}"
    finally:
        if run is not None and not keep_raw:
            for artifact in (run.raw_path, run.op_raw_path):
                if artifact is not None:
                    artifact.unlink(missing_ok=True)
        record["raw_retained"] = bool(run is not None and keep_raw)
        _write_json(folder / f"{bench.name}.json", record)
    return record


def main() -> int:
    args = _args()
    exe, requirements, bindings, out = _validate(args)
    spec = load_tps54320_spec(requirements, bindings, part=PART, subckt=PART)
    seed = seed_from_spec(spec)
    if seed is None or seed.part != PART:
        raise ValueError("Frozen TPS54332 specification did not render a model")
    benches = build_buck_system_benches(spec, requirements_path=requirements)
    by_name = {bench.name: bench for bench in benches}
    if tuple(by_name) != CASES:
        raise ValueError("M2 bench builder did not produce the seven expected cases")
    out.mkdir(parents=True, exist_ok=True)
    model = out / f"{PART}-fresh.lib"
    seed.write(model)
    provenance = {
        "schema_version": 1,
        "part": PART,
        "fresh_model_sha256": _sha256(model),
        "spec_digest": seed.spec_digest,
        "requirements_sha256": _sha256(requirements),
        "bindings_sha256": _sha256(bindings),
        "ltspice_exe_sha256": _sha256(exe),
        "script_sha256": _sha256(Path(__file__)),
        "verdict": "UNJUDGED; measured waveforms require comparison with cited requirements",
    }
    _write_json(out / "provenance.json", provenance)
    records = []
    for name in dict.fromkeys(args.cases):
        record = _run_one(
            by_name[name],
            exe=exe,
            model=model,
            out=out,
            timeout_s=args.timeout_s,
            keep_raw=args.keep_raw,
            emit_only=args.emit_only,
            provenance=provenance,
        )
        records.append(record)
        print(
            json.dumps({"case": name, "status": record["status"], "reason": record.get("reason")}),
            flush=True,
        )
    manifest = {
        "schema_version": 1,
        "provenance": provenance,
        "expected_cases": list(dict.fromkeys(args.cases)),
        "runs": records,
        "measured": sum(row["status"] == "MEASURED" for row in records),
        "unknown": sum(row["status"] == "UNKNOWN" for row in records),
        "failed": sum(row["status"] == "RUN_FAILED" for row in records),
        "deck_only": sum(row["status"] == "DECK_ONLY" for row in records),
    }
    _write_json(out / "manifest.json", manifest)
    return int(manifest["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
