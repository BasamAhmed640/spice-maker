# DECISIONS

Every decision that constrains later work. Format: id, date, decision, rationale,
rejected alternatives, evidence. Append-only; a superseded decision keeps its id
and gains a "Superseded by" line.

---

## D-001 — Python 3.14 pin

**Date:** 2026-09-18
**Decision:** `requires-python = ">=3.14,<3.15"`; `.venv` created with
`uv venv --python 3.14`; build backend hatchling; console script
`boardmodeler = "boardmodeler.cli:main"`.
**Rationale:** the machine has exactly one interpreter (`uv python list` reports
only 3.14.2) and every required wheel resolves for cp314.
**Rejected:** CPython 3.13 (would need an extra download for no benefit).
**Evidence:** `uv sync --all-extras` completed; installed versions observed:
numpy 2.5.2, pydantic 2.13.5, pypdf 6.19.0, pypdfium2 5.13.0, keyring 25.7.x,
PySide6-Essentials 6.11.2, spicelib 1.6.3, pytest 9.1.1, ruff 0.16.8,
pyinstaller 6.22.3, reportlab 5.0.1, psutil 7.2.2.

---

## D-002 — Waveform reader backend

**Date:** 2026-09-18
**Decision:** the native reader (`simulation/raw.py`) is authoritative. `spicelib`
(optional `sim` extra) is selected only when it reproduces the native read of the
smoke `.raw` within 1e-9 relative; otherwise the backend stays `native` and the
reason is reported by `boardmodeler doctor`.
**Rationale:** one reader must own the honesty of every measurement; an optional
library must never silently change what "measured" means.
**Rejected:** always using spicelib (adds a dependency to the critical path and
hides layout changes); never installing it (loses an independent cross-check).
**Evidence:** `simulation/backend.py::probe_backend` runs on the smoke `.raw`
produced by `smoke_test`; `doctor --json` reports `reader_backend`,
`spicelib_version`, `max_deviation` and the compared variable list.

---

## D-003 — First regulator part and model provenance

**Date:** 2026-09-18
**Decision:** the first regulator is the **TPS54320** (Texas Instruments), and the
type-A fixture is its **unencrypted PSpice transient model** (`SLVM451A`),
mechanically ported to LTspice. No fallback to `LT8609S` was needed.

**Rationale / observed evidence:**

|Step|Observation|
|---|---|
|Datasheet|`https://www.ti.com/lit/ds/symlink/tps54320.pdf` → HTTP 200, 1 678 941 bytes, sha256 `480b1cdb92b4668e…` (SLVS982C, 43 pages, embedded text)|
|Model package|`https://www.ti.com/lit/zip/SLVM451` → HTTP 200, 79 758 bytes, sha256 `ae5d9bc8128ea8c4…`; contains `TPS54320_TRANS.lib` (27 372 bytes). The product page lists it as "TPS54320 **Unencrypted** PSpice Transient Model Package (Rev. A)" — unencrypted, so a source-level port is possible at all. The average model (`SLVM896`) was fetched too, for reference.|
|Port|`models/adapt.py` applies 5 mechanical transforms (16 recorded changes) and the ported model **simulates and regulates** in LTspice 26.0.0.3|
|Probe result|start-up settles at **3.1324 V** against the application's 3.2691 V divider target (1 k / 3.24 k, Vref 0.8 V) — inside the 5 % probe criterion|
|Licence|TI model terms: provided "as is" for TI parts. Stored under `fixtures/regulator/tps54320/originals/` (**git-ignored**), never redistributed; the committed artifacts are `provenance.json` (URLs, statuses, sha256, licence note) and `adaptation.json` (the change list)|

**Rejected:** the pre-decided `LT8609S` fallback (not needed, and `analog.com`
timed out from this machine during the fixture probe, so its datasheet was not
obtainable anyway); a hand-written behavioural stand-in for the vendor part (the
type-B template exists separately and is *not* a substitute for vendor evidence).

---

## D-008 — The PSpice → LTspice port recipe

**Date:** 2026-09-18
**Decision:** vendor models are adapted only through
`models/adapt.py::port_pspice_to_ltspice`, which enumerates every change and is
covered by tests. The five transforms, each empirically required by the real
TPS54320 model:

|#|Transform|Why (observed)|
|---|---|---|
|1|fold PSpice `+` continuation lines into logical cards first|collapsing `VALUE { {…} }` before folding left a stray `}` on a continuation line → `Unknown parameter + }`|
|2|`VALUE { {expr} }` → `VALUE={expr}`|PSpice ABM syntax; LTspice wants a braced `VALUE=`|
|3|`VSWITCH(Roff,Ron,Voff,Von)` → `SW(Roff,Ron,Vt,Vh)` with `Vt=(Von+Voff)/2` and **`Vh=|Von-Voff|/2`**|LTspice's `Vh` is the **half**-band. Using `|Von-Voff|` doubled the band, so the model's 1 V internal logic never crossed the threshold: the oscillator never toggled and the converter never switched (observed: `V(ph)` flat, `V(vout)` = 0 V)|
|4|strip the `Vdc` unit suffix|LTspice rejects `0Vdc`|
|5|numerical help: no `.tran … uic`, `method=gear`, `trtol=10`, `gmin/abstol/vntol` relaxed, and a diode emission coefficient raised from `N=0.01` to `N=0.1`|with `uic` LTspice reported `Convergence Failure: Time step too small; initial timepoint: trouble with instance "xu1:D_U8_D12"` and never advanced; solving the operating point instead converges in 0.8 s. `N=0.01` is a PSpice idiom LTspice warns about ("Emission coefficient, N=0.01, too small")|

**Consequences recorded in the artifact:** the adaptation report states that diode
clamping differs from the PSpice original and that decks must not use `uic`; the
capability probes below never claim behaviour beyond what they measured.

**Cost note:** the ported transistor-level model needs ≈30 s of wall time per
simulated millisecond on this machine, so probe windows are scaled
(`ModelProbeSpec.probe_time_scale`) and every probe window is expressed as a
fraction of `tstop`.

---

## D-009 — What a capability status means

**Date:** 2026-09-18
**Decision:** `supported` requires a probe that ran and met its numeric criterion;
`unsupported` means structurally impossible or a declared exclusion; `unknown`
means the probe ran but could not establish the behaviour (the observed
measurement is recorded); `not_tested` means no probe exists, a prerequisite
failed, or the probe could not run. **Every one of these blocks a dependent
requirement** — `behavior_gate` gates on `!= "supported"`, so a "not tested"
behaviour can never produce a PASS.

**Evidence:** `models/capability.py`, `tests/models/test_capability.py`
(a raising probe and a cancelled probe both stay `not_tested`; the gate blocks
`unknown`, `unsupported` and `not_tested` alike).

---

## D-010 — Which model carries which claim

**Date:** 2026-09-18
**Decision:** the **generated type-B template** (`BM_REG_BUCK`/`BM_REG_LDO`) carries
the verified dynamic behaviour (startup, enable, power-good, current limit, the
feedback-divider dependency) and is what the demo board and the dynamic tests
exercise. The **ported vendor model** (type A, D-003) is the vendor-evidence
artifact: it is stored, adapted, probed, and reported through its capability
record, and it is exercised by an opt-in test rather than the default suite.

**Rationale — the observed behaviour of the vendor model on this machine:**

|Observation|Value|
|---|---|
|first isolated start-up probe (1.2 ms window, `tmax=100 ns`, `Css=1 nF`)|converged in 57 s, output 3.1324 V against a 3.2691 V target|
|8-probe capability sweep (2.1 ms windows via `probe_time_scale=0.35`)|389 s total; `shutdown`, `dc_regulation`, `input_current`, `reverse_current_prebias` = supported; `startup` timed out after 300 s (exit 1 then hang); `switching_waveforms` errored (a deck bug, since fixed); `load_transients`/`current_limit_recovery` = unknown (probe could not establish them)|
|Phase 2 application test (2.1 ms window, 10 Ω load)|**hit the 600 s cap** without finishing|

So the model is *usable* but not *reliably fast*: probing it is evidence, running a
multi-millisecond board scenario on it is not. Making the default test suite depend
on it would turn every run into a coin flip on convergence, which is a worse
failure mode than an explicit, recorded limitation.

**Consequences:**

* the capability record for the vendor model is exactly what the probes measured —
  it must not be "improved" by re-running until it passes;
* requirements mapped to an unprobed vendor behaviour are gated to UNKNOWN
  (D-009), so nothing downstream can quietly rely on it;
* `tests/regulator/test_first_regulator.py` documents this split at the top of the
  file, and the broken-divider requirement is verified on the type-B model, whose
  own capability record is probed and exported like any other model.

**Rejected:** deleting the vendor artifact (it is the type-A evidence the plan
asks for, and its `dc_regulation`/`input_current`/`shutdown` probes are real);
loosening the timeouts until it passes (that would hide a machine-dependent
property behind a number chosen to make a test green).

---

**Date:** 2026-09-18
**Decision:** all simulator invocations are
`LTspice.exe -b [-ascii (extra switches...)] <absolute deck path>` with
`cwd = <run directory>`; output discovery is `<stem>.raw`, `<stem>.log`,
`<stem>.op.raw` beside the deck. `-I<path>` is **never** passed. `.step` is
**never** used (one deck per corner instead).

**Rationale (measured on LTspice 26.0.0.3):**

|Observation|Evidence|
|---|---|
|`-b <deck>` exits 0, leaves `<stem>.raw`/`.log`/`.op.raw`/`.db` beside the deck|`tests/ltspice/test_invocation.py::test_run_batch_argv_and_artifacts`|
|`-b <deck>.asc` simulates **directly** (no `-netlist` pre-step)|`test_batch_simulates_asc_directly`|
|a deck with an undefined subcircuit exits 1 and writes the error into `.log` (no dialog)|`test_run_batch_reports_failure_for_bad_deck`|
|`-I<dir>` does not resolve the subcircuit and leaves the process alive on a modal dialog (90 s timeout, repeated 3x at 25 s)|Phase 0 probe, `build/scratch` session log; this is why `run_batch` has no `search_paths` parameter|
|passing `-I` *last* (as the shipped help implies) returns exit 1 with "This sub-circuit name is not defined"; passing it first returns 0 once and then times out — nondeterministic ⇒ unusable|same probe|
|generated `.asy` + `.lib` **beside** the `.asc` resolve with no `-I`, and LTspice auto-emits `.lib <model>` for the symbol's `SpiceModel` attribute|`test_local_symbol_and_model_resolve_without_search_path`|
|`LTspice.exe -version` prints `26.0.0` and exits 0 in 0.2 s|`smoke_test(...).version`|
|`.step` concatenates every step into one `.raw` whose header carries no step index|Phase 0 probe (`No. Points: 3139` for 3 steps, flags `real forward stepped`)|

**Rejected:** `-I` search paths (nondeterministic hang); `.step` (unreadable step
boundaries); waiting forever for process exit (a modal dialog after the log's
`Total elapsed time` line is indistinguishable from a hang — the watchdog kills
the tree we spawned and records `terminated_after_marker`).

**Watchdog:** the process tree of the spawned PID is killed after
`marker_grace_s` (5 s) past the log's `Total elapsed time` line, or at
`timeout_s`. Only the tree of our own PID is touched, never another LTspice
instance the owner may have open.

---

## D-007 — `.raw` payload formats

**Date:** 2026-09-18
**Decision:** the reader detects the payload layout from the file size and the
header, never from an assumption. Supported: header text in UTF-16LE (LTspice 26)
or ASCII; binary payloads with stride
`8 + 4*(nvars-1)` (variable 0 is a double, the rest float32), its 8-byte aligned
variant, `float32`, `float32-align8`, `float64`; and `-ascii` text payloads.
Stepped and complex payloads raise `RawFormatError`.

**Rationale:** LTspice 26 writes a **UTF-16LE** header (no BOM) where older
releases wrote ASCII, and stores non-time variables as **float32**, so a binary
`.raw` cannot support assertions tighter than ~1e-7 relative. ASCII output keeps
~10 significant digits and is therefore *more* precise than the default binary
file. Measurement code must not assume double precision for signals read from a
default `.raw`.

**Rejected:** guessing the layout from the flag word (a wrong guess would silently
produce garbage); zero-filling an unrecognised payload (forbidden).

**Evidence:** `tests/ltspice/test_raw_reader.py`: closed-form RC/divider checks,
ASCII-vs-binary agreement (measured 1.2e-9 for `V(in)`, 2.9e-8 for `V(out)`,
bounded at 1e-7 in the test with the float32 explanation), truncated/complex/
stepped payloads raising, and a synthetic file whose stride is derived from size.

---

## D-005 — HTTP inference endpoint (DeepSeek) and Bob

**Date:** 2026-09-18 (verified against the vendor documentation during Phase 4)
**Decision:** `providers/http_inference.py` is an OpenAI-compatible chat-completions
adapter that ships **no default endpoint and no default model**: both must come from
configuration, so nothing can be invented or silently inherited. The adapter's shape
is modelled on the strings published at `https://api-docs.deepseek.com/`, verified by
fetching that page during this session:

|Parameter|Documented value (observed 2026-09-18)|
|---|---|
|base_url (OpenAI-compatible)|`https://api.deepseek.com`|
|chat path|`/chat/completions` (the published `curl` example posts to `https://api.deepseek.com/chat/completions`)|
|model names|`deepseek-flash`, `deepseek-v4-pro`; the legacy names `deepseek-v4-flash` and `deepseek-v4-flash-vision-exp` are still accepted for retired models|
|auth|`Authorization: Bearer <key>`|

`tests/providers/test_http_inference.py::test_deepseek_documentation_still_names_the_configured_strings`
re-checks that the documentation still publishes `api.deepseek.com` and
`chat/completions`; it is marked `network` **and** requires
`BOARDMODELER_NETWORK_TESTS=1`, so the default suite makes no outbound call.

Note for anyone reading an older draft of this file: the model names
`deepseek-chat` / `deepseek-reasoner` are no longer the documented names, which is
exactly why no default is compiled in.

**Bob.** `providers/bob.py` implements `BobDirectProvider` and `BobShellProvider` but
this machine has no `BOB_*` credentials and no verified approved endpoint, so the
direct path reports `BLOCKED("bob_credentials_unavailable")` and is **never**
substituted by another provider. Bob Shell additionally requires both
`policy.allow_bob_shell` and `--allow-bob-shell`, refuses documents classified
internal/confidential/unknown, and runs through `security.subprocess_guard`. It is
recorded as unexercised rather than as "supported".

---

## D-011 — A scenario must apply the stimulus it declares

**Date:** 2026-09-18
**Decision:** every `ScenarioSpec` in `verification/scenarios.py` that the demo board
runs must have its declared stimulus injected into the deck it is checked with; a
scenario whose stimulus cannot be injected is reported as not applied, with the
reason, instead of being checked under a deck that does not contain it.

**Rationale:** `deck_for_scenario` originally varied the deck only for `fast_rail`
and `slow_rail`, so 21 of the 23 scenarios were checked against the *nominal* deck
under a different name. Every one of those results would have been a claim about a
stimulus that was never applied — the precise failure mode this project exists to
avoid, and one that is invisible in the report because the scenario id looks right.

**Consequences:**

* the built project carries `tests/scenario_stimulus.json` mapping each scenario to
  the exact injected card(s) or to `applied: false` with a note;
* `tests/pipeline/test_demo_decks.py` asserts each scenario's deck differs from the
  nominal deck (or is recorded as not applied) without needing a simulator;
* `tests/test_demo_scenarios.py` asserts the injection is *observable* — a measured
  quantity moves by more than 1 % against nominal — because an injection that changes
  no output is decoration, not a scenario;
* the reference clock is a stand-in whose rate is not asserted by any requirement
  (`REQ_DEMO_CLK_009` is an ASSUMPTION), and each deck says so, because driving a
  100 MHz clock with 1 ns edges across a 10 ms window forced about 1e6 timesteps and
  a 120 MB `.raw` for a property nothing checks.

**Rejected:** keeping the shared deck and documenting the shortcut (it would make the
report's per-scenario statuses unverifiable); shrinking the scenario list to the two
injected ones (the plan fixes the scenario ids, and the ones that matter — clock,
straps, reset, power-good — are exactly the ones that were missing).
