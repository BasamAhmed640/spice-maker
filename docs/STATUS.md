# STATUS

Updated at every phase boundary. **"Observed" means the exact command was run and
the result below is its actual output** — never a remembered or expected value.

## Current milestone

**Phase 6 — broader device coverage** (template families with committed regression
baselines and the explicit blocked path for real-device qualification). The core
phases (0–5) are complete; the integrated board demonstration is running end to end.

## What the product does (one paragraph)

Give it a datasheet and an optional part identity, and it produces a grounded
LTspice model — symbol, example schematic, executable tests, evidence, limits —
or, given a circuit, it checks the electrical/timing behaviour including schematic
connection mistakes. Every status is one of `PASS/FAIL/UNKNOWN/BLOCKED/NOT_APPLICABLE`;
no PASS is ever recorded without an observed simulator artifact.

## Completed phases

### Phase 0 — environment, contracts, simulator smoke test

|Step|Observed result|
|---|---|
|Toolchain|`uv venv --python 3.14` + `uv sync --all-extras` → numpy 2.5.2, pydantic 2.13.5, pypdf 6.19.0, pypdfium2 5.13.0, keyring 25.7.x, PySide6-Essentials 6.11.2, spicelib 1.6.3, pytest 9.1.1, ruff 0.16.8, reportlab 5.0.1, psutil 7.2.2, pyinstaller 6.22.3|
|Simulator|LTspice 26.0.0.3, smoke test passes with the analytic RC value 0.632 V (tolerance ±2 %)|
|Records|`domain/{enums,records,expressions,hashing,ids}.py`; 63 round-trip/strictness tests green|
|Invocation|**D-006** — `LTspice.exe -b [-ascii] <abs deck>`, `cwd=<run dir>`; `-I` unusable (GUI modal hang), `.step` unusable (concatenated `.raw`)|
|`.raw` reader|Native reader, UTF-16/ASCII headers, 5 payload layouts (**D-007**); layout by exact size match, never guessed|
|Backend|**D-002** — native authoritative; `spicelib` only when it agrees within 1e-9 on the smoke `.raw`|

### Phase 1 — deterministic verification core

`deck.py`/`measures.py`/`limits.py` (deck builders, convergence + truncation
classification, window coverage and resolution), `assertions.py` (every D5 op with
vacuous-pass guards: a missing measurement or an uncovered window is UNKNOWN, never
PASS), `scenarios.py` (all 23 required scenario ids), `corners.py` (timestep
refinement + enumerated sweeps + the temperature guard), `engine.py` (requirement
evaluation, honesty gates, fault-detection semantics), `primitives.py` with analytic
reference tests (Schmitt trip/release, delay `TPD`, open-drain impedance, push-pull
drive, supply-dependent thresholds, conduction, load steps).

Good/bad distinction proven on real LTspice output (`tests/ltspice/test_engine_e2e.py`,
`tests/test_cli_run_tests.py`): R1=10k → `PASS` with `min(V(out))=3.3`; R1=30k →
`FAIL` naming the observed 3.0 V against the 3.234 V lower limit. Every status in
`{PASS,FAIL,UNKNOWN,BLOCKED,NOT_APPLICABLE}` is produced by at least one test.

### Phase 2 — first regulator (TPS54320)

* **Vendor model (type A).** `tools/fetch_fixtures.py --allow-network` fetched the
  datasheet (1 678 941 bytes, sha256 `480b1cdb92b4668e…`, SLVS982C) and TI's
  unencrypted PSpice transient package `SLVM451A` (79 758 bytes, sha256
  `ae5d9bc8128ea8c4…`). `models/adapt.py` ports it mechanically (16 recorded
  changes, **D-008**); the port simulates and settles at 3.1324 V against the
  3.2691 V divider target. Vendor bytes stay git-ignored and are never redistributed.
* **Capability probing (D-009).** One probe deck per behaviour key through real
  LTspice runs; `supported` requires the probe to meet its numeric criterion, and
  `gate_from_capability` blocks every non-`supported` state for dependent
  requirements. Observed record: `shutdown`, `dc_regulation`, `input_current`,
  `reverse_current_prebias` = supported; `startup`, `load_transients`,
  `current_limit_recovery`, `compensation_loop`, `switching_waveforms` = unknown or
  not_tested; `thermal_dependence` = unsupported.
* **Which model carries which claim (D-010).** The generated type-B template is what
  the dynamic tests and the demo exercise; the vendor model is the type-A evidence
  artifact with an honest capability record, because a 2.1 ms application run on it
  hit the 600 s cap.
* **Requirements and pinmap.** `tools/extract_tps54320_fixture.py` slices every
  excerpt out of the cited page by anchor: 38 requirements, 15 pins, citation
  coverage 1.000, 0 validation errors. An invented citation is rejected as UNKNOWN.
* **Export.** `reporting/export.py` writes the model/symbol/tests/requirements/
  coverage/manifest/model-card/report set with relative paths, hashes every file,
  records the vendor model's hash without copying vendor bytes, and lists every
  requirement with no dynamic test in `coverage.json`.

### Phase 3 — integrated board-level demonstration

Built from `fixtures/demo_board` plus the synthetic `fixtures/switch_fixture`:
12 V source with a 2 ms ramp and 0.1 Ω source impedance, `U1` buck → 3V3, `U2` LDO →
1V8, reset circuit driving `PERST#`, strap pull-ups, sideband, per-rail loads, and
the unmodelled PCIe switch `U5`.

`uv run boardmodeler demo build --out build/demo` → **30 requirements, 10 test
cases, 10 static findings, 23/23 scenario stimuli applied**.

|Check|Observed result|
|---|---|
|SC001 syntax|PASS — 28 devices, 5 subcircuits|
|SC002 units/names|PASS — 28 refdes, 79 nodes, 19 values|
|SC003 missing dependency|PASS — 7 subcircuit instances resolved|
|SC004 part identity|PASS — all 28 components carry manufacturer + part number|
|SC005 pinmap/symbol/subckt|PASS — 6 parts, bijection over 26 mapped pins|
|SC006 symbol prefix/model|PASS — 6 symbols declare `Prefix X` and an existing `SpiceModel`|
|SC007 duplicate/dropped|PASS — 79 connections, each pin on at most one net|
|SC008 export portability|PASS — every include/model target inside the project root|
|SC009 supply domain|PASS — 78 power-capable pins evaluated individually|
|SC010 abstraction boundary|UNKNOWN for `U1` — the reduced behavioural buck has no switching node, so its `SW` boundary does not preserve connectivity. **Deliberate**|

Static checks found real fixture defects, which were fixed rather than suppressed:
a source pin's declared domain, an abstraction entry naming a net instead of a
refdes, a fixture that declared `PRECONDITIONS_SATISFIED` as device pin 21 while its
own contract says it is a diagnostic signal and *not* a pin, two nets missing from
`supply_domains`, and a reset supervisor wired with the polarity of a power-good
*pin* emulator (see D-012).

Dynamic check, 10 scenarios against real LTspice — `check` completes in ~11 s:

|Status|Count|Where|
|---|---|---|
|PASS|5|the nominal board, the slow-rail sequencing, the fast-rail boundary, the reset-early-release fault (violation detected as expected), and the invalid-strap fault (violation detected as expected)|
|FAIL|4|`staggered_rails` and `load_step` — the 3V3 rail dips to 3.126 V / 3.130 V against its declared 3.135 V floor when a load steps; `brownout_short_interrupt` — the rail collapses during the dip; `pullup_wrong_domain` — reported below|
|UNKNOWN|1|`pullup_missing`: with its pull-up removed the sideband node is isolated, LTspice drops it from the `.raw`, and the level requirement cannot be evaluated — reported with its reason instead of a guess|

Those FAILs are **findings, not test bugs**: the fixture declares a ±5 % window and
the reduced behavioural models exceed it on a load step. They are reported as they
are; nothing was widened to turn them green.

Fault matrix (`boardmodeler run mutations`): **7 of 7 injected faults detected**
(`swap_straps`, `en_invert`, `missing_pullup`, `pullup_wrong_domain`,
`early_reset_release`, `missing_pg`, `slow_rail_u2`) with the **original project
byte-identical afterwards** (hashes compared before and after). Getting there
required four real fixes:

* the deck is now rebuilt from the circuit on disk for each case, so a mutated
  circuit is actually simulated rather than checked against the unmutated deck;
* the fixture's `timing`/`loads` are the single source for the VIN ramp, the load
  steps and the reset delay, so a mutation of them reaches the simulator;
* two requirements were **missing**: nothing asserted the strap pin→net mapping or
  the reset pull-up's domain, so a strap swap and a re-referenced reset pull-up were
  invisible to every check. Added as `STRAP_011` and `RESET_012`;
* the mutators' declared detecting check is now the one that actually fires
  (`strap_connection`, `sideband_level`, `reset_pullup_domain`).

### Phase 4 — datasheet-to-model automation

`documents/chunk.py` (page-level, contiguous, line-boundary chunks),
`requirements/extract.py` (four provider tasks, strict D4 validation, cache keyed
by prompt hash so replays make zero requests), `pipeline/controller.py` (the frozen
10-stage chain, baseline freeze before repair, bounded repair capped at
`max_repair_iterations`, `RepairViolation` on any tolerance relaxation, test
deletion, evidence edit or circuit edit), `providers/http_inference.py` (OpenAI-
compatible chat-completions with injectable transport, redaction on every error
path, bounded retries, no guessed endpoint) and `providers/bob.py` (BLOCKED with
`bob_credentials_unavailable` — no `BOB_*` credentials exist on this machine — and
never silently substituted; Bob Shell additionally requires both the policy flag and
`--allow-bob-shell`).

### Phase 5 — desktop application and packaging

`pipeline/worker.py` + `ui/worker_client.py` (child-process job protocol, cancel by
process-tree termination), `ui/{app,main_window,results_panel,review_panel,waveforms,
settings,installer}.py`, offscreen GUI tests, `boardmodeler setup` (retro installer
wizard) and `boardmodeler ui`, plus `--self-test --json` and the PyInstaller spec.

### Phase 6 — coverage

Template-driven families (`models/templates.py` + `models/regression.py`) with
committed baselines compared under declared tolerances, and the explicit
`BLOCKED("device_documentation_unavailable")` path for qualifying a real PCIe
switch from a synthetic fixture.

## Blockers and honest gaps

* **Real PCIe-switch qualification is BLOCKED** — no public documentation exists for
  the class of part the synthetic fixture stands in for; the fixture is labelled
  `origin=TEST_FIXTURE` everywhere so no report can present it as device data.
* **OCR is unavailable** (`tesseract` absent): pages needing OCR produce explicit
  evidence gaps, never a silent substitution of embedded text.
* **`analog.com` timed out** during the LT8609S probe; the TPS54320 path was the
  primary one, so Phase 2 was unaffected.
* **The vendor model is not fast enough for board scenarios** (D-010). Recorded as a
  measured property, with the capability record gating anything that would depend on
  it.
* **SC010 is UNKNOWN for `U1`** by construction: the reduced behavioural buck has no
  switching node, and the boundary says so instead of implying full connectivity.

## Concurrency defect found and fixed during Phase 3

The simulator lock was a create-exclusive file released in a `finally`. A killed
run therefore left it behind, staleness was a one-hour mtime heuristic with no
liveness check, and after the timeout the code proceeded **without** the lock — so
queued runs both stalled and then could steal each other's deck. Observed: three
unrelated jobs waited their full 900 s on a lock whose owner had been dead for
15 minutes.

Fixed by holding an OS-level exclusive lock (`msvcrt.locking` / `flock`) on a
persistent file for the lifetime of the run, which the operating system releases on
process death, and by raising `LtspiceLockTimeout` instead of running unlocked.
`tests/ltspice/test_invocation.py` now covers both halves: a live holder excludes
another process, and a **killed** holder does not block the next run (the regression
test for this defect).

## Commands run (with observed results)

```powershell
uv run boardmodeler doctor --json              # smoke_test "pass", measured 0.632 V, reader_backend native
uv run boardmodeler demo build --out build/demo # 28 requirements, 10 test cases, SC001-SC009 PASS
uv run boardmodeler circuit check --project build/demo --json
uv run boardmodeler run mutations --project build/demo --report build/mutation-report.json
uv run boardmodeler export --project build/demo --out build/demo-export
```

## Next actions

1. Finish the Phase 6 regression baselines and re-run the full suite.
2. Keep `docs/DECISIONS.md` current; every decision that constrains later work is
   recorded there with its rationale and rejected alternatives.
