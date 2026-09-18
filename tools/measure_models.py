"""Measure the model maker, hard, and write the numbers down.

```powershell
uv run python tools/measure_models.py                 # everything
uv run python tools/measure_models.py --skip-discrimination
```

What it measures, all of it from real runs of the product path:

1. **Build time and accuracy** for the committed TPS54320 fixture
   (``model build --part TPS54320 --subckt BM_REG_BUCK --datasheet <pdf>
   --requirements <json> --bindings <json> --backend scripted --no-reinforce``):
   the whole build's wall clock, the re-judge (``model test``) wall clock, the
   per-stage wall clock taken from the CLI's own flushed progress lines, and the
   full row table with the margin (absolute and percent, against the nearest
   declared bound) for every PASS row that has a numeric limit.
2. **Determinism**: the same build twice into two directories; the ``.lib`` and
   ``.asy`` are compared by sha256 and the judged row tables by value.
3. **Discrimination**: a copy of the built model in a scratch directory is
   perturbed (small: inside the row's tolerance must still PASS; outside: must
   FAIL; structural: must go UNKNOWN) and re-judged with ``model test``. Each
   perturbation's expected flips are stated *before* the run and compared with
   what the harness actually reported.
4. **Timing breakdown**: the harness records no per-probe timing, so the tool
   runs one instrumented harness pass in-process, wrapping
   ``boardmodeler.authoring.harness.run_batch`` to time every probe and count the
   LTspice invocations. The instrumented pass is cross-checked against the CLI's
   own re-judge before its numbers are used.

Writes ``build/model-measurements.json`` and ``build/model-measurements.md`` and
prints the markdown summary. The shipped fixtures are never edited: perturbed
models live under ``build/measure-mutants/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

PART = "TPS54320"
SUBCKT = "BM_REG_BUCK"
FIXTURE = REPO_ROOT / "fixtures" / "regulator" / "tps54320"
DATASHEET = FIXTURE / "originals" / "tps54320_datasheet.pdf"
REQUIREMENTS = FIXTURE / "requirements.json"
BINDINGS = FIXTURE / "probes.json"

RUN1_NAME = "measure"
RUN2_NAME = "measure-run2"
TIMING_NAME = "measure-timing"
MUTANTS_NAME = "measure-mutants"
JSON_NAME = "model-measurements.json"
MD_NAME = "model-measurements.md"

STAGES = ("read", "extract", "bind", "author", "judge", "save")

#: The harness's own numbers, read from the module so this tool cannot drift:
#: the relative slack it allows before a declared limit is called a violation,
#: and the fraction of a typical value that counts as matching it.
from boardmodeler.authoring import harness as harness_mod  # noqa: E402  (after sys.path)
from boardmodeler.authoring.spec import SpecSet  # noqa: E402  (after sys.path)

LIMIT_SLACK = float(getattr(harness_mod, "_LIMIT_SLACK", 1e-6))
TYPICAL_TOLERANCE = float(getattr(harness_mod, "_TYPICAL_TOLERANCE", 0.10))

_MEASURED_RE = re.compile(r"=\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")

#: Each perturbation states what it changes, why, and — before it runs — which
#: rows it expects to flip. A perturbation whose prediction misses is reported
#: as a miss, not quietly dropped.
MUTANTS: tuple[dict[str, Any], ...] = (
    {
        "id": "uvlo_rise_inside",
        "kind": "constant",
        "old": "UVLO_RISE=4.3",
        "new": "UVLO_RISE=4.34",
        "intent": "small defect inside the row's tolerance (+40 mV on a 500 mV window)",
        "expect_flips": [],
        "expect_why": (
            "REQ_TPS54320_ELEC_004 allows 4.0..4.5 V and REQ_TPS54320_TEMPORAL_062 allows "
            "typ 4.0 V +/-10 % (0.40 V); +40 mV keeps both satisfied, so a correct harness "
            "must keep the row PASS"
        ),
    },
    {
        "id": "uvlo_rise_outside",
        "kind": "constant",
        "old": "UVLO_RISE=4.3",
        "new": "UVLO_RISE=4.9",
        "intent": "the same constant, pushed outside the row's tolerance (+600 mV)",
        "expect_flips": ["REQ_TPS54320_ELEC_004", "REQ_TPS54320_TEMPORAL_062"],
        "expect_why": (
            "measured VIN-at-start follows UVLO_RISE, so 4.9 V exceeds the 4.5 V maximum and "
            "deviates 0.9 V from the 4.0 V typical, past the 0.40 V band"
        ),
    },
    {
        "id": "vref_inside",
        "kind": "constant",
        "old": "VREF=0.8 ",
        "new": "VREF=0.806 ",
        "intent": "small defect inside the reference window (+6 mV on a 16 mV window)",
        "expect_flips": [],
        "expect_why": "REQ_TPS54320_ELEC_020 allows 0.792..0.808 V, so 0.806 V still passes",
    },
    {
        "id": "vref_outside",
        "kind": "constant",
        "old": "VREF=0.8 ",
        "new": "VREF=0.83 ",
        "intent": "the reference pushed outside its window (+30 mV)",
        "expect_flips": ["REQ_TPS54320_ELEC_020"],
        "expect_why": "0.830 V exceeds the 0.808 V maximum the vref probe judges",
    },
    {
        "id": "en_rise_inside",
        "kind": "constant",
        "old": "EN_RISE=1.25 ",
        "new": "EN_RISE=1.255 ",
        "intent": "small defect 5 mV inside a 50 mV window",
        "expect_flips": [],
        "expect_why": "REQ_TPS54320_ELEC_010 allows 1.21..1.26 V, so 1.255 V still passes",
    },
    {
        "id": "en_rise_outside",
        "kind": "constant",
        "old": "EN_RISE=1.25 ",
        "new": "EN_RISE=1.259 ",
        "intent": "the same constant moved 4 mV further, across the row's upper bound",
        "expect_flips": ["REQ_TPS54320_ELEC_010"],
        "expect_why": (
            "the measured EN-at-start sits about 1 mV above EN_RISE, so 1.259 V lands just "
            "past the 1.26 V maximum: the smallest change this report can call out"
        ),
    },
    {
        "id": "pg_port_dropped",
        "kind": "constant",
        "old": ".subckt BM_REG_BUCK VIN EN FB PG VOUT",
        "new": ".subckt BM_REG_BUCK VIN EN FB VOUT",
        "intent": "structural: the PWRGD port disappears from the model's .subckt line",
        "expect_flips": ["REQ_TPS54320_PG_052"],
        "expect_why": (
            "the pg_threshold probe binds PG, so that one probe must report UNKNOWN while the "
            "seven probes that never touch PG still measure the model"
        ),
    },
    {
        "id": "model_body_removed",
        "kind": "wipe",
        "old": "",
        "new": "",
        "intent": "structural: the model file ships without its .subckt at all",
        "expect_flips": [
            "REQ_TPS54320_ELEC_003",
            "REQ_TPS54320_ELEC_004",
            "REQ_TPS54320_ELEC_006",
            "REQ_TPS54320_ELEC_007",
            "REQ_TPS54320_ELEC_010",
            "REQ_TPS54320_ELEC_011",
            "REQ_TPS54320_ELEC_020",
            "REQ_TPS54320_PG_052",
            "REQ_TPS54320_TEMPORAL_062",
        ],
        "expect_why": (
            "no subcircuit means no port to bind: every probe must be UNKNOWN, and a harness "
            "that answered PASS here would be recording an unobserved result"
        ),
    },
)


# ---------------------------------------------------------------------------- #
# subprocess plumbing


class MeasureError(RuntimeError):
    """A measurement could not be taken; the report says which one and why."""


class Run:
    """One observed CLI invocation: what was run, how long, and what it printed."""

    def __init__(
        self,
        argv: Sequence[str],
        exit_code: int,
        seconds: float,
        stdout: str,
        stderr: str,
        timeline: Sequence[tuple[float, str]],
        timed_out: bool,
    ) -> None:
        self.argv = list(argv)
        self.exit_code = exit_code
        self.seconds = seconds
        self.stdout = stdout
        self.stderr = stderr
        self.timeline = list(timeline)
        self.timed_out = timed_out

    def payload(self) -> dict[str, Any]:
        return {
            "command": " ".join(self.argv),
            "exit_code": self.exit_code,
            "wall_clock_s": round(self.seconds, 3),
            "timed_out": self.timed_out,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def run(argv: Sequence[str], *, cwd: Path, timeout: float) -> Run:
    """Run ``argv``, stamping every stdout line so stage times can be recovered."""
    started = time.perf_counter()
    proc = subprocess.Popen(
        list(argv),
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    deadline_reached = threading.Event()
    finish = threading.Event()

    def watch() -> None:
        if not finish.wait(timeout):
            deadline_reached.set()
            proc.kill()

    watchdog = threading.Thread(target=watch, daemon=True)
    watchdog.start()

    timeline: list[tuple[float, str]] = []
    stderr_lines: list[str] = []

    def drain_err() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line.rstrip("\n"))

    err_thread = threading.Thread(target=drain_err, daemon=True)
    err_thread.start()

    stdout_lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        text = line.rstrip("\n")
        timeline.append((time.perf_counter() - started, text))
        stdout_lines.append(text)
    exit_code = proc.wait()
    seconds = time.perf_counter() - started
    finish.set()
    err_thread.join(timeout=10)

    return Run(
        argv=list(argv),
        exit_code=exit_code,
        seconds=seconds,
        stdout="\n".join(stdout_lines),
        stderr="\n".join(stderr_lines),
        timeline=timeline,
        timed_out=deadline_reached.is_set(),
    )


def cli_prefix(explicit: str | None) -> list[str]:
    """The ``boardmodeler`` command to run, as a user would type it."""
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise MeasureError(f"--cli {explicit} is not a file")
        return [str(path)]
    exe = Path(sys.executable)
    for name in ("boardmodeler.exe", "boardmodeler"):
        candidate = exe.with_name(name)
        if candidate.is_file():
            return [str(candidate)]
    found = shutil.which("boardmodeler")
    if found:
        return [found]
    raise MeasureError("the boardmodeler CLI was not found; pass --cli <path>")


# ---------------------------------------------------------------------------- #
# artifacts


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MeasureError(f"{path} could not be read: {exc}") from None
    except json.JSONDecodeError as exc:
        raise MeasureError(f"{path} is not JSON: {exc}") from None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def decks_under(root: Path) -> list[Path]:
    """Every deck the harness wrote under ``root`` — one per LTspice invocation."""
    return sorted(root.glob("probes/*/deck.cir"))


def stage_table(run: Run) -> list[dict[str, Any]]:
    """Per-stage wall clock from the CLI's flushed ``<stage> <status>`` lines."""
    stamped: list[tuple[float, str, str]] = []
    for at, line in run.timeline:
        fields = line.strip().split()
        if len(fields) >= 2 and fields[0] in STAGES:
            stamped.append((at, fields[0], fields[1]))
    table: list[dict[str, Any]] = []
    for index, (at, stage, status) in enumerate(stamped):
        end = stamped[index + 1][0] if index + 1 < len(stamped) else run.seconds
        table.append(
            {
                "stage": stage,
                "status": status,
                "started_s": round(at, 3),
                "seconds": round(end - at, 3),
            }
        )
    return table


def measured_number(measured: str) -> float | None:
    """The number in a row's ``key = value unit`` measurement, or ``None``."""
    match = _MEASURED_RE.search(measured or "")
    return None if match is None else float(match.group(1))


def _row_key(row: Mapping[str, Any]) -> tuple[Any, Any, Any, Any, Any]:
    """What a row table is compared on, value for value."""
    return (row["req_id"], row["required"], row["measured"], row["status"], row["page"])


def row_margin(char: Mapping[str, Any], value: float) -> dict[str, Any] | None:
    """How far ``value`` sits from the nearest declared bound (or the typical band)."""
    minimum, maximum, typ = char["min_value"], char["max_value"], char["typ_value"]
    unit = char["unit"]
    if minimum is not None or maximum is not None:
        bounds = []
        if minimum is not None:
            bounds.append(("min", float(minimum)))
        if maximum is not None:
            bounds.append(("max", float(maximum)))
        bound, limit = min(bounds, key=lambda item: abs(value - item[1]))
        distance = abs(value - limit)
        return {
            "kind": "documented_limit",
            "nearest_bound": bound,
            "bound_value": limit,
            "measured": value,
            "margin_abs": distance,
            "margin_pct": None if limit == 0 else 100.0 * distance / abs(limit),
            "slack_abs": LIMIT_SLACK * max(abs(minimum or 0.0), abs(maximum or 0.0), 1e-12),
            "unit": unit,
        }
    if typ is not None:
        typ = float(typ)
        band = TYPICAL_TOLERANCE * abs(typ)
        deviation = abs(value - typ)
        return {
            "kind": "typical_band",
            "typ": typ,
            "band": band,
            "deviation": deviation,
            "measured": value,
            "margin_abs": band - deviation,
            "margin_pct": None if band == 0 else 100.0 * (band - deviation) / band,
            "slack_abs": None,
            "unit": unit,
        }
    return None


def annotate_rows(
    rows: Sequence[Mapping[str, Any]], chars: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """The row table with numeric limits and, for numeric PASS rows, the margin."""
    annotated: list[dict[str, Any]] = []
    for row in rows:
        char = chars.get(str(row["req_id"]))
        entry: dict[str, Any] = {
            "req_id": row["req_id"],
            "required": row["required"],
            "measured": row["measured"],
            "status": row["status"],
            "page": row["page"],
            "probe": None if char is None else char.get("probe"),
            "limits": (
                None
                if char is None
                else {
                    "min": char.get("min_value"),
                    "max": char.get("max_value"),
                    "typ": char.get("typ_value"),
                    "unit": char.get("unit"),
                }
            ),
            "margin": None,
        }
        value = measured_number(str(row["measured"]))
        has_limit = char is not None and (
            char.get("min_value") is not None
            or char.get("max_value") is not None
            or char.get("typ_value") is not None
        )
        if entry["status"] == "PASS" and char is not None and value is not None and has_limit:
            entry["margin"] = row_margin(char, value)
        annotated.append(entry)
    return annotated


def chars_by_id(spec_payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(char["char_id"]): char for char in spec_payload["characteristics"]}


# ---------------------------------------------------------------------------- #
# the four measurements


def measure_build(
    cli: list[str], build_dir: Path, timeout: float, command_timeout: float
) -> dict[str, Any]:
    """Build the fixture model from scratch and report time, rows and stages."""
    out_dir = build_dir / RUN1_NAME
    if out_dir.exists():
        shutil.rmtree(out_dir)
    argv = [
        *cli,
        "model",
        "build",
        "--part",
        PART,
        "--subckt",
        SUBCKT,
        "--datasheet",
        str(DATASHEET),
        "--requirements",
        str(REQUIREMENTS),
        "--bindings",
        str(BINDINGS),
        "--backend",
        "scripted",
        "--no-reinforce",
        "--timeout",
        str(timeout),
        "--out",
        str(out_dir),
    ]
    run_result = run(argv, cwd=REPO_ROOT, timeout=command_timeout)
    results_path = out_dir / "results.json"
    if run_result.timed_out:
        raise MeasureError(f"the build did not finish within {command_timeout:g}s")
    if not results_path.is_file():
        raise MeasureError(
            f"the build wrote no results.json (exit {run_result.exit_code}): "
            f"{run_result.stdout.strip()[-400:] or run_result.stderr.strip()[-400:]}"
        )
    results = read_json(results_path)
    spec = read_json(out_dir / "spec" / "characteristics.json")
    lib = out_dir / f"{SUBCKT}.lib"
    asy = out_dir / f"{SUBCKT}.asy"
    stages = stage_table(run_result)
    # The author loop runs the whole frozen probe set once per judged turn, into
    # ``<out>/build/harness/probes/<probe>/``; the decks on disk are the last
    # turn's, so the count of LTspice invocations is decks x judged turns.
    judged_turns = sum(
        1 for stage in stages if stage["stage"] == "judge" and stage["status"] == "running"
    )
    decks = decks_under(out_dir / "build" / "harness")
    return {
        "out_dir": str(out_dir),
        "run": run_result.payload(),
        "status": results["status"],
        "detail": results["detail"],
        "counts": results["counts"],
        "stages": stages,
        "model": {
            "lib": str(lib),
            "asy": str(asy),
            "lib_sha256": sha256_file(lib) if lib.is_file() else None,
            "asy_sha256": sha256_file(asy) if asy.is_file() else None,
        },
        "judged_turns": judged_turns,
        "decks_in_final_judged_turn": len(decks),
        "ltspice_invocations": len(decks) * judged_turns,
        "rows": [dict(row) for row in results["rows"]],
        "chars": chars_by_id(spec),
    }


def measure_judge(
    cli: list[str], out_dir: Path, timeout: float, command_timeout: float
) -> dict[str, Any]:
    """Re-judge the built model with ``model test`` and time it.

    The judge's own harness directory is emptied first, so the decks found
    afterwards are exactly the ones this invocation wrote — a probe that never
    reached the simulator leaves no deck behind.
    """
    harness_dir = out_dir / "harness"
    if harness_dir.exists():
        shutil.rmtree(harness_dir)
    argv = [*cli, "model", "test", "--out", str(out_dir), "--json", "--timeout", str(timeout)]
    run_result = run(argv, cwd=REPO_ROOT, timeout=command_timeout)
    if run_result.timed_out:
        raise MeasureError(f"the re-judge did not finish within {command_timeout:g}s")
    try:
        payload = json.loads(run_result.stdout)
    except json.JSONDecodeError as exc:
        raise MeasureError(f"model test --json did not print JSON: {exc}") from None
    return {
        "run": run_result.payload(),
        "status": payload["status"],
        "counts": payload["counts"],
        "report_model_sha256": read_json(out_dir / "harness-report.json")["model_sha256"],
        "probes": [
            {"probe_id": probe["probe_id"], "status": probe["status"], "judged": probe["judged"]}
            for probe in payload["probes"]
        ],
        "decks_written": len(decks_under(harness_dir)),
        "ltspice_invocations": len(decks_under(harness_dir)),
    }


def measure_determinism(
    cli: list[str],
    build_dir: Path,
    timeout: float,
    command_timeout: float,
    first: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the same fixture a second time and compare bytes and judgements."""
    out_dir = build_dir / RUN2_NAME
    if out_dir.exists():
        shutil.rmtree(out_dir)
    argv = [
        *cli,
        "model",
        "build",
        "--part",
        PART,
        "--subckt",
        SUBCKT,
        "--datasheet",
        str(DATASHEET),
        "--requirements",
        str(REQUIREMENTS),
        "--bindings",
        str(BINDINGS),
        "--backend",
        "scripted",
        "--no-reinforce",
        "--timeout",
        str(timeout),
        "--out",
        str(out_dir),
    ]
    run_result = run(argv, cwd=REPO_ROOT, timeout=command_timeout)
    results_path = out_dir / "results.json"
    if run_result.timed_out or not results_path.is_file():
        code = run_result.exit_code
        raise MeasureError(f"the second build produced no results.json (exit {code})")
    results = read_json(results_path)
    lib = out_dir / f"{SUBCKT}.lib"
    asy = out_dir / f"{SUBCKT}.asy"
    second_rows = [dict(row) for row in results["rows"]]
    first_rows = [dict(row) for row in first["raw_rows"]]
    differences = [
        {"req_id": before["req_id"], "first": _row_key(before), "second": _row_key(after)}
        for before, after in zip(first_rows, second_rows, strict=True)
        if _row_key(before) != _row_key(after)
    ]
    return {
        "out_dir": str(out_dir),
        "run": run_result.payload(),
        "status": results["status"],
        "counts": results["counts"],
        "seconds": run_result.seconds,
        "files": {
            "lib": {
                "first": first["model"]["lib_sha256"],
                "second": sha256_file(lib) if lib.is_file() else None,
            },
            "asy": {
                "first": first["model"]["asy_sha256"],
                "second": sha256_file(asy) if asy.is_file() else None,
            },
        },
        "lib_identical": bool(
            first["model"]["lib_sha256"]
            and lib.is_file()
            and sha256_file(lib) == first["model"]["lib_sha256"]
        ),
        "asy_identical": bool(
            first["model"]["asy_sha256"]
            and asy.is_file()
            and sha256_file(asy) == first["model"]["asy_sha256"]
        ),
        "row_tables_identical": first_rows == second_rows,
        "row_differences": differences,
    }


def apply_mutation(mutant: Mapping[str, Any], out_dir: Path) -> dict[str, Any]:
    """Perturb the copy's model file; never the shipped fixture."""
    lib = out_dir / f"{SUBCKT}.lib"
    if mutant["kind"] == "wipe":
        note = f"* {SUBCKT}: model body removed by measure_models.py\n"
        lib.write_text(note, encoding="utf-8", newline="\n")
        return {"target": str(lib), "occurrences": 1}
    text = lib.read_text(encoding="utf-8")
    occurrences = text.count(mutant["old"])
    if occurrences != 1:
        raise MeasureError(
            f"mutation {mutant['id']}: {mutant['old']!r} appears {occurrences} times in {lib}; "
            "the bundled template changed and this tool's mutation needs updating"
        )
    lib.write_text(text.replace(mutant["old"], mutant["new"]), encoding="utf-8", newline="\n")
    return {"target": str(lib), "occurrences": occurrences}


def rows_from_report(
    out_dir: Path, baseline_by_id: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """The row table a harness report implies, in the spec's own order.

    Mirrors ``make_model._Run.rows()`` for the probe-bound rows; ``required`` is
    taken verbatim from the baseline table (the limits come from the frozen spec
    and no mutation can change them). :func:`self_check_rows` proves the mirror
    by rebuilding the baseline's own table from its report.
    """
    report = read_json(out_dir / "harness-report.json")
    spec = read_json(out_dir / "spec" / "characteristics.json")
    outcomes = {str(outcome["probe_id"]): outcome for outcome in report["outcomes"]}
    rows: list[dict[str, Any]] = []
    for char in spec["characteristics"]:
        baseline = baseline_by_id[str(char["char_id"])]
        row = {
            "req_id": char["char_id"],
            "required": baseline["required"],
            "measured": baseline["measured"],
            "status": baseline["status"],
            "page": char["source_page"],
        }
        if char["probe"] is not None:
            outcome = outcomes.get(str(char["probe"]))
            if outcome is None:
                row["status"] = "UNKNOWN"
                row["measured"] = "-"
            else:
                row["status"] = str(outcome["status"])
                row["measured"] = str(outcome.get("judged") or "-")
                if outcome["status"] == "UNKNOWN" and outcome.get("unknown_reason"):
                    row["measured"] = f"- ({outcome['unknown_reason']})"
        rows.append(row)
    return rows


def self_check_rows(out_dir: Path, baseline_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Prove the report-to-rows mirror reproduces the harness's own row table."""
    baseline_by_id = {str(row["req_id"]): row for row in baseline_rows}
    rebuilt = rows_from_report(out_dir, baseline_by_id)
    fields = ("req_id", "required", "measured", "status", "page")
    expected = [{key: row[key] for key in fields} for row in baseline_rows]
    return {
        "matched": rebuilt == expected,
        "compared_rows": len(expected),
        "mismatches": [
            {"req_id": left["req_id"], "expected": left, "rebuilt": right}
            for left, right in zip(expected, rebuilt, strict=True)
            if left != right
        ],
    }


def measure_discrimination(
    cli: list[str],
    build_dir: Path,
    timeout: float,
    command_timeout: float,
    first: Mapping[str, Any],
) -> dict[str, Any]:
    """Perturb a copy of the built model per :data:`MUTANTS` and re-judge it."""
    mutants_dir = build_dir / MUTANTS_NAME
    source = Path(str(first["out_dir"]))
    baseline_by_id = {str(row["req_id"]): row for row in first["raw_rows"]}
    results: list[dict[str, Any]] = []
    for mutant in MUTANTS:
        out_dir = mutants_dir / str(mutant["id"])
        if out_dir.exists():
            shutil.rmtree(out_dir)
        shutil.copytree(source, out_dir)
        mutation = apply_mutation(mutant, out_dir)
        judge = measure_judge(cli, out_dir, timeout, command_timeout)
        rows = rows_from_report(out_dir, baseline_by_id)
        flips = [
            {
                "req_id": row["req_id"],
                "before": baseline_by_id[row["req_id"]]["status"],
                "after": row["status"],
                "measured_before": baseline_by_id[row["req_id"]]["measured"],
                "measured_after": row["measured"],
            }
            for row in rows
            if row["status"] != baseline_by_id[row["req_id"]]["status"]
        ]
        observed = sorted(flip["req_id"] for flip in flips)
        expected = sorted(str(x) for x in mutant["expect_flips"])
        results.append(
            {
                "id": mutant["id"],
                "intent": mutant["intent"],
                "change": (
                    f"{mutant['old']!r} -> {mutant['new']!r}"
                    if mutant["kind"] == "constant"
                    else f"model file replaced by a comment ({mutation['target']})"
                ),
                "expect_flips": expected,
                "expect_why": mutant["expect_why"],
                "prediction_held": observed == expected,
                "observed_flips": observed,
                "flips": flips,
                "status_counts": judge["counts"],
                "wall_clock_s": round(float(judge["run"]["wall_clock_s"]), 3),
                "exit_code": judge["run"]["exit_code"],
                "report_model_sha256": judge["report_model_sha256"],
                "ltspice_invocations": judge["ltspice_invocations"],
                "rows": [
                    {
                        "req_id": row["req_id"],
                        "required": row["required"],
                        "measured": row["measured"],
                        "status": row["status"],
                        "page": row["page"],
                    }
                    for row in rows
                ],
            }
        )
        print(
            f"  mutant {mutant['id']:20} {judge['counts']} flips={observed or 'none'}"
            f"{'' if observed == expected else '  <-- PREDICTION MISSED'}",
            flush=True,
        )
    return {
        "note": (
            "each perturbation is applied to a copy of the built model under "
            f"{mutants_dir.name}/; the shipped fixtures are never edited"
        ),
        "mutants": results,
    }


def measure_timing(build_dir: Path, out_dir: Path, timeout: float) -> dict[str, Any]:
    """Per-probe wall clock and LTspice invocation count, from an instrumented pass."""
    from boardmodeler.authoring.harness import HarnessReport, run_harness
    from boardmodeler.simulation import ltspice as ltspice_mod

    spec_path = out_dir / "spec" / "characteristics.json"
    spec = SpecSet.from_json(spec_path.read_text(encoding="utf-8"))
    lib = out_dir / f"{SUBCKT}.lib"
    install = ltspice_mod.locate()
    if install is None:
        raise MeasureError("LTspice was not found; the timing pass cannot run")
    workdir = build_dir / TIMING_NAME
    if workdir.exists():
        shutil.rmtree(workdir)

    real_run_batch = ltspice_mod.run_batch
    invocations: list[dict[str, Any]] = []

    def timed_run_batch(exe: Any, deck: Any, run_dir: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        result = real_run_batch(exe, deck, run_dir, **kwargs)
        seconds = time.perf_counter() - started
        raw = getattr(result, "raw_path", None)
        invocations.append(
            {
                "probe_id": Path(run_dir).name,
                "deck": str(deck),
                "seconds": round(seconds, 3),
                "raw_written": bool(raw is not None and Path(raw).is_file()),
                "timed_out": bool(getattr(result, "timed_out", False)),
                "cancelled": bool(getattr(result, "cancelled", False)),
            }
        )
        return result

    harness_mod.run_batch = timed_run_batch
    started = time.perf_counter()
    try:
        report: HarnessReport = run_harness(
            model_lib=lib,
            subckt=spec.subckt,
            spec=spec,
            workdir=workdir,
            ltspice=install.path,
            timeout_s=timeout,
        )
    finally:
        harness_mod.run_batch = real_run_batch
    seconds = time.perf_counter() - started
    return {
        "note": (
            "the harness records no per-probe timing, so this is one instrumented in-process pass "
            "with boardmodeler.authoring.harness.run_batch wrapped: it is the same harness the CLI "
            "calls, and the pass is cross-checked against the CLI's re-judge below"
        ),
        "workdir": str(workdir),
        "total_probe_seconds": round(seconds, 3),
        "invocations": len(invocations),
        "decks_written": len(decks_under(workdir)),
        "model_sha256": report.model_sha256,
        "counts": report.counts(),
        "per_probe": invocations,
        "probes": [
            {"probe_id": outcome.probe_id, "status": outcome.status, "judged": outcome.judged}
            for outcome in report.outcomes
        ],
    }


# ---------------------------------------------------------------------------- #
# rendering


def _fmt(value: Any, digits: int = 6) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}g}"
    return str(value)


def _margin_text(entry: Mapping[str, Any]) -> str:
    margin = entry.get("margin")
    if not margin:
        return "-"
    unit = margin.get("unit") or ""
    if margin["kind"] == "documented_limit":
        percent = "-" if margin["margin_pct"] is None else f"{margin['margin_pct']:.3g} %"
        return (
            f"{_fmt(margin['margin_abs'])} {unit} to nearest bound "
            f"({margin['nearest_bound']} {_fmt(margin['bound_value'])} {unit}); {percent}; "
            f"limit slack {_fmt(margin['slack_abs'])} {unit}"
        )
    percent = "-" if margin["margin_pct"] is None else f"{margin['margin_pct']:.3g} %"
    return (
        f"{_fmt(margin['margin_abs'])} {unit} inside the 10 % band around typ "
        f"{_fmt(margin['typ'])} {unit} (deviation {_fmt(margin['deviation'])} {unit}); {percent}"
    )


def _counts_line(counts: Mapping[str, Any]) -> str:
    order = ("PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE", "BLOCKED")
    return " · ".join(f"{counts.get(name, 0)} {name}" for name in order)


def render_markdown(payload: Mapping[str, Any]) -> str:
    build = payload["build"]
    judge = payload["judge"]
    environment = payload["environment"]
    model = build["model"]
    lines: list[str] = []
    lines.append("# Model-maker measurements — TPS54320 (BM_REG_BUCK), scripted backend")
    lines.append("")
    lines.append(f"Observed {environment['observed_at']} at Python {environment['python']}")
    lines.append("")
    ltspice = environment["ltspice"]
    lines.append(f"- LTspice: {ltspice['path']} (version {ltspice['version']})")
    digest = (model["lib_sha256"] or "")[:16]
    lines.append(f"- Model: `{model['lib']}` sha256 `{digest}`")
    lines.append("- Every number below comes from a real run; the commands are quoted verbatim.")
    lines.append("")
    lines.append("## 1. Build time and accuracy")
    lines.append("")
    lines.append(f"```\n{build['run']['command']}\n```")
    lines.append("")
    lines.append(
        f"- **Build wall clock: {build['run']['wall_clock_s']} s** "
        f"(exit {build['run']['exit_code']}, status `{build['status']}`), "
        f"{build['ltspice_invocations']} LTspice invocation(s) "
        f"({build['decks_in_final_judged_turn']} probes x {build['judged_turns']} judged turn(s))"
    )
    lines.append(f"- {build['detail']}")
    lines.append("")
    lines.append(f"```\n{judge['run']['command']}\n```")
    lines.append("")
    lines.append(
        f"- **Re-judge wall clock: {judge['run']['wall_clock_s']} s** "
        f"(exit {judge['run']['exit_code']}, status `{judge['status']}`), "
        f"{judge['ltspice_invocations']} LTspice invocation(s)"
    )
    lines.append(
        f"- Row counts after the re-judge: {_counts_line(judge['counts'])} "
        f"({len(build['rows'])} datasheet rows)"
    )
    lines.append(f"- Judge report model sha256 `{(judge['report_model_sha256'] or '')[:16]}`")
    self_check = payload["spec"]["row_self_check"]
    compared = self_check["compared_rows"]
    verdict = "every row matches the build table" if self_check["matched"] else "MISMATCH"
    lines.append(
        f"- Row-table self-check: this tool rebuilt all {compared} rows "
        f"from the build's own `harness-report.json` — {verdict}"
    )
    lines.append("")
    if build["stages"]:
        lines.append("| Stage | Status | Started (s) | Seconds |")
        lines.append("| --- | --- | --- | --- |")
        for stage in build["stages"]:
            stage_id = stage["stage"]
            lines.append(
                f"| {stage_id} | {stage['status']} | {stage['started_s']} | {stage['seconds']} |"
            )
        lines.append("")
    lines.append("### Every datasheet row")
    lines.append("")
    lines.append("| Requirement | Required | Measured | Status | Page |")
    lines.append("| --- | --- | --- | --- | --- |")
    for row in build["rows"]:
        required = str(row["required"]).replace("|", "\\|")
        measured = str(row["measured"]).replace("|", "\\|")
        lines.append(
            f"| `{row['req_id']}` | {required} | {measured} | {row['status']} | "
            f"{row['page'] if row['page'] is not None else '-'} |"
        )
    lines.append("")
    pass_rows = [row for row in build["rows"] if row["status"] == "PASS"]
    lines.append(f"### PASS rows with their margin ({len(pass_rows)} rows)")
    lines.append("")
    lines.append("| Requirement | Required limit | Measured | Margin |")
    lines.append("| --- | --- | --- | --- |")
    for row in pass_rows:
        required = str(row["required"]).replace("|", "\\|")
        measured = str(row["measured"]).replace("|", "\\|")
        lines.append(f"| `{row['req_id']}` | {required} | {measured} | {_margin_text(row)} |")
    lines.append("")
    lines.append("## 2. Determinism (two builds, byte and value)")
    lines.append("")
    determinism = payload["determinism"]
    if determinism is None:
        lines.append("Not measured in this run (skipped by flag).")
        lines.append("")
    else:
        seconds = determinism["run"]["wall_clock_s"]
        lines.append(f"- Second build: {seconds} s, status `{determinism['status']}`")
        files = determinism["files"]
        lib_first = (files["lib"]["first"] or "")[:16]
        lib_second = (files["lib"]["second"] or "")[:16]
        asy_first = (files["asy"]["first"] or "")[:16]
        asy_second = (files["asy"]["second"] or "")[:16]
        lib_same = "identical" if determinism["lib_identical"] else "DIFFERENT"
        asy_same = "identical" if determinism["asy_identical"] else "DIFFERENT"
        lines.append(
            f"- `{SUBCKT}.lib` sha256: run 1 `{lib_first}`, run 2 `{lib_second}` — **{lib_same}**"
        )
        lines.append(
            f"- `{SUBCKT}.asy` sha256: run 1 `{asy_first}`, run 2 `{asy_second}` — **{asy_same}**"
        )
        rows_same = "identical" if determinism["row_tables_identical"] else "DIFFERENT"
        lines.append(
            f"- Judged row tables: **{rows_same}** (all {len(build['rows'])} rows compared on "
            "req_id, limits, measured value, status and page; this covers the measured numbers, "
            "so it is a statement about the simulator runs too)"
        )
        if determinism["row_differences"]:
            lines.append("")
            lines.append("| Requirement | Run 1 | Run 2 |")
            lines.append("| --- | --- | --- |")
            for difference in determinism["row_differences"]:
                lines.append(
                    f"| `{difference['req_id']}` | {difference['first']} | {difference['second']} |"
                )
        lines.append("")
    lines.append("## 3. Discrimination (deliberate defects, re-judged)")
    lines.append("")
    discrimination = payload["discrimination"]
    if discrimination is None:
        lines.append("Not measured in this run (skipped by flag).")
        lines.append("")
    else:
        lines.append(discrimination["note"])
        lines.append("")
        for mutant in discrimination["mutants"]:
            verdict = "prediction held" if mutant["prediction_held"] else "**PREDICTION MISSED**"
            lines.append(f"### `{mutant['id']}` — {mutant['intent']}")
            lines.append("")
            lines.append(f"- Change: `{mutant['change']}`")
            expected_flips = ", ".join(mutant["expect_flips"]) or "no row flips"
            lines.append(f"- Expected: {expected_flips}")
            lines.append(f"- Why: {mutant['expect_why']}")
            counts = _counts_line(mutant["status_counts"])
            lines.append(
                f"- Observed: {counts}, {mutant['wall_clock_s']} s, "
                f"{mutant['ltspice_invocations']} LTspice invocation(s) — {verdict}"
            )
            if mutant["flips"]:
                lines.append("")
                lines.append("| Requirement | Before | Measured before | After | Measured after |")
                lines.append("| --- | --- | --- | --- | --- |")
                for flip in mutant["flips"]:
                    lines.append(
                        f"| `{flip['req_id']}` | {flip['before']} | {flip['measured_before']} | "
                        f"{flip['after']} | {flip['measured_after']} |"
                    )
            lines.append("")
    lines.append("## 4. Timing breakdown")
    lines.append("")
    timing = payload["timing"]
    if timing is None:
        lines.append("Not measured in this run (skipped by flag).")
        lines.append("")
    else:
        lines.append(timing["note"])
        lines.append("")
        invocations = timing["invocations"]
        decks = timing["decks_written"]
        lines.append(
            f"- Total probe time: **{timing['total_probe_seconds']} s** across "
            f"**{invocations} LTspice invocation(s)** ({decks} decks written)"
        )
        matches = timing.get("matches_cli_judge")
        sha_matches = timing.get("model_sha256_matches_build")
        lines.append(
            f"- The instrumented pass reproduced the CLI re-judge exactly: "
            f"**{matches}** (model sha256 matches the build: {sha_matches})"
        )
        lines.append("")
        lines.append("| Probe | LTspice seconds | .raw written | Timed out |")
        lines.append("| --- | --- | --- | --- |")
        for probe in timing["per_probe"]:
            probe_id = probe["probe_id"]
            lines.append(
                f"| {probe_id} | {probe['seconds']} | {probe['raw_written']} | "
                f"{probe['timed_out']} |"
            )
        lines.append("")
    lines.append("## What was not measured")
    lines.append("")
    for item in payload["not_measured"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------- #
# entry point


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build-dir", type=Path, default=REPO_ROOT / "build")
    parser.add_argument("--cli", default=None, help="the boardmodeler executable to drive")
    parser.add_argument("--timeout", type=float, default=120.0, help="seconds per simulation")
    parser.add_argument(
        "--command-timeout", type=float, default=1800.0, help="seconds per CLI call"
    )
    parser.add_argument("--skip-determinism", action="store_true")
    parser.add_argument("--skip-discrimination", action="store_true")
    parser.add_argument("--skip-timing", action="store_true")
    parser.add_argument("--json", action="store_true", help="print the JSON instead of the summary")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    build_dir: Path = args.build_dir
    build_dir.mkdir(parents=True, exist_ok=True)
    cli = cli_prefix(args.cli)

    from boardmodeler.simulation.ltspice import locate, version

    install = locate()
    environment = {
        "observed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cli": cli,
        "ltspice": {
            "path": None if install is None else str(install.path),
            "version": None if install is None else version(install.path),
        },
        "fixture": {
            "part": PART,
            "subckt": SUBCKT,
            "datasheet": str(DATASHEET),
            "requirements": str(REQUIREMENTS),
            "bindings": str(BINDINGS),
            "backend": "scripted",
        },
        "harness_slack_relative": LIMIT_SLACK,
        "harness_typical_tolerance": TYPICAL_TOLERANCE,
    }
    if install is None:
        raise MeasureError("LTspice was not found; nothing here can be measured without it")

    report: dict[str, Any] = {"tool": "tools/measure_models.py", "environment": environment}

    print("building the fixture model (run 1)…", flush=True)
    build = measure_build(cli, build_dir, args.timeout, args.command_timeout)
    build["raw_rows"] = [dict(row) for row in build["rows"]]
    build["rows"] = annotate_rows(build["rows"], build["chars"])
    build.pop("chars")
    spec_payload = read_json(Path(build["out_dir"]) / "spec" / "characteristics.json")
    spec_digest = SpecSet.from_json(json.dumps(spec_payload)).digest()
    print(
        f"  build {build['run']['wall_clock_s']}s status={build['status']} "
        f"{_counts_line(build['counts'])}",
        flush=True,
    )

    print("re-judging with 'model test'…", flush=True)
    judge = measure_judge(cli, Path(build["out_dir"]), args.timeout, args.command_timeout)
    print(f"  judge {judge['run']['wall_clock_s']}s {_counts_line(judge['counts'])}", flush=True)

    self_check = self_check_rows(Path(build["out_dir"]), build["rows"])
    if not self_check["matched"]:
        raise MeasureError(
            "this tool's report-to-rows mirror does not reproduce the harness's own row table "
            f"({len(self_check['mismatches'])} mismatching row(s)); the discrimination section "
            "would be guessing, so it was not run"
        )

    determinism = None
    if not args.skip_determinism:
        print("building the fixture model again (run 2)…", flush=True)
        determinism = measure_determinism(cli, build_dir, args.timeout, args.command_timeout, build)
        lib_same = determinism["lib_identical"]
        asy_same = determinism["asy_identical"]
        rows_same = determinism["row_tables_identical"]
        print(
            f"  lib identical={lib_same} asy identical={asy_same} rows identical={rows_same}",
            flush=True,
        )

    discrimination = None
    if not args.skip_discrimination:
        print("perturbing copies of the built model…", flush=True)
        discrimination = measure_discrimination(
            cli, build_dir, args.timeout, args.command_timeout, build
        )

    timing = None
    if not args.skip_timing:
        print("timing every probe (instrumented pass)…", flush=True)
        timing = measure_timing(build_dir, Path(build["out_dir"]), args.timeout)
        observed = [
            {"probe_id": probe["probe_id"], "status": probe["status"], "judged": probe["judged"]}
            for probe in timing["probes"]
        ]
        expected = [
            {"probe_id": probe["probe_id"], "status": probe["status"], "judged": probe["judged"]}
            for probe in judge["probes"]
        ]
        timing["matches_cli_judge"] = observed == expected
        timing["cli_judge_mismatches"] = [
            {"instrumented": left, "cli": right}
            for left, right in zip(observed, expected, strict=True)
            if left != right
        ]
        built_sha = build["model"]["lib_sha256"]
        timing["model_sha256_matches_build"] = timing["model_sha256"] == built_sha
        print(
            f"  {timing['invocations']} invocation(s), {timing['total_probe_seconds']}s total, "
            f"matches CLI judge={timing['matches_cli_judge']}",
            flush=True,
        )
        if not timing["matches_cli_judge"]:
            raise MeasureError(
                "the instrumented pass disagrees with the CLI's own re-judge; the per-probe "
                "numbers were not reported as if they belonged to that judge"
            )

    not_measured: list[str] = []
    if determinism is None:
        not_measured.append("determinism: skipped by --skip-determinism")
    if discrimination is None:
        not_measured.append("discrimination: skipped by --skip-discrimination")
    if timing is None:
        not_measured.append("per-probe timing: skipped by --skip-timing")
    not_measured.append(
        "authoring-agent time for a real provider (--backend api/bob): this run uses the "
        "bundled offline template, which authors in one turn; no inference request was made"
    )
    not_measured.append(
        "harness-internal per-probe timing from the CLI passes: the harness records none, so the "
        "CLI build/judge times are wall clock only and per-probe times come from the "
        "instrumented in-process pass"
    )
    not_measured.append(
        "anything about a part other than TPS54320, and any row declared NOT_APPLICABLE: no probe "
        "reaches those rows, so their margins cannot be measured"
    )

    report.update(
        {
            "spec": {"digest": spec_digest, "chars": spec_payload, "row_self_check": self_check},
            "build": build,
            "judge": judge,
            "determinism": determinism,
            "discrimination": discrimination,
            "timing": timing,
            "not_measured": not_measured,
        }
    )

    json_path = build_dir / JSON_NAME
    md_path = build_dir / MD_NAME
    markdown = render_markdown(report)
    payload_json = json.dumps(report, indent=2, sort_keys=True) + "\n"
    json_path.write_text(payload_json, encoding="utf-8", newline="\n")
    md_path.write_text(markdown, encoding="utf-8", newline="\n")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(markdown)
    print(f"\nwrote {json_path} and {md_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
