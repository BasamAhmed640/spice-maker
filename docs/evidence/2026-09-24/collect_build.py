"""Copy one finished build's reproducible evidence into this folder.

    python collect_build.py <build out dir> <runs/X.json> <runs/X.time.json> <name>

Writes generated-models/<name>/ with the published .lib, the harness report, the CLI
result, per-turn timing and token usage, every probe deck (.cir) with its LTspice log
(as UTF-8 .log.txt; the repository ignores *.log and *.raw) and SHA256SUMS.txt over the
copied files plus the .raw files that were measured (hashes only; the waveforms stay out
of Git). Nothing is re-simulated or re-judged here.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_log(path: Path) -> str:
    data = path.read_bytes()
    if len(data) > 1 and data[1:2] == b"\x00":
        return data.decode("utf-16-le", errors="replace")
    return data.decode("utf-8", errors="replace")


def main(out_dir: str, result_json: str, time_json: str, name: str) -> None:
    out = Path(out_dir)
    target = HERE / "generated-models" / name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    sums: dict[str, str] = {}
    for lib in out.glob("*.lib"):
        shutil.copy2(lib, target / lib.name)
    for name_ in ("harness-report.json", "MODEL_CARD.md"):
        if (out / name_).is_file():
            shutil.copy2(out / name_, target / name_)
    # A build still running at collection time has no final result yet; its finished
    # turns are still evidence, and the summary says it was collected mid-run.
    result_path, time_path = Path(result_json), Path(time_json)
    result = (
        json.loads(result_path.read_text(encoding="utf-8"))
        if result_path.is_file() and result_path.stat().st_size
        else {"status": "RUNNING_AT_COLLECTION", "part": name}
    )
    timing = json.loads(time_path.read_text(encoding="utf-8")) if time_path.is_file() else {}
    turns = []
    for path in sorted(out.glob("build/candidates/*/*/result.json")):
        turn = json.loads(path.read_text(encoding="utf-8"))
        statuses: dict[str, int] = {}
        for outcome in turn["report"]["outcomes"]:
            statuses[outcome["status"]] = statuses.get(outcome["status"], 0) + 1
        turns.append(
            {
                "turn": turn["turn"],
                "elapsed_s": round(turn["elapsed_s"], 1),
                "usage": turn["usage"],
                "outcomes": statuses,
                "reused_simulation": turn.get("reused_simulation"),
                "candidate_sha256": sha256(next(path.parent.glob("*.lib"))),
            }
        )
    probes = target / "probes"
    for deck in sorted(out.glob("build/validation-cache/*/probes/*/deck.cir")):
        key = deck.parent.parent.parent.name[:12]
        folder = probes / f"{key}-{deck.parent.name}"
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(deck, folder / "deck.cir")
        log = deck.with_suffix(".log")
        if log.is_file():
            (folder / "deck.log.txt").write_text(read_log(log), encoding="utf-8")
            sums[f"{folder.relative_to(target).as_posix()}/deck.log (original bytes)"] = sha256(
                log
            )
        for raw in deck.parent.glob("*.raw"):
            sums[f"{folder.relative_to(target).as_posix()}/{raw.name} (not copied)"] = sha256(raw)
    summary = {
        "part": result.get("part"),
        "status": result.get("status"),
        "counts": result.get("counts"),
        "detail": result.get("detail"),
        "iterations": result.get("iterations"),
        "wall_time_s": timing.get("elapsed_s"),
        "exit_code": timing.get("rc"),
        "turns": turns,
        "history": result.get("history"),
    }
    (target / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for path in sorted(target.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            sums[path.relative_to(target).as_posix()] = sha256(path)
    (target / "SHA256SUMS.txt").write_text(
        "".join(f"{digest}  {name_}\n" for name_, digest in sorted(sums.items())),
        encoding="utf-8",
    )
    print(json.dumps({k: summary[k] for k in ("part", "status", "counts", "wall_time_s")}))


if __name__ == "__main__":
    main(*sys.argv[1:5])
