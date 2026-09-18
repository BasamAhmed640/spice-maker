# STATUS

Updated at every phase boundary. **"Observed" means the exact command was run and
the result below is its actual output** — never a remembered or expected value.

## Current milestone

**The model maker (D-014).** The product is: *datasheet + part number + agent key in,
an agent-authored model judged by real simulator runs out, saved where LTspice can use
it.* The board/circuit/UI layers built against the earlier, wider spec are dormant and
reachable only by explicit flags (`boardmodeler ui --board-ui`, `boardmodeler demo …`).

## What the product does (one paragraph)

Give it a datasheet and a part number; agents author an LTspice model; one deterministic
probe deck per datasheet characteristic runs in real LTspice and compares the measured
value to the cited limit; you get the `.lib`, a validated symbol, a runnable example
circuit, and a model card listing every row — judged, unknown, or not reachable by
simulation. Every status is one of `PASS/FAIL/UNKNOWN/BLOCKED/NOT_APPLICABLE`; no PASS is
ever recorded without an observed simulator artifact.

### Model maker — observed results (2026-09-18)

|What|Observed|
|---|---|
|Harness against the known-good `BM_REG_BUCK` library|**8 PASS / 0 FAIL**, 9 datasheet rows judged, **6.2 s** wall, one LTspice run per probe|
|Measured vs model constants|`uvlo_rise` 4.30223 V (UVLO_RISE 4.3) · `uvlo_fall` 3.89727 V (3.9) · `en_rise` 1.25106 V (1.25) · `en_fall` 1.14863 V (1.15) · `vref` 0.799993 V (0.8 VREF) · `current_limit` 3 A (ILIM 3) · `soft_start` 4.4853 ms (analytic 4.47 ms) · `load_regulation` error 0.049 % at 2 A · `pg_threshold` 24.9 mV low, 0.50 mA sink|
|Known-bad model (`VREF` 0.8 → 0.5)|`vref` **FAIL**: measured 0.499996 V vs required 0.792–0.808 V (page 4), detail names the deficit and appends the verbatim excerpt; 6 PASS + 1 UNKNOWN alongside|
|Missing port (`VOUT` renamed)|**8/8 UNKNOWN** with `port_missing:VOUT` — zero PASS, zero FAIL|
|Deck that cannot finish|UNKNOWN with the observed reason, never PASS|
|Bindings|all **38** TPS54320 rows accounted for: 9 bound to probes, 29 with a written `not_testable_reason`|
|Author loop (scripted agent, real LTspice)|turn 1 writes the bundled template → **PASS**, 8 probe outcomes, 7.5 s wall|
|Loop safety paths (scripted)|fail-then-pass ends PASS after 2 turns with the turn-1 feedback in the turn-2 prompt · spec edit → `UNKNOWN(spec_tampered)` with **zero** simulations run · cap → UNKNOWN naming the failing probe · unavailable backend → BLOCKED with the reason verbatim|
|Bob integration|argv verified against IBM's docs: `bob run --format json --max-turns <n> [--team-id <t>] <prompt>`; `status:error` → failure; timeout/cancel kills the process tree; a sentinel key never appears in results, argv, or messages; availability reports `bob_shell_not_installed` / `bob_credentials_unavailable`|
|Suites|`uv run pytest -q tests/authoring` → **103 passed** (independently re-run); `tests/gui/test_model_maker.py` → 3 passed; `tests/test_cli_model.py` → 5 passed|
|Chain (`pipeline/make_model.py`)|six stages read/extract/bind/author/judge/save; TPS54320 fixtures + scripted author + **real LTspice** → **PASS in 10.7 s for 38 rows** (9 bound rows PASS, 29 `NOT_APPLICABLE` with written reasons), publishes lib + symbol + card + example + `results.json`|
|Discrimination through the chain|perturbed model → one FAIL row (`v_fb = 0.5 V` vs `min 0.792 / max 0.808 V`); missing port → that row UNKNOWN `port_missing:PG`|
|Honest stops|no LTspice → BLOCKED `ltspice_not_found` with **zero** agent turns; no `bob` on PATH → BLOCKED `bob_shell_not_installed` **verbatim** (no silent fallback to another provider); cancel → UNKNOWN `cancelled`; agent edits the spec → UNKNOWN `spec_tampered` with no simulation run|
|Cost of a repeat run|extraction cached: second run over the same datasheet → **0** provider calls (4 cache hits)|
|Binder|deterministic: two runs write byte-identical `bindings.json`, and its map equals the reviewed `probes.json` exactly|
|Suites|`uv run pytest -q -m "not ltspice"` → **796 passed, 1 skipped, 0 failed**; `tests/authoring tests/pipeline/test_make_model.py tests/gui` → **128 passed** (real LTspice runs included)|
|Termination|**no wall clock**: `max_iterations=None` by default (runs until satisfied), 2 consecutive no-progress turns end as `UNKNOWN` naming the stall and the probes still failing, agent invocations unbounded unless a caller sets `turn_timeout_s`. Progress = the model bytes changed **and** the failing set is not identical to the previous turn's. Observed: an agent improving over six turns reaches PASS (`iterations=6`, no hidden cap); a repeating agent stops after exactly two no-progress turns|
|Web reinforcement|one bounded search per part before the agent starts; candidates come from the agent, every candidate is fetched by our own client (TLS default, 1 MiB cap, redirect cap, text/pdf only, unreachable → recorded with its reason); only text we retrieved is stored, verbatim with sha256, in `spec/supporting.json`, and the card lists it under "Supporting material (searched, not evidence for the verdicts)". The stage cannot change a status or fail a build; `--no-reinforce` / the setup switch disable it. The search is cancel-aware and carries its own `reinforce_timeout_s` (default 300 s); expiry records `unavailable` with the budget reason and the build continues — the author loop itself stays unbounded|
|Sweep|`uv run pytest -q tests/authoring` → 163 passed; `tests/pipeline/test_make_model.py` → 20 passed; `tests/ui tests/gui` → 50 passed; `-m "not ltspice"` → 855 passed, 1 skipped, 0 failed|
|Model maker window (`boardmodeler ui`)|part number · datasheet · model folder · GO with the progress detail, plus SETUP and CHECK ENVIRONMENT buttons; stage table and datasheet-row table. Fixed 900×600; controls styled from the shared `RETRO_STYLESHEET` (no window rule can repaint a button)|
|Setup page (`boardmodeler setup` / SETUP)|one page of persistent settings: LTspice path + RUN SMOKE TEST, the agent provider and its API key, MODEL FOLDER, LTspice user library shown read-only, web reinforcement. Sized to its content; no fixed-height dead space. `boardmodeler setup --json` prints the same settings (see the next section for the provider row and the Bob-only build)|

### Identity, agent providers and the installer (2026-09-18, second pass)

The owner asked for three more things on top of the model maker: rename the repository to
*Spice Maker*, ship it as a one-click installer carrying the pepper mark, and make the agent
take **raw API keys — Bob's by default, plus the mainstream vendors — with no login
anywhere**. Then two repositories from one codebase, one restricted to the IBM Bob API, and
"make sure OpenCode Go is an option for the testing one". Nothing was added outside the
model package: the product is still the IC model, and the harness still owns every verdict.

|What|Observed|
|---|---|
|Repository|`boardmodeler` renamed to `spice-maker` (GitHub API `PATCH` → HTTP 200; `git ls-remote` on the new URL answers `c5731c7…`). The local clone keeps its directory name|
|Product identity|window/app name *Spice Maker*, frozen `SpiceMaker.exe`, install dir `%LocalAppData%\SpiceMaker`. The Python distribution (`boardmodeler`), console script, keyring service and `%APPDATA%\BoardModeler` config path are unchanged on purpose (D-015) so an existing install keeps its stored key and settings|
|Agent catalog|`agent_providers.CATALOG`: IBM Bob (default) + OpenAI, Anthropic, Google Gemini, DeepSeek, OpenRouter, xAI, Groq, Mistral, **OpenCode Zen / Go**. Endpoints and default model ids are the vendors' documented values and each entry carries its doc URL; a build with one entry *is* the Bob-only build|
|Bob, natively|Bob Shell is the documented consumer of an Inference-scope key: `BOB_API_KEY` alone authenticates (`bob run --format json --max-turns N <prompt>`, key only in the child environment), `--team-id` for a *general* key. The key bit is already stored here and reads `source=keyring`; without Bob Shell the build stops as `BLOCKED bob_shell_not_installed` with the install URL, and never substitutes another provider|
|Bob over HTTP|probed and refused: Cloudflare `403` (bot-management HTML) for `urllib`, `curl` and Bun `fetch` on `api.us-east.bob.ibm.com/inference/v1/models` and `/chat/completions`, with `Authorization: Apikey` and `Bearer` alike. IBM documents no inference path, so no guessed endpoint ships (D-005)|
|OpenCode Zen / Go|`POST https://opencode.ai/zen/v1/chat/completions` with no key → `401 {"type":"error","error":{"type":"AuthError","message":"Missing API key."}}`; with a bogus `Authorization: Bearer` key → `Invalid API key`; with `x-api-key` → `Missing API key`. Wire and auth scheme verified against the live gateway|
|DeepSeek|live `POST https://api.deepseek.com/chat/completions` → HTTP 200 (`deepseek-flash`). Both of its models reason first: on the pipeline's own 23.8 kB prompt they spend the entire output budget on `reasoning_content` and return empty `content` at 12 288 and 32 768 tokens (3 observed runs, 57–303 s), so a DeepSeek key cannot finish an authoring turn today. The backend reports it honestly (`response_empty … finish_reason='length'`) instead of pretending, and the budget is settable (`--max-tokens`, `agent_max_tokens`, default 32 768)|
|Setup page|one content-sized page: LTspice + RUN SMOKE TEST, an `AGENT` row (only when the build's catalog holds more than one provider), that provider's own `… API KEY` row + SAVE KEY, a `MODEL` row for providers that take one, MODEL FOLDER, LTSPICE LIBRARY read-only, web reinforcement. Rendered offscreen: 1016×292 with Bob selected, 1042×325 with Anthropic — each equal to its `sizeHint`|
|Doctor|one line per catalog provider, source only — e.g. `deepseek=source=env` when only the vendor variable is set — and never a value|
|Installer|`releases\SpiceMaker-win-Setup.exe` = **63,077,965 B** over a **124 MB** payload (was 122.6 MB over 254 MB before the freeze excludes): one-click Velopack setup with the animated pepper splash and no wizard pages, Start Menu + desktop shortcuts, `QuietUninstallString` uninstall. It carries no LTspice, Bob Shell or Python payload — SHA-256 of all 16 606 files under the two raw LTspice trees is unchanged across install and uninstall|
|Frozen app|`SpiceMaker.exe --cli setup --json` prints the settings JSON with `agent_api_key … source=keyring`; `-m boardmodeler.cli doctor --json` prints the doctor JSON (the exact form `ui/model_maker.py` re-enters with, so CHECK ENVIRONMENT works frozen); the window `Spice Maker — IC model maker` was captured running from the installed build|
|Model package, judged frozen|`build/frozen-check` — built from the committed fixtures with the bundled author in **11.4 s** of real LTspice work (lib + symbol + card + example + report) — re-judged by the *installed* app: `model test` → **PASS, 8 PASS / 0 FAIL**|
|Suites|`uv run pytest -q` → **1052 passed, 1 skipped, 0 failed** (5 m 44 s, LTspice-marked tests included; the skip is the network opt-in in `tests/providers`); `tests/gui tests/ui` → 53 passed; `uv run ruff check .` and `uv run ruff format --check .` → clean|

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

Those FAILs are **one model-fidelity finding, not three board faults** (D-013):
measured, they appear only in the window containing a hard load step, scale with the
step amplitude, are unchanged by a 10x loop-gain increase, and worsen with more output
capacitance — the large-signal step response of a reduced model whose compensation is a
template constant. They should be UNKNOWN under the capability gate (D-009); they are
FAIL only because the demo build was never given a `workdir`, so no capability records
exist for the board's generated models. Fixing that is the next action in D-013.

An earlier draft of this paragraph called these FAILs **findings, not test bugs**: the fixture declares a ±5 % window and
the reduced behavioural models exceed it on a load step. They are reported as they
are; nothing was widened to turn them green.

Fault matrix (`boardmodeler run mutations`): **every fault in the plan's required
set is detected** — `swap_straps`, `en_invert`, `missing_pullup`,
`pullup_wrong_domain`, `early_reset_release`, `missing_pg`, `slow_rail_u2` (7 of 7).
Sweeping all twelve mutators shows **9 of 12 detected**: `invalid_strap`,
`break_sideband` and `remove_rail` change the verdict to FAIL but not through the
check each declares (`strap_word_invalid`, `open_drain_level`,
`SC009_supply_domain_assignment` respectively), so those three are listed here as
open rather than counted as detections
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
uv run pytest -q                               # 785 passed, 1 skipped (includes the LTspice-marked tests)
uv run boardmodeler doctor --json              # smoke_test "pass", measured 0.632 V, reader_backend native
uv run boardmodeler demo build --out build/demo # 30 requirements, 10 test cases, SC001-SC009 PASS
uv run boardmodeler circuit check --project build/demo --json
uv run boardmodeler run mutations --project build/demo --report build/mutation-report.json
uv run boardmodeler export --project build/demo --out build/demo-export
```

## Next actions

1. Keep `docs/DECISIONS.md` current; every decision that constrains later work is
   recorded there with its rationale and rejected alternatives.
