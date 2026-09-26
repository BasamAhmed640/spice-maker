# M0: system-level model direction and catalog

Date: 2026-09-25 (America/New_York). Milestone M0 is documentation only.
This report records the decision, scope and verification of the documents;
it is **not** electrical-model evidence and contains no new LTspice PASS.

## Baseline read before editing

- General `main`: `c722e50` (`Fix the buck template's gate ground and premature
  current-limit bench; make the slice reusable`).
- Bob `main`: `cca682b` (same change adapted to the Bob edition).
- Neither repo had `docs/SYSTEM_MODELS_PLAN.md` when this run began.
- The prior buck slice remains 12 PASS / 4 FAIL / 3 UNKNOWN on the frozen
  TPS54332DDA 19-row spec. Those measurements belong to
  [`2026-09-25-buck-slice/REPORT.md`](../2026-09-25-buck-slice/REPORT.md);
  they are historical and were not rerun or promoted by M0.

## Documents in scope

1. `docs/DECISIONS.md`: D-048 states the system-level objective and
   acceptance boundaries.
2. `docs/TEMPLATE_CATALOG.md`: 22 families and ten-step build order, with
   current implementation status called out separately.
3. `docs/SYSTEM_MODELS_PLAN.md`: M0–M9+ status, evidence paths and commit IDs.
4. `docs/STATUS.md`: exact M0 commands and results in each edition.

The old AI authoring path is retained; M0 makes no behavioral, simulator,
packaging or UI change. The vendor TPS54332 library was not copied into either
repository. No API key value was read, no inference request was made, and no
LTspice process was launched for M0.

## Documentation acceptance

The owner prompt is `C:\Users\basam\src\AGENT-PROMPT -- SPICE MAKER SYSTEM MODELS.md`.
Its catalog and milestones are normative for this M0 doc update. The catalog
describes **future** coverage: only the existing `peak_current_buck_v1` slice
has reusable implemented behavior today. The generic pin model, AVG mode,
pinout confirmation UI, and default code-built system path are future
milestones, not M0 deliverables.

## Checks and observed results

| Check | Observed result |
| --- | --- |
| General `.venv\Scripts\python.exe -m pytest -q tests\test_shared_core.py` | 2 passed in 1.07 s |
| Bob `.venv\Scripts\python.exe -m pytest -q tests\test_shared_core.py` | 2 passed in 1.12 s |
| General `.venv\Scripts\python.exe tools\shared_core.py --compare ..\spice-maker-bob` | `identical: 42 files` |
| General catalog number/order check | Family rows 1–22 and build-order steps 1–10, each once and in sequence |
| Cross-edition documentation comparison | D-048 text equal; catalog and plan SHA-256 hashes equal before edition-specific status entries |
| `git diff --check` in both repositories | No whitespace errors after removing one extra Bob EOF blank line |

The first draft of the catalog check counted numbered examples outside the build-order
section and reported 13. The check was corrected to count only the `## Build order`
section; it then observed exactly steps 1–10. No catalog text changed to make the
check pass. No model was generated or simulated during M0, so M0 has no electrical
measurement, band, waveform hash, or LTspice time to report.
