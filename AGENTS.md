# AGENTS.md — repository rules

## Commands

```powershell
uv sync --all-extras                       # single setup command
uv run pytest -q                           # unit + integration (LTspice-marked tests run locally)
uv run pytest -q -m "not ltspice"          # simulator-free subset
uv run ruff check . ; uv run ruff format --check .
uv run boardmodeler doctor --json
```

## Layout rules

* `src/boardmodeler/` — package. `domain/records.py` is the shared data contract; changing it late
  invalidates fixtures and manifests, so change it deliberately and update `SCHEMA_VERSION`.
* `fixtures/` — committed test fixtures only. Anything downloaded from a vendor lives under
  `fixtures/**/originals/` and is **git-ignored** (local use only).
* `projects/`, `build/`, `runs/` — generated. Never committed.

## Hard rules

1. **Never write into the LTspice installation or library directory.** Decks are written under the
   project's `runs/`; models are referenced by absolute path at run time or copied beside the deck on
   export. `C:\Users\<user>\AppData\Local\LTspice\lib` is read-only for us.
2. **Never record an unobserved result.** A `TestResult` with `status=PASS` requires an observed
   simulator artifact (`.raw`/`.log`) with its hash recorded in the manifest. If the simulator is
   missing, the run is `BLOCKED`; missing measurements are `UNKNOWN`.
3. **Never present synthetic data as device data.** Everything under `fixtures/switch_fixture/` and
   `fixtures/demo_board/` carries `origin=TEST_FIXTURE` and `evidence.extraction=synthetic_fixture`.
   The synthetic PCIe-switch fixture has no real part number.
4. **Never report a citation as verified** unless its excerpt appears (whitespace-normalized) in the
   extracted text of the cited page. Absolute-maximum-ratings text is never an operating limit.
5. **Never silently fall back.** Provider selection is explicit: a configured provider is used or the
   stage is `BLOCKED` with the reason. Bob is never replaced by an external provider automatically.
6. **No telemetry.** Extraction and model artifacts are cached by hash; repeat runs make zero
   inference requests.
7. **Secrets never persist.** Credentials live in the OS keyring (`boardmodeler` service) or
   `BOARDMODELER_<NAME>_API_KEY` for CI; they are never logged, written into project files, or exported.
8. **Repair is bounded and cannot weaken honesty.** Repair may only edit `models/candidates/<n>/`,
   is capped by `max_repair_iterations`, and may never relax a tolerance, delete a test, alter source
   evidence, narrow coverage, or edit the circuit.
9. **No temperature or statistical claims without modeled temperature dependence / explicit data.**

## Conventions

* Python 3.14, `from __future__ import annotations`, ruff line length 100.
* All record models: `model_config = ConfigDict(extra="forbid")`, JSON round-trip via
  `model_dump_json(indent=2)`.
* Tests: `pytest`, markers `ltspice`, `network`, `gui`. A test earns its place only where a plausible
  bug would fail it.
* `docs/STATUS.md` is updated at every phase boundary with the exact commands run and their observed
  results. `docs/DECISIONS.md` records every decision that constrains later work.
