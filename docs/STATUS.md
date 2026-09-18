# STATUS

Updated at every phase boundary. "Observed" means the exact command was run and
the result below is its actual output.

## Current milestone

**Phase 2 — first regulator** (in progress: capability probing running; type-B
template work in flight; export/model-card done).

## Completed

### Phase 2 — first regulator (partial)

* **Vendor model (type A) obtained and running.** `tools/fetch_fixtures.py
  --allow-network` fetched the TPS54320 datasheet (HTTP 200, 1 678 941 bytes,
  sha256 `480b1cdb92b4668e…`, SLVS982C) and TI's **unencrypted** PSpice transient
  model package `SLVM451A` (HTTP 200, 79 758 bytes, sha256 `ae5d9bc8128ea8c4…`).
  `models/adapt.py` ports it mechanically (16 recorded changes, **D-008**) and the
  ported model simulates in LTspice 26.0.0.3: start-up settles at **3.1324 V**
  against the 3.2691 V divider target, `V(ph)` swings 0→12.5 V, and the run
  completes in 0.8 s (versus never advancing before the switch-hysteresis fix).
  Committed artifacts: `provenance.json`, `adaptation.json`; the vendor bytes stay
  under the git-ignored `originals/` and are never redistributed (D-003).
* **Datasheet → requirements + pinmap.** `tools/extract_tps54320_fixture.py`
  slices every excerpt out of the cited page by anchor, so a citation cannot be
  remembered or paraphrased: **38 requirements, 15 pins, citation coverage
  1.000**, 0 validation errors, 1 deliberate warning (the datasheet prints the
  hiccup cycle counts in the TYP column while the requirement is classed
  `TYPICAL_VALUE`, which becomes a non-blocking review item).
  `tests/regulator/test_tps54320_fixture.py` re-validates all of it
  deterministically, including an invented-citation rejection and the
  vendor-model port-order ↔ pinmap bijection.
* **Capability probing (D-009).** `models/capability.py` runs one probe per
  behaviour key through real LTspice runs and can only report `supported` when a
  probe met its numeric criterion; a raising or cancelled probe stays
  `not_tested`, and `behavior_gate` blocks every non-`supported` state.
* **Export + model card.** `reporting/export.py` writes the §14 file set with
  relative paths, hashes every file into `manifest.json`, records the vendor
  model's hash while refusing to copy vendor bytes, and lists every requirement
  with no dynamic test in `coverage.json` (critical ones also as findings).
* **Symbol generation.** `models/symbolism.py` emits `.asy` files whose
  `SpiceOrder` values are a checked bijection onto the subcircuit's port order.

### Phase 0 — environment, contracts, simulator smoke test

|Step|Result|
|---|---|
|Toolchain|`uv venv --python 3.14` + `uv sync --all-extras` → numpy 2.5.2, pydantic 2.13.5, pypdf 6.19.0, pypdfium2 5.13.0, keyring 25.7.x, PySide6-Essentials 6.11.2, spicelib 1.6.3, pytest 9.1.1, ruff 0.16.8, reportlab 5.0.1, psutil 7.2.2, pyinstaller 6.22.3|
|Domain records|`domain/{enums,records,expressions,hashing,ids}.py`; 63 round-trip/strictness tests green|
|Simulator invocation|Resolved empirically and recorded as **D-006**: `LTspice.exe -b [-ascii] <abs deck>` with `cwd=<run dir>`; `-I` is unusable (GUI modal hang); `.step` unusable (concatenated `.raw`)|
|`.raw` reader|Native reader for UTF-16/ASCII headers and 5 payload layouts, **D-007**; layout chosen by exact size match, never guessed|
|Backend selection|**D-002** — native is authoritative; `spicelib` only when it agrees within 1e-9 on the smoke `.raw`|
|CLI|`boardmodeler version`, `boardmodeler doctor [--json] [--no-smoke]`|
|Security/providers|`security/{credentials,paths,subprocess_guard,policy}.py`, `providers/{base,fixture,registry}.py`; 54 tests green|
|Documents|`documents/{pdf,store,ocr,pages}.py`; 41 tests green; OCR reports `tesseract_not_found` (no silent fallback)|

Observed smoke test (real LTspice 26.0.0.3):

```
$ uv run boardmodeler doctor --json
ltspice.version      = "26.0.0"
ltspice.smoke_test   = "pass"
ltspice.measured_v   = 0.632... (analytic 0.632 V, tolerance +/-2%)
ltspice.exit_code    = 0
reader_backend       = native (spicelib 1.6.3 cross-check reported in DECISIONS D-002)
ocr                  = unavailable: tesseract_not_found
```

### Phase 1 core — deterministic verification

|Component|State|
|---|---|
|`simulation/deck.py`|Deck/`Source`/`TranSpec`/`MeasSpec` builders + `{{placeholder}}` template rendering (unknown placeholders raise)|
|`simulation/measures.py`|`Observation`, `RunDiagnostics`, truncation detection, convergence classification|
|`simulation/limits.py`|Window coverage/resolution checks, finiteness, `RunUsability` (BLOCKED vs UNKNOWN)|
|`verification/assertions.py`|Every D5 op implemented with `Verdict` + vacuous-pass guards|
|`verification/scenarios.py`|All 23 required scenario ids with intent and coverage notes|
|`verification/corners.py`|Timestep refinement + enumerated-axis sweeps + temperature guard|
|`verification/engine.py`|Requirement evaluation, honesty gates, fault-detection semantics|
|`pipeline/runner.py`|Per-case `runs/<run_id>/` execution with cancellation|
|`pipeline/project.py`|Project layout, frozen-baseline preference|
|CLI|`boardmodeler run tests --project <dir> [--scope] [--test] [--json] [--out] [--list-tests] [--strict]`|

Evidence that good and bad are distinguished by real runs
(`tests/ltspice/test_engine_e2e.py`, `tests/test_cli_run_tests.py`):

* a divider that yields 3.3 V → `PASS` with `min(V(out))=3.3` in `measured`;
* the same case with `R1=30k` → `FAIL` naming the observed 3.0 V and the violated
  lower limit 3.234 V;
* every status in `{PASS, FAIL, UNKNOWN, BLOCKED, NOT_APPLICABLE}` is produced by at
  least one test (fault semantics, convergence BLOCK, missing-simulator BLOCK,
  unexercised precondition NOT_APPLICABLE, missing signal UNKNOWN).

## Blockers

* `analog.com` (LT8609S datasheet) timed out during the Phase 2 fixture probe. The
  TPS54320 datasheet downloads normally, so Phase 2 starts on its primary path;
  the LT8609S fallback would need an alternative source for its datasheet.
* OCR is unavailable by design on this machine (`tesseract` absent): pages needing
  OCR produce explicit evidence gaps.

## Next actions

1. `tools/fetch_fixtures.py` — fetch the TPS54320 datasheet (public, local use only)
   and attempt the manufacturer model package; record provenance, hashes, licence note.
2. Requirement + pinmap extraction with citation verification.
3. `requirements/{model,review}.py`, capability probes, `models/regulator.py` type-B
   templates, export/model card.

## Commands run (with observed results)

```powershell
uv run pytest -q                     # 259 passed (before the Phase 2 tests land)
uv run ruff check src tests          # All checks passed
uv run boardmodeler version          # boardmodeler 0.1.0
uv run boardmodeler doctor --json    # smoke_test "pass", measured 0.632 V
uv run boardmodeler run tests --project <prj> --json --strict   # summary {"PASS": 1}, exit 0
```
