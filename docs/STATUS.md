# Maintenance 1.1.3 — 2026-09-20

Exact target parts now reach extraction and its cache key; suffixes from one family PDF
cannot share unrelated cached rows. JSON diagnostics redact full responses before
excerpting them, including partial-secret boundary alignments. Draft changes are now
covered by regression tests instead of being untested working-tree-only changes.

This general edition contains 11 provider entries. The Bob edition now deliberately
owns a distinct Bob-only catalog, author adapter, UI and related tests/documentation.
The sync helper preserves those differences rather than copying them back.

Installer 1.1.3 replaces the prior root binary while keeping Install.exe in the main
Code-menu ZIP. Build cleanup matches the exact package/version filename, so 1.1.1
cannot remove 1.1.10 artifacts; the project Python 3.14 environment is required.
Historical sections below describe their original revisions, not the current release.
The Windows workflow is a manually dispatched build job; no unobserved CI packaging
run or bit-for-bit reproducibility is claimed.

A complete LM358 model remains unqualified: prior live extraction returned invalid
JSON despite HTTP success. These fixes are not evidence that every op-amp behavior has
an implemented probe. Current test/build results are recorded after execution below.

# Desktop PDF retry and Go subscription — 2026-09-20

The reported LM358 run stopped before inference: the same cached PDF was first
registered without remote permission and then retried with permission. The document
store now permits only that explicitly requested permission change; content and all
other metadata conflicts remain refused. A regression grants and revokes permission
through the actual model pipeline's read stage.

OpenCode Go now has a separate provider entry and documented /zen/go/v1 endpoint,
sharing the OS credential slot with the explicitly separate Zen option. API calls
identify SpiceMaker and keep a stable x-opencode-session through extraction and repairs.
Supported default models use their highest documented effort; production catalogs no
longer switch thinking off after an empty response.

Observed live checks with the user's stored OpenCode credential: Zen returned HTTP 401
(insufficient Zen credit); Go returned HTTP 200 for a short SPICE authoring request at
max reasoning (168 output tokens, 135 reasoning tokens). No secret was written into
repository or diagnostic files. The user's unchanged 68-page LM358 PDF passed the
previously failing registration step and reached live Go extraction.

# Code menu ZIP includes the installer — 2026-09-20

Added this edition's verified v1.1.1 Install.exe, INSTALL.txt and SHA256SUMS.txt at the
repository root. The installer is byte-identical to the already GUI-tested release ZIP
payload; its SHA256 matches the included checksum. Updated build.ps1 to refresh these
root files after successful packaging, and README instructions to use Code -> Download
ZIP on main. No backend or frozen application code changed.

# Windows startup hotfix 1.1.1 — 2026-09-20

Confirmed the 1.1.0 QtWidgets startup crash: The frozen build contained an incompatible ICU 78 `icuuc.dll`; its original
provenance was not established. Qt requested
`ucnv_open`; the bundled DLL exposed `ucnv_open_78`. Loading Qt6Core failed with Windows
error 127; preloading Windows System32/icuuc.dll made the same Qt6Core load successfully.

Changes: `installer/freeze.py` limits dependency discovery to Python/Windows locations;
`installer/build.ps1` now requires the actual frozen GUI to open before packaging.
`installer/verify_gui.py` launches from an empty directory with a minimal PATH, requires
a visible responsive Qt window, captures it, and checks clean exit. Both README pages
link directly to their own v1.1.1 download; the Windows workflow default is 1.1.1.

Observed checks:
- The new GUI verifier rejected the unchanged 1.1.0 executable's error dialog.
- `installer/build.ps1 -Version 1.1.1` succeeded in both repositories, including the
  real Windows GUI launch, screenshot, clean exit, animated setup and download ZIP.
- Both captured model-maker windows were visually inspected.
- Ruff check and format check passed for both new Python packaging helpers.
- Both existing 1.1.0 installations were removed using their registered uninstallers,
  exit 0. Their installation folders and uninstall registrations are gone.

# Faster model creation and I/O validation — 2026-09-19

Implemented the shared selected-key extraction/author path, batched extraction with one
bounded semantic repair, current-model repair context, numeric progress ranking, retained
best candidate, immutable attempt snapshots, and artifact-checked validation reuse. Bob
prompts use stdin and repairs resume the exact task. Conditions and pin maps reach the
frozen spec; distinct operating points produce distinct tests. Nine electrical I/O probes
and an explicitly unvalidated vendor IBIS/AMI/Touchstone import path are available.
Both editions share source/tests with an explicit Bob-only build flavor.

Observed checks before the publishing gate:

- `uv run pytest -q -m "not ltspice and not network"`: 1009 passed, 4 skipped
  (git-ignored vendor originals), before the final extraction-repair regression was added.
- General: focused real-simulator/integration/controller suite: 43 passed in 37.85 s.
- Bob: `uv run pytest -q tests/authoring/test_fast_io.py`: 7 passed in 12.61 s.
- `uv run python -m tools.benchmark_authoring`: nine synthetic I/O probes in real
  LTspice, first validation 5.5378 s / one scripted author turn; repeated validation
  0.0632 s / zero author turns. This excludes API latency and device qualification.
- Bounded live DeepSeek extraction of an explicitly synthetic one-page PDF: 40.16 s,
  four pins, two requirements, no validation issues; repeat used all four cached tasks.
  Earlier live format/classification failures led to the combined schema and bounded
  correction; no invalid result is accepted as a valid model specification.
- Both Velopack packages built successfully; each download ZIP contains the original
  Setup.exe bytes under Install.exe, checksum and readme. Splash GIFs have 90 frames.

An initial broad suite found fixture/schema integration failures, now repaired, and a
pre-existing timing-dependent baseline rewrite; unchanged baseline bytes are now retained.
See the publishing gate/CI for the final committed revision. High-speed electrical and
protocol simulation, arbitrary-device accuracy and temperature qualification are not
claimed by these checks. No Bob live inference result is claimed.

---

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
|Loop feedback (live)|an unreadable run's reason now carries the simulator's own last error lines, whether no `.raw` was written at all (`sim_output_unreadable: no .raw was written (…); LTspice said: …\BM_REG_BUCK.lib(95): Undefined model "rout_drv"`) or LTspice wrote one and it holds no data (`… no 'Binary:' or 'Values:' section found …; LTspice said: Voltage source V_en and voltage source B_enable are paralleled making an over-defined circuit matrix. | You will need to correct the circuit or add some series resistance.` — LTspice prints that pair with no prefix of its own, so the log parser now keeps it instead of dropping it) — and that reason is the text the next turn's prompt quotes, so a turn can tell a syntax error from a missing file (before, both read as "no `.raw`"). The API backend also re-asks **once** inside a turn when the reply is not the required JSON object, quoting the parse error back, and once more with the entry's own fallback setting (`retry_body`: thinking off for DeepSeek) when a reply comes back empty at the model's output limit; a retry that also fails is reported with both attempts named|
|Bob integration|argv verified against IBM's docs: `bob run --format json --max-turns <n> [--team-id <t>] <prompt>`; `status:error` → failure; timeout/cancel kills the process tree; a sentinel key never appears in results, argv, or messages; availability reports `bob_shell_not_installed` / `bob_credentials_unavailable`|
|Suites|`uv run pytest -q tests/authoring` → **103 passed** (independently re-run); `tests/gui/test_model_maker.py` → 3 passed; `tests/test_cli_model.py` → 5 passed|
|Chain (`pipeline/make_model.py`)|six stages read/extract/bind/author/judge/save; TPS54320 fixtures + scripted author + **real LTspice** → **PASS in 10.7 s for 38 rows** (9 bound rows PASS, 29 `NOT_APPLICABLE` with written reasons), publishes lib + symbol + card + example + `results.json`|
|Discrimination through the chain|perturbed model → one FAIL row (`v_fb = 0.5 V` vs `min 0.792 / max 0.808 V`); missing port → that row UNKNOWN `port_missing:PG`|
|Honest stops|no LTspice → BLOCKED `ltspice_not_found` with **zero** agent turns; no `bob` on PATH → BLOCKED `bob_shell_not_installed` **verbatim** (no silent fallback to another provider); cancel → UNKNOWN `cancelled`; agent edits the spec → UNKNOWN `spec_tampered` with no simulation run|
|Cost of a repeat run|extraction cached: second run over the same datasheet → **0** provider calls (4 cache hits)|
|Binder|deterministic: two runs write byte-identical `bindings.json`, and its map equals the reviewed `probes.json` exactly|
|Suites|`uv run pytest -q -m "not ltspice"` → **796 passed, 1 skipped, 0 failed**; `tests/authoring tests/pipeline/test_make_model.py tests/gui` → **128 passed** (real LTspice runs included)|
|Termination|**no build deadline**: `max_iterations=None` by default (runs until satisfied), 2 consecutive no-progress turns end as `UNKNOWN` naming the stall and the probes still failing. The loop API leaves agent invocations unbounded unless a caller sets `turn_timeout_s`; the product `api` path (including a Bob API key) applies a finite 600 s per-turn default, overridable per run, while a direct `--backend bob` CLI turn stays unbounded absent an override. Progress = the model bytes changed **and** the unknown-row/failing-row/numeric-error ranking improved. Observed: an agent improving over six turns reaches PASS (`iterations=6`, no hidden cap); a repeating agent stops after exactly two no-progress turns|
|Web reinforcement|one bounded search per part before the agent starts; candidates come from the agent, every candidate is fetched by our own client (TLS default, 1 MiB cap, redirect cap, text/pdf only, unreachable → recorded with its reason); only text we retrieved is stored, verbatim with sha256, in `spec/supporting.json`, and the card lists it under "Supporting material (searched, not evidence for the verdicts)". The stage cannot change a status or fail a build; `--no-reinforce` / the setup switch disable it. The search is cancel-aware and carries its own `reinforce_timeout_s` (default 45 s); expiry records `unavailable` with the budget reason and the build continues — the author loop itself stays unbounded|
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
|DeepSeek|live `POST https://api.deepseek.com/chat/completions` → HTTP 200. Its models reason *first*: left at their defaults on the pipeline's own 23.8 kB prompt they spend the whole output budget on `reasoning_content` and return empty `content` (12 288 and 32 768 tokens, 3 runs, 57–303 s) — reported as `response_empty … finish_reason='length'` **and now avoided**: the catalog carries DeepSeek's documented thinking switch per entry, and MODEL stays editable per machine. Measured on the TPS54320 fixture: `{"thinking": {"type": "disabled"}}` → both files in ~10 s but **0 PASS** in three turns (the deck referenced an undefined sub-model); `{"thinking": {"type": "enabled"}, "reasoning_effort": "low"}` (shipped) → **4 PASS / 0 FAIL** after three turns, the rest UNKNOWN on the model's own convergence. `deepseek-flash` is the id DeepSeek's model list serves as *DeepSeek-V4.1-Flash*|
|Setup page|one content-sized page: LTspice + RUN SMOKE TEST, an `AGENT` row (only when the build's catalog holds more than one provider), that provider's own `… API KEY` row + SAVE KEY, a `MODEL` row for providers that take one, MODEL FOLDER, LTSPICE LIBRARY read-only, web reinforcement. Rendered offscreen: 1016×292 with Bob selected, 1042×325 with Anthropic — each equal to its `sizeHint`|
|Doctor|one line per catalog provider, source only — e.g. `deepseek=source=env` when only the vendor variable is set — and never a value|
|Installer|`releases\SpiceMaker-win-Setup.exe` = **63,077,965 B** over a **124 MB** payload (was 122.6 MB over 254 MB before the freeze excludes): one-click Velopack setup with the animated pepper splash and no wizard pages, Start Menu + desktop shortcuts, `QuietUninstallString` uninstall. It carries no LTspice, Bob Shell or Python payload — SHA-256 of all 16 606 files under the two raw LTspice trees is unchanged across install and uninstall|
|Frozen app|`SpiceMaker.exe --cli setup --json` prints the settings JSON with `agent_api_key … source=keyring`; `-m boardmodeler.cli doctor --json` prints the doctor JSON (the exact form `ui/model_maker.py` re-enters with, so CHECK ENVIRONMENT works frozen); the window `Spice Maker — IC model maker` was captured running from the installed build|
|Model package, judged frozen|`build/frozen-check` — built from the committed fixtures with the bundled author in **11.4 s** of real LTspice work (lib + symbol + card + example + report) — re-judged by the *installed* app: `model test` → **PASS, 8 PASS / 0 FAIL**|
|Suites|`uv run pytest -q` → **1052 passed, 1 skipped, 0 failed** (5 m 44 s, LTspice-marked tests included; the skip is the network opt-in in `tests/providers`); `tests/gui tests/ui` → 53 passed; `uv run ruff check .` and `uv run ruff format --check .` → clean|

### Model-accuracy evidence, part-class refusals, hardening (2026-09-18, third pass)

The owner asked for strong tests on the actual models with reported timing and accuracy, and
for parts the harness cannot judge to be refused rather than modelled. Both are now measured
rather than asserted (`tools/measure_models.py` writes `build/model-measurements.{json,md}`).

|What|Observed|
|---|---|
|Model build, real LTspice|TPS54320/BM_REG_BUCK from the committed fixtures, bundled author: **15.2 s** wall (11.2 / 11.7 / 15.2 over three runs), **PASS**, 8 probes, one author turn. Stages: read 4.7 s, bind 0.009 s, author+judge 6.4 s, save 0.16 s|
|Re-judge (`model test`)|**10.2 s** (6.7 / 7.6 / 10.2 over three runs), PASS, 8 LTspice invocations, model sha256 unchanged by the re-judge|
|Accuracy|9 judged rows PASS / 0 FAIL of 38 rows (29 `NOT_APPLICABLE` with written reasons). Measured vs the datasheet's own limits: `i_heavy` 2 A vs max 3 A (33 % margin), `vin_at_start` 4.30223 V vs 4–4.5 V (4.4 %), `en_at_start` 1.25106 V vs 1.21–1.26 V (0.71 %), `en_at_stop` 1.14863 V vs 1.1–1.17 V (1.8 %), `v_fb` 0.799993 V vs 0.792–0.808 V, `i_vin` 75.7 µA vs max 800 µA (90.5 %)|
|Determinism|two independent builds produce **byte-identical** `.lib` and `.asy` (sha256 `28f5af84…`, `e25fd7ba…`) and identical row tables|
|Discrimination (the strong test)|8 perturbations on copies of the built model, each with a written prediction: the three *inside-tolerance* changes (UVLO +40 mV, VREF +6 mV, EN +5 mV) flip nothing (8 PASS, 0 FAIL); the three *outside* changes flip exactly the predicted rows to FAIL with the measured value; dropping the `PG` port turns that row into `UNKNOWN` (never PASS); replacing the model body with a comment turns all 9 judged rows `UNKNOWN` with **0 PASS**. **8 of 8 predictions held**|
|Part-class refusal|`unsupported_part_class: <kind>: <reason>` — MCUs (STM32/ATmega/MSP430/ESP32/RP2040/nRF52/PIC) and programmable logic (Xilinx 7-series/UltraScale/Zynq, Cyclone/MAX 10/Arria/Stratix, ECP5/iCE40/MachXO) are refused before any agent turn or simulation, with the reason naming what the probes measure. Observed: `model build --part STM32F407 …` → `BLOCKED — unsupported_part_class: microcontroller: the part number matches the STMicroelectronics STM32 family; the probes measure analogue thresholds and regulation; a firmware-defined part has no datasheet row this harness can bind` (exit 1 under `--strict`, nothing written); `--part XC7A35T` → the same shape for `fpga`. Lookalikes (MAX232, XC6206, LTC3891) stay supported|
|Security hardening|a read-only audit of the API-key path found no key-leak path (not falsified) and three defects, now fixed: a Windows path component with a trailing dot/space is refused before the containment check; control characters (NUL) in a reply name are refused instead of raising out of `author()` (and an unwritable path reports `api_write_failed … may be incomplete` rather than escaping as `backend_error`); the GUI no longer substitutes the default provider for a configured-but-unaccepted id — the raw id reaches `build_api_backend` and SETUP/`setup --json` report it with an `agent_provider_accepted` flag. Note recorded honestly: the trailing-space escape was **not reproducible** on this platform/Python (the audit's reading was static), and the refusal is kept as defence in depth|
|Bob-only build|the same test suite passes with a one-entry catalog: 99 passed in the general build and 99 passed in the trimmed build for the six catalog-touching files, so the restricted repository ships green tests rather than 35 failures|
|Suites|`uv run pytest -q` → **1112 passed, 1 skipped, 0 failed** (6 m 27 s, LTspice included); `uv run ruff check .` and `uv run ruff format --check .` → clean|
|Environment note|two full-suite runs died with a Windows fatal access violation inside `pypdf` while several agents and LTspice runs shared the machine; the same call reproduced `NameError: name '_LENGTH_LIMIT' is not defined` in a tight loop. The pypdf files match their RECORD and the symbol is defined, and clearing the `__pycache__` directories made 36 subsequent reads clean and the suite green — recorded here so a future crash is not mistaken for a code regression|

### Delivery of the two builds (2026-09-18, same day)

|What|Observed|
|---|---|
|Gate|`no-mistakes axi run` on `feature/api-key-agents-and-installer`: review found five items (per-provider request parameters, Bob `team_id` dropped on the new default backend, unbounded injected-backend search, unguarded config read in the GO slot, unvalidated `agent_max_tokens`) and fixed them in `bc6a149`; `document` refreshed the provider/setup/CLI docs in `4dc439d` and `541ea5d`; `test` ran the suite; `pr` and `ci` were **skipped automatically because `gh` is not installed**, which is why the pull request was opened through the API instead|
|General repo|branch pushed at `541ea5da`; pull request [#1](https://github.com/BasamAhmed640/spice-maker/pull/1) is open against `main` for review|
|Bob-only repo|`github.com/BasamAhmed640/spice-maker-bob` published at `dd6ceb5` = the general head `541ea5da` + one commit that trims `CATALOG` to `(bob,)` and states the restriction in the README; its own suite runs green there once the git-ignored datasheet originals are present: `uv run pytest -q` → **1116 passed, 1 skipped, 0 failed** in its own fresh environment|
|Installer from the validated head|`releases\SpiceMaker-win-Setup.exe` **63,089,442 B** (payload 122.8 MiB, +11 KB over the previous build), installed silently; the installed executable's md5 equals the fresh build's, the window title is byte-exact `Spice Maker — IC model maker`, `--cli setup --json` reports `agent_api_key … source=keyring` and ten accepted providers including `opencode`, `-m boardmodeler.cli doctor --json` passes the LTspice smoke test, and the frozen binary re-judged a real model package **PASS, 8 PASS / 0 FAIL** in 13.0 s with the `.raw` files rewritten by real runs|

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

## 2026-09-20 — shared row-verdict follow-up

Supply-current exclusions now normalize ASCII and Unicode dash separators in the
statement only. Output-leakage rows under a supply-current heading remain testable.
Rows sharing one simulation are judged against their own limits; result and card
totals count those rows, while harness/progress counts remain simulator-case counts.
Unavailable simulator outcomes retain UNKNOWN/BLOCKED even with partial measurements.

Observed checks: `python -m ruff check .` passed; `python -m pytest -q -m
"not ltspice and not network"` passed 1048 tests, with 4 skipped and 128 deselected
(25.41 s, ambient DeepSeek key removed from the child process). A real CLI/LTspice
synthetic split-limit check produced one observed waveform/log pair, one passing row,
one failing row, and matching card totals with zero API calls. No real device accuracy
is inferred from this artificial threshold fixture. The cancellation-only unit test
now provides a fake simulator installation, fixing the failure on GitHub runners
without changing production missing-simulator behavior.


1.1.2 packaging verification: both builds passed the actual frozen Windows GUI launch
before packaging. Root Install.exe is byte-identical to this edition's ZIP payload and
canonical Velopack setup; its SHA256 matches the root checksum. General full offline
suite: 1052 passed, 4 skipped (before eight additional default-reasoning cases, all passed
in their focused run). Bob: 1050 passed, 14 skipped. Ruff check/format passed, 190 files.
The installed desktop executable was updated and matched the rebuilt application hash;
its real window opened, responded and exited cleanly. The selected provider is now Go,
with the existing OS credential and requested model preserved.


## Observed 1.1.3 checks

- `.venv/Scripts/python.exe -m pytest -q -m "not ltspice" --tb=short`: 1063 passed, 5 skipped, 127 deselected. Skips identify absent vendor originals and the opt-in network documentation check.
- Ruff check and format completed successfully.
- `installer/build.ps1 -Version 1.1.3` completed, including a real responsive frozen
  Qt window, animated setup and root/ZIP installer publication.
- Root Install.exe matches the canonical setup and release ZIP installer byte for byte.
  Both splash GIFs retain 90 frames.
- The actual build cleanup code retained 1.1.10 and a different package's artifacts
  while deleting only requested 1.1.1 filenames in an isolated test directory.

- `.venv/Scripts/python.exe -m pytest -q -m ltspice --tb=short`: 127 passed, 1068 deselected, against the real installed simulator (272.42 seconds).

- The screenshot helper now restores/foregrounds and redraws its own test window before capture. An incomplete background capture was corrected and the Bob window was visually checked.


## Observed 1.1.4 timer checks

- Added a monotonic elapsed clock beside GO, independent of progress messages; it
  includes cancellation cleanup, freezes on completion/error, and resets on the next run.
- `python -m pytest -q tests/gui -m "not ltspice" --tb=short`: 14 passed,
  1 real-simulator integration test deselected. New cases drive actual GO clicks and a
  quiet worker through success, blocked results, exceptions, cancellation and restart.
- `python -m ruff check .` and `python -m ruff format --check .`: passed.
- `installer/build.ps1 -Version 1.1.4`: succeeded, including the actual frozen GUI launch.
- Root Install.exe matches the release ZIP and canonical setup; checksum verified.
  The pepper animation retains 90 frames. No paid API requests were made.
- Agent roles and present LM358 qualification limits are documented in AGENT_WORKFLOW.md.


## 1.1.5 LM358 correction — 2026-09-20

Added the exact-datasheet reviewed profile, dual-op-amp DC/AC/transient probes, complex raw support, and op-amp follower export. Offline suites initially found a missing probe question and an outdated registry unit whitelist; both corrected. Final checks and package evidence are recorded below.

Final local checks: `pytest -q -m "not ltspice and not network"`: 1106 passed, 4 skipped (git-ignored vendor originals). `ruff check .` and `ruff format --check .`: passed. Frozen GUI launch and animated package checks: passed; release ZIP and root Install.exe are identical. Shared simulator suite: 151 passed; the subsequently added exported-op-amp-example test also passed. Instrument-only tests use a labeled synthetic fixture, not claimed device data.

Live selected-key LM358 source run at maximum thinking: 221.9 seconds, 32 nominal comparisons passed in real LTspice, 10 explicit coverage gaps. The initial 32,768-token failure was reproduced from the provider finish reason and usage. Offline key-route tests cover all ten HTTP provider entries, including authorization failures; these are not live certification of other accounts. Reasoning preservation/recovery covers none/low/high/max.


## 2026-09-20 — standardized IC symbols (1.1.6)

- `python -m ruff check .` and `python -m ruff format --check .`: passed.
- `python -m pytest -q -m "not ltspice and not network"`: 1112 passed,
  4 skipped (unavailable vendor originals), 154 deselected.
- Shared renderer integration, run in the general checkout:
  `python -m pytest -q tests/reporting/test_symbol_layout.py tests/pipeline/test_make_model.py::test_scenario_a_scripted_template_passes_and_publishes_the_deliverables`:
  8 passed, including actual LTspice schematic-to-netlist pin-order verification and
  a complete simulated publication. The renderer and new test are identical in both editions.
- Opened the saved LM358 model with the new symbol in LTspice 26.0.0.3: visible leads,
  separated labels, all pins within the body height, reference and value outside.
- No paid inference requests were needed for this change. This does not add electrical
  coverage or establish a new live IBM Bob result.


## 2026-09-20 — compound datasheet units (1.1.7)

The saved SN74LVC1GX04 extraction failed before authoring: the unit vocabulary
rejected valid `ns/V` and `°C/W` limits. Recognition now includes time/voltage,
voltage/time, current/time and thermal resistance. Numerator and denominator
prefixes are scaled independently. Frozen characteristics use the same conversion.
Unmeasurable rows remain explicit gaps; accepting a unit does not create a probe.

- `python -m ruff check .` and `python -m ruff format --check .`: passed.
- `python -m pytest -q -m "not ltspice and not network"`: 1126 passed,
  4 skipped (unavailable vendor originals), 154 deselected.
- New checks cover compound scaling, incompatible dimensions, extraction without
  an unnecessary correction request, cached replay and retaining unprobed rows.


## 2026-09-20 — diverse datasheets and bounded recovery (1.1.8)

Implemented independent frozen circuit recipes with physical pin maps, bounded cached
extraction batches, explicit unsupported quantities and coverage gaps, whole-response
deadlines, observed failure feedback on resumed runs, stable candidate retention,
structural library preflight, and corrected RAW/path handling. No provider or reasoning
level is silently substituted. The Bob catalog and API backend remain Bob-only.

Observed checks:
- `.venv/Scripts/python.exe -m pytest -q -m "not ltspice and not network" --disable-warnings`: 1156 passed, 4 skipped.
- Final shared partial-fixture change: `pytest -q tests/authoring/test_circuit_probe.py -m "not ltspice"`: 24 passed, 2 deselected in the general checkout.
- General real-simulator regression: `pytest -q tests/authoring/test_circuit_probe.py tests/authoring/test_live_failure_regressions.py tests/pipeline/test_make_model.py -m ltspice`: 9 passed, 80 deselected.
- Ruff lint/format checks passed before final packaging. The final general frozen GUI opened; the final Bob executable was blocked by Windows signing policy. Earlier Bob startup passes were intermediate builds only.

The live five-datasheet matrix includes TLV9002, SN74LVC1G14, SN74LVC1T45,
TPS7A2033 and LMV331. All five final model files passed 30 independent real-LTspice
functional checks (3, 1, 8, 10 and 8 respectively). Checks cover both amplifier channels,
both logic edges, translator directions, unequal supplies, ground offsets, regulator
full load and shutdown, and comparator output states. The regulator has 14 covered
PASS results and one covered UNKNOWN settling check. All devices retain untested
numeric rows as UNKNOWN. Translator propagation timing was omitted by extraction.

Earlier failed regulator candidates are historical evidence, not the published model.
The final repair request ended with an incomplete provider stream; the last measured
candidate was preserved. Its 731-second run demonstrates that maximum-reasoning API
latency remains unresolved. Latest replay times are not cold-start speed benchmarks.
These tests do not establish every datasheet, all operating conditions, manufacturer
diversity (all five are TI), or a new live Bob account result. Software regression
passes must not be represented as device certification. See DATASHEET_ROBUSTNESS.md.


Final shared regressions: all 29 circuit-recipe tests passed with real LTspice
(including node aliases and a deliberately wrong output with a DC hint). General
offline suite: 1159 passed, 4 skipped, 159 deselected. Bob focused latest regressions:
29 passed, 4 skipped, 5 deselected. General final frozen executable passed all seven
TLV9002 checks and the release ZIP installer matched the root checksum. General
installer completed with exit 0; installed and built executable SHA256 both equal
51bbecc18c56c8b1be49ba6bf745fccc342a9622d33c07ec25945950da1208ba.

Final source CI at `cc7c558` passed: 1158 passed, 5 skipped, 159 deselected; Ruff lint and format passed.
[GitHub Actions evidence](https://github.com/BasamAhmed640/spice-maker/actions/runs/35535579351).

## 2026-09-20 — credential connection check (1.1.9)

SAVE & CHECK KEY now checks the exact newly saved credential asynchronously, with
a 15-second UI deadline, cancellation on close/provider change, no automatic retries,
and fixed messages that never echo provider bodies or exception text. HTTP checks
use a 256-token inference budget and retain model/reasoning settings. Bob checks
use an empty workspace, all documented tool groups disabled, one turn and a 0.05
Bobcoin cap. License acceptance remains a user action. HTTP redirects cannot forward keys.

`pytest -q -m "not ltspice and not network" --disable-warnings`: 1177 passed, 4 skipped, 159 deselected.
Ruff check and format passed. Live general-edition checks accepted the saved valid
credential and rejected an intentionally invalid credential. No saved key was found
in either repository or the inspected Bob JSON/log files.

Both final 1.1.9 frozen GUI startup verifiers passed. The earlier Bob signing-policy
block did not recur on this feature build; no Windows security policy or trust store
was changed. Bob Shell 2.0.4 was installed from its checksum-verified IBM package.
Its optional telemetry was disabled using the documented setting. Live Bob inference
is pending the user's acceptance of IBM's license.

Both packaged 1.1.9 executables passed all seven TLV9002 real-LTspice checks;
release ZIP installer bytes matched the tracked root installers and checksums.
The Bob 1.1.9 installer completed with exit 0, and the installed application opened
successfully with the same executable hash as the tested frozen build.

Final general-installation check: Windows Application Control blocked the 1.1.9
Install.exe before launch (error 4551, Code Integrity event 3077). Its frozen app
had passed GUI/simulator checks, but that does not establish installer acceptance.
The tracked installer/instructions/checksum/animation were restored to the last
verified 1.1.8 release. General 1.1.9 source is pushed; a new general binary release
is not published. The installed general application remains 1.1.8. An approved
signing certificate/service is needed for a dependable solution; no trust settings
or Windows security policy were changed.


## 2026-09-20 — minimal local credential storage (1.1.10 source)

- Replaced the credential-vault dependency with current-user DPAPI ciphertext in
  an edition-specific LocalAppData data folder. One selected key is retained.
- Atomic ciphertext-only writes, validation, tampering rejection, environment
  fallback and redacted diagnostics are covered. Settings omit default values.
- `.venv/Scripts/python.exe -m pytest -q -m "not ltspice"`: **1,182 passed, 5 skipped, 158 deselected**.
- After moving data outside installer-owned directories, focused security and
  setup tests: **62 passed** in each edition. Ruff lint and format checks passed.
- Real Windows encryption round trip and a fresh-process read passed. The general
  live key check accepted the encrypted-file credential. Bob's key decrypted, but
  its live check remains blocked by the user's pending IBM license acceptance.
- The 1.1.10 frozen application was blocked at launch with WinError 4551
  (Application Control). No policy was bypassed or changed. The repository keeps
  its last verified 1.1.8 installer; the privacy changes currently require source
  or a future approved binary release. The existing desktop app is still 1.1.8.
