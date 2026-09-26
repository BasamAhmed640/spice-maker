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

## D-006 — Simulator invocation

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
* the reference clock is a stand-in whose rate is not asserted by any requirement
  (`REQ_DEMO_CLK_009` is an ASSUMPTION), and each deck says so, because driving a
  100 MHz clock with 1 ns edges across a 10 ms window forced about 1e6 timesteps and
  a 120 MB `.raw` for a property nothing checks.

**Rejected:** keeping the shared deck and documenting the shortcut (it would make the
report's per-scenario statuses unverifiable); shrinking the scenario list to the two
injected ones (the plan fixes the scenario ids, and the ones that matter — clock,
straps, reset, power-good — are exactly the ones that were missing).

---

## D-012 — The demo board's own defects, and which source owns the timing

**Date:** 2026-09-18
**Context:** wiring the scenario stimuli (D-011) and the fault matrix exposed four
defects in the demonstration itself, each of which had made a claim unfalsifiable.

1. **The reset supervisor had the polarity of a power-good *pin*.** `U3`/`U4` were
   wired as `BM_PG`, whose documented behaviour is to *assert its output when the
   sensed level is good*. A reset output must do the opposite: hold low while power
   is bad and release after it is good. Measured consequence: `PERST#` tracked the
   3V3 rail up at 2.05 ms, before either power-good pin was valid, and was pulled low
   2 ms *after* power was good — the exact inverse of the fixture's own contract.
   Fixed by adding the primitive the circuit actually needs: `BM_RESET_SUP`
   (`pg_in out vdd vss`, `VTH/VHYS/TD`), same shape as `BM_PG` with inverted
   polarity, documented as such in `models/primitives.py`. Measured after the fix:
   PG_1V8 valid at 2.849 ms, `PERST#` released at 5.629 ms — a 2.78 ms hold, inside
   the required 1 ms to 100 ms band.

2. **The deck carried its own copy of the fixture's timing.** `loads` and `timing`
   in `circuit/project.json` declared the load steps and the reset delay, while the
   deck builder used module constants. A mutation of the declared value therefore
   changed nothing the simulator saw — `release_reset_early` was inert, and
   `slow_rail_u2`'s "1V8 overload" never reached the deck. Fixed by making the
   project the single source: the deck reads `timing.load_step_*`, `loads` and
   `timing.pg_delay_s` from the circuit it is building for. The fault matrix now
   detects both mutations.

3. **Two requirements were missing entirely.** Nothing asserted that the switch's
   strap pins are on the nets their levels are read from, and nothing asserted the
   `PERST#` pull-up's domain, so a strap *swap* and a reset pull-up re-referenced to
   the 12 V rail were invisible to every check (the level measurements cannot see
   either: the strap levels are unchanged by swapping which pin reads them). Added
   `STRAP_011` (pin→net for all three straps) and `RESET_012` (the reset line idles
   at the 3V3 level and never above it). Both are dynamic, so they fire on the deck.

4. **A load step inside a converter's soft start is a different experiment.**
   Stepping the 1V8 load at the instant its LDO starts dragged that rail to −20 V,
   and a load step landing inside a requirement's steady window reported the
   fixture's own stimulus as a rail violation. The load steps now land after each
   rail is regulating, and the values live in the fixture.

**Deliberately left failing.** With those fixed, three of the ten scenarios report
FAIL: `staggered_rails` and `load_step` dip the 3V3 rail to 3.126 V/3.130 V against
its declared 3.135 V floor when a load steps, and `brownout_short_interrupt`
collapses during the dip. A fourth, `pullup_missing`, is UNKNOWN: its removal
isolates the sideband node so LTspice drops it from the `.raw` and the level
requirement cannot be evaluated, and it is reported with that reason. These are
reported as findings about the fixture and the reduced models.
Widening the ±5 % window, or omitting the load until the number flips, would be
choosing the answer rather than measuring it — which is the one thing this project
must not do.

**Evidence:** `uv run boardmodeler demo build --out build/demo` → 30 requirements,
10 test cases, 23/23 stimuli applied; `uv run boardmodeler run mutations` → 7/7
detected with the original project byte-identical. The full-suite and
scenario-check results are recorded under "Commands run" in `docs/STATUS.md`.

---

## D-013 — The three circuit FAILs are one model-fidelity finding, not three board faults

**Date:** 2026-09-18
**What was reported:** `staggered_rails`, `load_step` and `brownout_short_interrupt`
FAIL the 3V3 rail-window requirement, and `pullup_missing` is UNKNOWN. A reader
reasonably asks whether the board is broken. It is not, and the measurements below
are why the number is misleading as it stands.

**Measured, on the built demo project with real LTspice:**

|Variant|min V(3V3) in the 4–10 ms window|Floor|
|---|---|---|
|`load_step` baseline (0.5 A step at 6 ms)|2.9107 V|3.135 V|
|the same deck with the extra step removed|**3.2703 V** (in window)|3.135 V|
|the same deck with the step at 0.05 A instead of 0.5 A|**3.2693 V** (in window)|3.135 V|
|buck error-amp gain ×10 (`GM=50` → `GM=500`)|2.9107 V (unchanged)|3.135 V|
|1V8 nominal load removed|2.7026 V (worse)|3.135 V|
|output capacitance ×10|−5.79 V (worse)|3.135 V|
|`BM_LOAD` given a supply-validity gate|2.8921 V|3.135 V|

**Reading:** the excursion appears only in the window that contains a *hard current
step*, scales with the step amplitude, is untouched by loop gain, and gets worse with
more output capacitance. That is the large-signal step response of a **reduced
behavioural model whose compensation is a template constant**, not a demonstrated
rail-margin failure of a real converter (a switching regulator responds within a few
switching cycles; this template has no switching stage at all). The brownout FAIL has
the same shape: a sagging input with a constant-current load against a model whose
protections latch.

**The actual defect this exposes:** the demonstration asks a *transient-margin*
question of a model whose transient response was never established, and the project
already has the right mechanism for that — the capability gate (D-009). It was bypassed
because `build_demo_project` was never given a `workdir`, so **no capability records
were produced for the board's generated models** and nothing could be gated. A
model-limited transient therefore surfaces as a circuit FAIL, which overstates what
was learned in exactly the direction this project exists to avoid.

**Fix (next action, not yet implemented):** probe the board models during the build so
`models/capabilities/*.json` exists, bind the rail-window requirement's transient
behaviour to `load_transients`, and let the gate turn these three results into UNKNOWN
with `model_capability_unsupported` until the template's step response is validated
against a declared criterion. The steady-state window check stays as it is: with the
step removed it passes at 3.2703 V, so it is a real and passing claim.

**Rejected:** widening the ±5 % window, or shrinking the step until it passes — both
choose the answer instead of measuring it. Rejected too: deleting the scenarios; a
scenario that cannot be concluded is exactly what UNKNOWN is for, and the FAIL is
evidence that the gate is missing rather than evidence about the board.

---

## D-014 — Agent-authored models, judged by a frozen datasheet harness (the product pivot)

**Date:** 2026-09-18. **Status:** implemented (`boardmodeler model build|test|install`).

**What the owner actually asked for:** "give it a part number and a datasheet, spin up
agents to make a SPICE model that is thoroughly tested and saved, so I can go into
LTspice and wire it up." The delivered product had drifted: the AI read the datasheet
into requirement rows, but the *model* was a hand-written template library
(`BM_REG_BUCK`/`BM_REG_LDO`) whose parameters were typed by hand, and the "agent"
never authored SPICE. The board demonstration, the circuit checker and the desktop UI
are all outside that ask.

**Decision.** Add one path and make it the front door:

```
boardmodeler model build --part <PN> --subckt <NAME> --requirements <json> \
    --bindings <json> --out <dir> [--iterations N]
```

1. The datasheet's extracted rows become a **frozen spec** (`<workdir>/spec/characteristics.json`):
   requirement id, limit, unit, page, verbatim excerpt, and the probe bound to it.
2. An **agent authors** `<workdir>/model/<SUBCKT>.lib` and `<SUBCKT>.asy`. Nothing else
   the agent writes is used, and the spec directory may not change — `build_model`
   re-hashes it after every turn and stops with `UNKNOWN(spec_tampered)` if it did.
   Tolerance relaxation is therefore impossible by construction, not by instruction.
3. A **deterministic harness** runs one probe deck per bound characteristic through
   real LTspice and compares the measured value to the cited limit. No measurement →
   `UNKNOWN` with a reason. A characteristic no probe can reach keeps its
   `not_testable_reason` on the card.
4. Deliverables: `<SUBCKT>.lib`, a symbol whose `SpiceOrder` bijection is validated,
   `example.cir`, `harness-report.json`, and `MODEL_CARD.md` — every number on the card
   comes from an observed run, and unmeasured rows are listed as declared gaps.

**Agent backend: IBM Bob Shell**, chosen by the owner. Interface verified against IBM's
own documentation on 2026-09-18 (recorded here because D-011 requires endpoint/CLI
strings to come from official docs, never invention):

|Fact|Source|
|---|---|
|Install (Windows)|`powershell -c "irm -Uri https://bob.ibm.com/download/bobshell.ps1 \| iex"`; Node ≥ 24 (`/docs/shell/getting-started/install-and-setup`)|
|Non-interactive run|`bob run [options] [prompt...]`, prompt may be piped (`/docs/shell/getting-started/start-bobshell-non-interactive`)|
|Automation flags|`--format json\|stream-json`, `--max-turns <n>`, `--max-cost <amount>`, `--resume <task-id>\|latest`|
|Result object|`{type:"result", timestamp, status:"success"\|"error", stats:{task_id,total_tokens,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,cache_ratio}}`|
|Auth|`BOB_API_KEY`; a key with Scope **Inference** needs nothing else, a **general** key also needs `--team-id <team-id>` (`/docs/ide/account/api-keys`)|
|Skills|YAML frontmatter (`name`, `description`) + Markdown; repo copy at `skills/ltspice-model-author/SKILL.md` (`/docs/shell/features/skills`)|
|Usage accounting|Bob reports **tokens**; the shell path counts runs in **turns** — never converted silently (D-011)|

**Why a frozen harness rather than "the agent tests itself":** an LLM can write a
plausible `.lib` for any part; what it cannot do is decide whether the result matches
the silicon. The harness is the part of the system that can be trusted, so it owns the
limits, the measurements, and the verdicts, and the agent owns only text. This also
keeps the earlier honesty invariants intact: `PASS` still requires an observed
simulator artifact, and a row that cannot be judged is still `UNKNOWN` with its reason.

**Rejected:** (a) letting the agent edit the spec or tolerances — that is the failure
mode the whole design exists to prevent; (b) judging an agent's model against another
model (vendor or template) as the oracle — a comparison between two models says nothing
about the datasheet; (c) generating the model from a template and calling it
agent-authored — the owner asked for authorship, and the template path remains
available as a *seed* the agent may read, not as the answer.

**Termination (owner's rule, 2026-09-18, revised the same day):** *no wall clock ends a
build.* The first cut used a per-turn timeout and a build deadline; the owner rejected time
limits outright — "I want the agents to run until satisfied". The loop therefore ends on
satisfaction or on the agent stalling, and nothing else:

* `PASS` — the harness passes for every covered characteristic;
* `UNKNOWN` — the agent made no progress for `stall_patience` (2) consecutive turns. A turn
  makes progress when the model bytes changed **and** the failing set differs from the
  previous turn's; a failure that changed shape is still motion. The detail names the turn
  count and the probes still failing, and the result carries every row measured so far;
* `UNKNOWN` — a caller-set `max_iterations` was reached. `None` is the default: no cap;
* `UNKNOWN(cancelled)`.

`turn_timeout_s` defaults to `None` at the loop API (an agent invocation is unbounded
there); the product `api` path (including a Bob API key) applies a finite 600 s per-turn
budget when the caller leaves it unset, and an explicit value overrides that, while a
direct `--backend bob` CLI turn stays unbounded absent an override. This is the tool's own
policy for the product path, not a harness reduction. The probe set, the per-probe timeout
and the number of judged rows are never reduced for speed — that is the half of the old
rule that stands: bound the agent's repetitions, never the harness's diligence.

**Internet reinforcement (same day):** the owner asked that the internet be searched for
supporting material while a model is made. The stage records errata, application notes and
vendor-model caveats, but only text this tool actually retrieved, stored verbatim with its
URL and hash; an agent's claim we could not fetch is kept as `retrieved=false` with the
reason. Nothing in it can change a status — the datasheet rows stay the only oracle — so it
can inform a reader without ever upgrading a model's claims. The search is cancel-aware
(the build's cancellation event reaches the agent turn) and carries its own budget
(`reinforce_timeout_s`, default 45 s, `None` unbounded); when that budget expires the stage
records `unavailable` with the reason and the build continues. This budget applies to the
search only: the author loop stays unbounded. The fetcher refuses any
destination that is not a public internet host (loopback, private, link-local, multicast
or reserved addresses, and any name that resolves to one of those or does not resolve at
all), and re-checks that policy on every redirect hop.

**Cost of the drift, recorded honestly:** the UI (3.4 kloc), the circuit checker and
schematic layer (4.0 kloc), and the board demonstration were built against the earlier
spec and are not part of this path. They are left in place, dormant, rather than
deleted; nothing in the new path imports them.


---

## D-015 — Two builds from one catalog: API-key agents, and "Spice Maker"

**Context.** The owner asked for three things at once: rename the repository to
*Spice Maker*, ship it as a one-click installer like the Claude/ChatGPT/Slack downloaders,
and make the agent take a **raw API key** — Bob's by default, plus the mainstream vendors —
with no login flow anywhere. Then: two repositories, both carrying that work, one of which
accepts only the IBM Bob API.

**Decision — the agent catalog is the single knob.** `src/boardmodeler/agent_providers.py`
holds an ordered tuple of `AgentProvider` entries. Everything else reads it: the setup
page builds its provider row from it, the backend factory resolves a provider from it, and
`doctor` reports one credential per entry. A build that must accept only Bob sets the
`BOB_ONLY` flavor (D-016), which filters `CATALOG` to the one entry; then there is *no*
provider row on the setup page and the key row keeps the label it always had
(`BOB API KEY`). `tests/ui/test_setup_dialog.py` proves the single-entry build behaves that
way in-process, so the promise is tested, not asserted.

**Decision — keys, not logins.** Every provider is reached with a raw API key stored in the
OS keyring under `provider:<name>:api_key` (the repo's existing credential helper), with
`BOARDMODELER_<NAME>_API_KEY` and the vendor's own variable (e.g. `OPENAI_API_KEY`) as
environment fallbacks. Nothing in this path opens a browser, and no key is ever written to
a config file, a project directory, a manifest or a log line.

* **Bob** keeps its documented route: the Bob CLI (Bob Shell), whose own docs state that
  `bob run`/`bob chat` authenticate from `BOB_API_KEY` alone (an Inference-scope key needs
  no `--team-id`; a general key does). The key is passed to the child process through its
  environment and never appears in argv.
* *Bob over plain HTTP was probed and refused, and is therefore not shipped*: on
  2026-09-18 every request to `https://api.us-east.bob.ibm.com/inference/v1/...` from this
  machine answered Cloudflare `403` (bot-management HTML) for `urllib`, `curl` and Bun
  `fetch`, with both `Authorization: Apikey` and `Bearer`. IBM publishes no inference path
  in its docs, only the region host list. A guessed endpoint that the vendor's edge blocks
  is exactly the kind of thing D-005 forbids, so Bob stays on the documented CLI path.
* The HTTP vendors speak their own documented shapes (`openai` chat-completions,
  `anthropic` messages, `google` generateContent). Endpoints and default model ids are the
  vendors' own quickstart values, each entry carrying the doc URL it came from, and the
  model id is editable per machine because those strings drift.

**Decision — what the rename does and does not touch.** The repository is `spice-maker`,
the window/app/installer identity is *Spice Maker* (with a space), and the frozen
executable is `SpiceMaker.exe`. The Python distribution keeps the name `boardmodeler`, the
console script stays `boardmodeler`, and the keyring service (`boardmodeler`), credential
names (`provider:bob_shell:api_key`) and config directory (`%APPDATA%\BoardModeler`) are
unchanged on purpose: renaming them would orphan the API key and settings an existing
install already has, and that is a worse outcome than an internal name that lags the
brand. `docs/STATUS.md` records the state of each surface.

**Rejected.** (a) Deleting the non-Bob provider code in the restricted build — it doubles
the maintenance of a fork whose whole difference is one tuple, and the tests covering the
HTTP wires would not exist there to catch a regression in the shared code. (b) Shipping the
reverse-engineered Bob inference endpoint as a default — unverifiable from this machine and
undocumented by IBM. (c) Making the setup page a multi-page wizard to hold provider,
model and key — the surface stays one content-sized page (`docs/DECISIONS.md` D-014, and the
forbidden-label test in `tests/ui/test_setup_dialog.py`).

**Addendum, same day (second pass).** Four things were settled after the decision above was written.

* **OpenCode Zen / Go is a catalog entry, not a new wire.** `POST
  https://opencode.ai/zen/v1/chat/completions` with `Authorization: Bearer <key>` — verified against
  the live gateway: no key → `401 {"type":"error","error":{"type":"AuthError","message":"Missing API
  key."}}`, a bogus Bearer key → `Invalid API key`, and the same bogus key in `x-api-key` → `Missing API
  key`, so the scheme is Bearer. Only the models Zen serves from `/chat/completions` are reachable
  through this wire; its `/responses` and `/messages` models are not, and the MODEL row in SETUP is
  where that choice lives. This is the "OpenCode Go option" the owner asked the general build to carry;
  the Bob-only build has one catalog entry and therefore no such option.
* **`model build --provider` names the *agent* provider.** It named the extraction provider before.
  Extraction keeps D-011's own walk over `ProviderConfig.provider_order`, and `--requirements` /
  `--bindings` now reach the datasheet path's request too, so a supplied extraction is honoured there
  instead of silently re-extracting with the fixture provider.
* **A reasoning-first model needs its thinking switch named, and then it authors.** Against the real
  23.8 kB prompt, DeepSeek's models spend the entire output budget on `reasoning_content` and return an
  empty `content` when they are left at their default thinking settings (observed three times, at
  12 288 and 32 768 tokens, 57–303 s). The backend reports `response_empty … finish_reason='length'`
  with the truncation named, and the budget is settable (`--max-tokens`, `agent_max_tokens`, default
  32 768). DeepSeek documents two request fields for this, so the catalog now carries them per entry —
  a provider's own documented switch, not a guessed one, and MODEL/BUDGET stay editable per machine:
  `{"thinking": {"type": "enabled"}, "reasoning_effort": "low"}` for the two DeepSeek models. Measured
  on the TPS54320 fixture: thinking **disabled** answers in ~10 s with both files but reaches **0 PASS**
  in three turns (the deck it writes references a sub-model it never defines), while thinking enabled
  at low effort answers in ~1 min and reaches **4 PASS / 0 FAIL** after three turns, the remaining
  UNKNOWNs naming the model's own convergence. The setting that produced the better model ships; Bob
  and the other vendors are unaffected.
* **The author is told what the simulator said, and one malformed reply is re-asked.** The harness
  stops a run that cannot proceed honestly, but "no `.raw` appeared" does not tell an author whether it
  wrote a syntax error or forgot a file. The `unknown_reason` for an unreadable run now carries the
  offending line LTspice printed (`…BM_REG_BUCK.lib(95): Undefined model "rout_drv"`), and that reason
  is exactly what the next turn's prompt quotes as feedback. The API backends likewise re-ask **once**
  inside a turn when the reply is not the required JSON object, quoting the parse error back to the
  model, and — when a reply comes back empty at the model's output limit, which the switch that made it
  answer can still cause — with the entry's own fallback setting (`retry_body`: thinking off for
  DeepSeek) instead of giving up the turn. Every one of these is a repair of the *request*, not a second
  opinion: a retry that also fails is reported with both attempts named, and none of them can manufacture
  a PASS — the harness still judges the file that is on disk.
* **The installer is the application and nothing else.** A Velopack one-click setup with the animated
  pepper splash, Start Menu and desktop shortcuts, and `Update.exe --uninstall --silent`; it carries no
  LTspice, Bob Shell or Python payload (SHA-256 of all 16 606 files under the two raw LTspice trees is
  unchanged across install and uninstall). The freeze excludes the venv's optional weight
  (`spicelib` → scipy/matplotlib, `reportlab` → PIL) and the Qt modules a Widgets application never
  loads: 122.6 MB → 63.1 MB of Setup.exe over a 254 MB → 124 MB payload. The cost is the optional
  `spicelib` reader inside the frozen build, which reports its documented "not installed (optional 'sim'
  extra); using the native reader" — the native reader is authoritative anyway (D-002).
* **Bob stays native.** Bob Shell with `BOB_API_KEY` is the documented consumer of an Inference-scope
  key; the application defaults to that provider, opens no browser, never logs in, and never substitutes
  a vendor when Bob is unavailable. The Bob-only build is the same code with a one-entry catalog.


## D-016 — Shared, measured model iteration and explicit I/O scope (2026-09-19)

The selected author backend also extracts the supplied datasheet. Four tasks share one
schema/document context, with one correction attempt for invalid structure or semantic
classification. Adapter prompt versions participate in extraction-cache keys. The user's
GO action explicitly authorizes sending the selected document; its unknown classification
is not relabeled public. Internal/confidential policy restrictions remain authoritative.

Keep the best measured model and immutable attempt snapshots. Compare unknown rows,
failed rows, then normalized numeric residual; cycling failures is not improvement.
Reuse only matching validation inputs with intact raw/log evidence, remeasurement and
limit comparison. No UNKNOWN cache reuse, no tolerance relaxation, no false PASS.

Operating conditions and pin maps are part of the frozen spec. I/O coverage is electrical
and conditional; separate operating points get separate decks. Temperature-dependent and
high-speed channel/protocol claims need separate data and engines. Imported vendor files
are byte-preserved, caller-attributed sources, with validation explicitly UNKNOWN.

The two repositories share implementation and tests; build_flavor.BOB_ONLY filters the
accepted catalog. Separate Velopack IDs keep installs distinct. Easy-download ZIPs wrap
the unmodified animated installer as Install.exe; LTspice and Bob Shell remain external
prerequisites. Publishing to each repository's main branch is explicitly user-authorized.

Per-row verdicts are derived from each frozen characteristic and the existing observed
measurement; they require no new persisted report field or additional simulation.
The model card and MakeModelResult count rows, including an explicit UNKNOWN for a
missing outcome and separate not-testable rows. HarnessReport and author-loop progress
continue to count simulator cases. The aggregate model verdict and all numeric limits
remain unchanged. A partial measurement cannot promote an unavailable run to PASS.


### Windows installer dependency isolation (2026-09-20)

The release build must resolve native dependencies using the Python environment and
Windows system directories, never arbitrary tools inherited through PATH. Frozen CLI
success does not establish GUI startup: before packaging, launch the normal executable
from outside the source tree, observe its actual responsive Qt window, and close it.
This gate belongs in build.ps1. The Windows workflow invokes that script when dispatched;
its configuration does not establish that a CI packaging run has occurred.


### Installer in GitHub Code archives (2026-09-20)

At the user's explicit request, track the verified installer as root Install.exe, plus
INSTALL.txt and SHA256SUMS.txt. GitHub Code -> Download ZIP must include the executable.
Use an ordinary Git binary, not a pointer or download bootstrapper. build.ps1 refreshes
these files after packaging; release updates must commit them to the appropriate main.


### Remote consent retries and API reasoning (2026-09-20)

An explicit current egress decision may update only remote_inference_allowed on an
otherwise byte-identical DocumentRecord. The default document-store API still refuses
metadata changes. The model pipeline opts in so retrying after the consent dialog works.

OpenCode Go and Zen are separate catalog entries and billing routes, with no automatic
fallback. Both retain the existing credential name. Go requires a client user agent and
stable session header (https://opencode.ai/docs/go/). Highest supported default-model
effort is sent on extraction and repair requests; a token-limit failure may not silently
lower thinking. Models without a documented effort control retain vendor behavior.
Reasoning references: https://api-docs.deepseek.com/guides/thinking_mode/,
https://platform.claude.com/docs/en/build-with-claude/effort,
https://ai.google.dev/gemini-api/docs/thinking,
https://docs.x.ai/developers/model-capabilities/text/reasoning.


## Bob source isolation and maintenance 1.1.3 — 2026-09-20

The Bob edition now owns a catalog and author adapter containing only Bob, with no
unused competing integrations behind a filter. Its UI, tests and docs are explicitly
edition-owned. Synchronization preserves those files. The general catalog retains
11 entries. Both editions include target-part extraction/cache identity and whole-text
redaction before diagnostic excerpts. API JSON schema validity alone does not qualify
a real device; the unresolved LM358 extraction and op-amp coverage remain explicit.


## Elapsed build time (1.1.4)

The GO timer uses a monotonic clock and a GUI event timer, independent of worker
progress signals. It starts for an accepted build, includes cancellation cleanup,
freezes on the terminal result/error, and resets on the next GO. It is not an ETA.
The agent workflow is documented in AGENT_WORKFLOW.md without claiming unmeasured
model accuracy or exposing private reasoning.


## LM358 qualification (1.1.5)

Use a hash-bound reviewed extraction profile for the exact supplied LM358 datasheet, preserving explicit coverage gaps and rechecking citations. Qualify both the instruments and the generated model in LTspice; typical comparisons prevent idealized zero-offset/bias candidates passing maximum limits alone. Add native complex AC decoding and use it consistently when revalidating cached reports. See LM358_VALIDATION.md for scope.

The observed Go response spent all 32,768 tokens on reasoning and ended with finish_reason=length. Maximum effort is preserved; the DeepSeek maximum-effort default is now 131,072 total output tokens, per https://api-docs.deepseek.com/api/create-chat-completion/. Explicit caller/config caps remain honored. Length-truncated text is never accepted as extraction, and is not retried unchanged as malformed JSON.

Truncation recovery uses one larger request only for a documented model ceiling and automatic budgets. It preserves reasoning and provider, aggregates usage, and shares the original turn deadline. Explicit token/cost caps are never increased. The transport regression covers none/low/high/max and rejects truncated repair responses even when they parse as JSON.


## 2026-09-20 — application-owned symbol layout

Every authored model is published with the deterministic rectangle renderer, including
cache hits and command-line publication. Agent drawings are not used for the final
symbol. Datasheet pin directions guide placement; physical package pin numbers never
replace `.subckt` positions. Validation checks each pin's actual order, as well as the
set and bijection. The agent is asked to write only the electrical model, saving the
symbol output tokens. See STANDARD_SYMBOLS.md.


## 2026-09-20 — preserve compound quantity dimensions

Datasheet extraction and frozen characteristics share the validated unit parser.
Prefixes on quotient operands are independent: `ns/V` is `1e-9 s/V`, whereas
`V/ns` is `1e9 V/s`. No reciprocal conversion is implied. Thermal resistance in
Celsius per watt and kelvin per watt uses temperature differences, not absolute
temperature offsets. Unknown units and incompatible dimensions remain rejected.


## 2026-09-20 — independent fixtures and explicit coverage

Use bounded declarative test recipes for diverse physical pin maps; freeze recipes and limits before authoring. Preserve untested quantitative rows as UNKNOWN. Unknown units are retained without conversion and cannot be measured; stress ratings have an explicit class. Cache successful extraction batches; keep observed UNKNOWN feedback in memory for repair, never as a shortcut to PASS. A failed API turn with unchanged bytes stops. Local unambiguous syntax/ground corrections retain original bytes and require a new simulator run. Correct fixture thresholds only from explicit source conditions before freezing a new run.

## D-020 — bounded credential verification

Saving a key retains it in the OS credential store even when a short connection
check is inconclusive. Only an observed inference response earns VERIFIED. Timeouts,
quota, setup and incomplete replies remain UNVERIFIED. HTTP 401/403 are reported as
authentication/permission refusal, without echoing server text. Checks send no user
documents and cannot change the provider, model or reasoning choice. Bob license
acceptance is explicit and is never performed automatically by the application.


## 2026-09-20 — one encrypted local credential per edition

At the user's request, Windows Credential Manager is no longer read or written.
Current-user DPAPI protects a single local credential file in SpiceMakerData or
SpiceMakerBobData under LocalAppData, outside the installer-managed folders.
Saving a provider replaces the prior saved credential. No plaintext fallback,
machine-wide protection, hard-coded encryption key or automatic credential export
is provided. Explicit environment variables still support CLI automation.
Defaults are omitted from settings JSON; model evidence and caches remain separate.
Existing vault entries require explicit user-authorized migration/removal; the app
does not enumerate or delete a user's other saved credentials.

**Superseded by:** D-022 (credentials are a plain local file in the extracted
folder, 2026-09-21).


## 2026-09-20 — scope extraction to the selected datasheet

A model run must pass its registered document ID to extraction. Other documents
left in a shared output folder are excluded before text collection, egress checks,
disclosures and cache keys. The board-project API keeps its explicit all-document
default. Excluding unrelated files must not change their stored originals.
Installer builds must match the application and project version. Windows packaging
can run on GitHub's hosted runner; this does not change local Windows security policy.

## 2026-09-20 - Portable folder is the storage boundary

The user requires installation and all application-owned state under the extracted
GitHub folder. Do not restore global config or keys. First launch requires explicit
setup. The installer creates no global install/shortcut/credential entries. Updates
replace app/ only. DPAPI ciphertext, scratch directories and model exports stay local.
Normalize only unambiguous flat citation page numbers; conflicting page numbers remain
validation errors. Preserve all numeric requirements and reasoning settings.

## 2026-09-20 - Respect stream completion and expose real network activity

HTTP SSE [DONE] ends the response without waiting for connection EOF. The decoder
still rejects missing finish reasons. Progress is per request, carries counts only,
and cannot mix concurrent batches. Metadata section routing leaves full electrical
requirements intact and falls back to all pages when pin headings are unrecognized.


## 2026-09-21: user-selected quick default

The user requested local sanity checks instead of waiting for full simulation verification. GUI quick mode skips independent AI test planning and the full electrical simulation suite, preserves every extracted row, and labels exported models electrically unverified. Full verification remains available explicitly. No numerical tolerance or measured PASS requirement is weakened. Source/CLI full defaults remain backward compatible; the CLI offers --sanity.


## 2026-09-21: bounded load checks and local source repair

Quick mode performs a generic unpowered LTspice load under a five-second deadline
when the simulator is available. Explicit syntax rejection withholds the export;
timeout or unavailable simulation is displayed as inconclusive/unavailable, never
electrical PASS. Full electrical verification remains optional.
Repairing a behavioral source must update current references only in that source's
own subcircuit, preserving unrelated circuits and original evidence. The sanity
receipt version changes so candidates from the earlier repair logic are not reused.


## D-021 — Per-turn authoring budget: a retry needs a viable budget (2026-09-21)

One authoring turn is bounded by one caller budget, but the attempts inside it do
not get whatever is left over. A retry is started only with at least
`MIN_ATTEMPT_S` (30 s) remaining; attempt one is gated only on having time, so a
caller's own small `timeout_s` is honoured as given. A turn that cannot afford a
retry keeps the reason it actually observed — a truncated or unparseable reply —
and budget exhaustion is raised as its own condition (`deadline_exhausted`), never
as `timeout`. The recorded UCC28251 residual was 24.1999 s, and the previous
`timeout_s=max(0.001, remaining)` made a doomed retry read as a model verdict. A
token-ceiling retry is slower than the attempt that already ran, so it needs a real
budget behind it, not a remainder.

Rejected: funding retries from the remainder; reporting a clock failure as a model
outcome; raising `MAX_OUTPUT_TOKENS` without measurement (it stays 32768).
Evidence: `authoring/api_backend.py`; the regression test fails on `HEAD` and
passes on the fix; focused authoring/egress/credential tests 228 passed, 1 skipped.


## D-022 — Credentials are a plain local file inside the extracted folder (2026-09-21)

One selected key is stored per edition as `data/credentials.json` (Bob:
`credentials.bob.json`) through `storage.state_file()`, which refuses any name
that could land outside the copy. No DPAPI, no Credential Manager, no registry, no
user-profile location and no machine-held key; explicit environment variables
remain for automation. **Tradeoff, stated to the user:** this is weaker at rest
than DPAPI — the file is protected only by the folder, so anyone who can read the
folder can read the key. SETUP and every provider entry say "plain local file (not
encrypted)" rather than implying encryption.

Rejected: DPAPI or a credential vault (defect 3 requires no Windows keys ever), a
machine-wide key, and a plaintext fallback that is not disclosed.
Evidence: `security/credentials.py`, `storage.py`,
`tests/security/test_credentials.py`, `tests/test_portable_storage.py`.


## D-023 — LTspice is configured, never searched at startup (2026-09-21)

`locate()`/`locate_outcome()` read only an explicit setting — the saved
configuration, `LTSPICE_EXE`, or the argument the caller passed — and otherwise
report `reason="unset"` without touching an install directory. Well-known install
locations are probed by `discover()` only when the user explicitly asks, which in
the application is SETUP's FIND press. BROWSE takes a hand-picked executable, and
the status text distinguishes the user's search, a by-hand choice and a saved
configuration. `doctor` keeps reporting `reason=unset` on an unconfigured machine.
A test session states its simulator once through `LTSPICE_EXE` (session-scoped
autouse fixture), so no test relies on implicit discovery and none skips on a
false "LTspice is not installed" message.

Rejected: convenience discovery at startup (the old behaviour) and silently falling
through to another installation; both can snoop a machine and invalidate results.
Evidence: `simulation/ltspice.py`, `ui/setup_dialog.py`,
`tests/test_ltspice_explicit_only.py`.


## D-024 — Egress only to the selected vendor's documented host (2026-09-21)

`agent_providers.endpoint_is_vendor()` is the single source of truth and compares
the whole host against the selected catalog entry's documented endpoint — no
suffix, substring or registrable-domain match — so lookalikes such as
`api.deepseek.com.evil.test`, `evilapi.deepseek.com` and `deepseek.com` are all
refused. A provider outside the catalog declares no vendor host and may reach only
loopback; a CLI provider is refused for any URL.
`http_inference.require_vendor_endpoint()` runs before any header, credential
lookup or socket and is never retried (`endpoint_not_vendor`);
`api_backend._post_json` calls it once before the attempt loop, so no authoring
path can reach a non-vendor destination.

Web reinforcement is limited to the part vendor's own hosts, drawn from the
`DocumentRecord` source URL, vendor-io manifests and the catalog's documentation
hosts (a host matches itself and its subdomains). A candidate outside that set is
recorded as `unverified_claim` with a `vendor_refused:` reason and never fetched;
when no candidate is inside, the stage finishes immediately as
`unavailable/no_vendor_source_found` instead of spending its budget and reporting a
generic skip.

Rejected: suffix/substring host matching, retrying a refused destination, and a
generic skip after a budget-bound search.
Evidence: `providers/http_inference.py`, `authoring/api_backend.py`,
`authoring/reinforce.py`; the new tests fail at collection against unmodified
`HEAD` (`cannot import name 'endpoint_is_vendor'`).


## D-025 — BOB_ONLY narrows the catalog; it does not fork behaviour (2026-09-21)

`build_flavor.BOB_ONLY` filters `agent_providers.CATALOG` down to the Bob entry
(it also selects the per-edition application name and credential filename) rather
than special-casing behaviour behind conditionals. One catalog therefore keeps the
endpoint, credential and provider rules identical between editions, and the Bob
build cannot construct a provider it does not ship. Edition-specific files remain
the excluded flavour set that `tools/sync_shared_core.py` deliberately does not
copy; Bob's `api_backend.py` is 83 lines against the general edition's 1032 and is
hand-adapted, not filtered.

Rejected: per-edition copies of catalog logic and `if bob:` branches in shared
code.
Evidence: `agent_providers.py`, `security/credentials.py`, `installer/build.ps1`,
`tools/sync_shared_core.py`.


## D-026 — A stage must not start work its budget cannot fund (2026-09-21)

`authoring/reinforce.py` gave its only agent turn the whole `reinforce_timeout_s`
allowance (`45.0` by default). One candidate-query turn on a max-effort reasoning
model needs minutes, so the budget was *arithmetically* unspendable: every build
paid the full 45 s and received `search_budget_exceeded: the supporting-material
search did not finish within 45 s` — the owner's original log and every run since.
"Give up fast" means declining **before** spending, not reporting an expired budget
afterwards. A new floor, `MIN_AGENT_TURN_S = 90.0`, makes the stage decline
immediately with `search_budget_too_small: … raise reinforce_timeout_s to enable
it`, naming the setting the user can act on; at or above the floor the turn is
attempted. Verified two-sided: at an 89 s budget the stage refuses with **zero**
turns started; at 120 s the guard does not fire and the path proceeds into the
candidate turn. Consequence, stated rather than hidden: because the default `45.0`
is below the floor, the stage skips instantly by default — effectively off until
the budget is raised to at least 90 s.

Same class as D-021 (the per-turn authoring budget): a caller's budget must fund the
work it authorises, and exhaustion must name itself instead of reading as a model
result. Rejected: starting the turn and reporting the expired budget afterwards
(the recorded failure mode, which burned the allowance and returned nothing);
lowering the floor to match the old default (a 45 s turn cannot finish, so that
only restores the defect); a silent skip with no reason.

Evidence: `authoring/reinforce.py` (`MIN_AGENT_TURN_S`, `search_budget_too_small`),
`pipeline/make_model.py` (`reinforce_timeout_s: float | None = 45.0`);
`tests/authoring/test_reinforce.py`
(`test_a_budget_too_small_for_one_turn_gives_up_without_spending_it`, whose stub
backend must not be entered); two-sided manual check recorded in
`docs/STATUS.md` §A5.

## D-027 — Bob is edition-gated out of the general build; shared symbols stay (2026-09-22)

The general edition must contain no path that installs, launches or authenticates a
third-party CLI agent. Bob is therefore removed from `agent_providers.CATALOG` in this
checkout (that file is edition-local and not synced), and every shared symbol that the
Bob-only edition still needs — `BobShellBackend`, `run_bob_shell`, `bob_environment`,
the `allow_bob_shell` policy field, the `bob` backend name, the `BOB_*` provider kinds —
stays in the tree **behind an explicit edition guard** that runs before a key is read,
before argv is built and before any process exists. Deleting them instead would break
`spice-maker-bob`, whose `api_backend.py` and `make_model.py` construct `BobShellBackend`
directly.

Rejected: deleting the shared symbols (breaks the sibling edition and the one-source
rule that `tools/sync_shared_core.py` exists to protect); adding the shared files to the
sync exclude list so they drift (they are the same files the Bob edition compiles).

Evidence: `build_flavor.BOB_ONLY` gates in `authoring/backends.py` (constructor guard),
`cli.py` (`--backend` choices, `--team-id` registration, the `bob` branch),
`pipeline/make_model.py` (`bob_backend_unavailable` before construction),
`security/key_verification.py`, `ui/settings.py`, `ui/main_window.py`,
`providers/registry.py`; `tests/test_edition_bob_absent.py` (11 tests: no catalog entry,
no default, the constructor raises before `subprocess` is reachable, `doctor --json`
lists no Bob row, `--backend bob` exits non-zero without a traceback, the setup page
offers no Bob row); `uv run python -c "…" doctor --json` → 10 providers, zero Bob lines.

## D-028 — One switch governs egress, and off means nothing is sent (2026-09-22)

`AppConfig.internet_access` is the single source of truth for outbound access, and SETUP
exposes exactly one control for it (`INTERNET ACCESS`). It is deliberately the only
network control on that page: the old `reinforce_check` / `allow_remote` /
`allow_bob_shell` spread meant a user could believe they had cut egress while a stage
still reached the network. With the switch off, `security/network.require_network`
refuses with `internet_access_off` before any request is built, and
`BOARDMODELER_NO_NETWORK` forces it off regardless of the file so a test or a cautious
user can pin it. `full_verification` moved to the build window because it is a per-build
choice, not a network policy — the repository's own rule puts per-build choices in the
window.

Rejected: a second "allow web search" box (that is the sprawl this replaces); making the
switch default off (a fresh copy could not build a model at all, and the switch is a user
control, not the sandbox — the sandbox is D-029 and the vendor-host pinning of D-024).

Evidence: `config.py` (`internet_access`, legacy `web_reinforcement` accepted on load),
`security/network.py`, `ui/setup_dialog.py` (one checkbox), `ui/model_maker.py`
(`FULL VERIFICATION` beside GO), `tests/security/test_network_off.py` (socket tripwire:
zero connection attempts, refusal names the switch), `tests/ui/test_setup_dialog.py`.

## D-029 — One execution policy, and a child environment is an allowlist (2026-09-22)

Every child process this application starts passes `security/execution.py` before it
exists: an absolute executable from a name allowlist, no shell form possible, an argv
shape that keeps every value out of a flag position, a cwd pinned inside a declared root,
a mandatory positive timeout, a bounded capture, and an environment built from an allowlist
with credential-shaped names refused even if an allowlist edit adds one. Callers that must
own their process loop (the simulator's marker/cancel watchdog, the worker's streamed
progress) call `validate()` and spawn the resolved values, and are named in an allowlist
that a meta-test asserts is neither incomplete nor stale.

The environment rule is the part that answers the owner's "never touch my keys":
LTspice is spawned with fifteen OS variables and a private `TEMP` inside the copy, so the
agent API key in the parent environment is not visible to the simulator or to anything it
spawns. Where a child legitimately needs more (the worker is this program's own
interpreter and runs the inference), the extra names are enumerated — `LTSPICE_EXE`, the
`BOARDMODELER_*` family and the selected provider's documented aliases — and a test proves
an unrelated secret such as `AWS_SECRET_ACCESS_KEY` does not reach it.

Rejected: reusing the guard's `run_guarded` for everything (it buffers unbounded output
through `communicate()`, which is the failure mode the cap exists to prevent); leaving the
three UI spawn sites unmanaged (they now go through the policy, or through
`QDesktopServices` with no child process at all).

Evidence: `src/boardmodeler/security/execution.py`, `tests/security/test_execution.py`
(refusals prove no spawn happens, a real timeout kills the tree, a 16 MiB flood is reported
`truncated`, and the AST meta-test fails on any unallowlisted spawn site in `src/`),
`simulation/ltspice.child_environment` + `tests/ltspice/test_child_environment.py`
(including a source guard that the simulator's spawn keeps `env=`).

## D-030 — The simulator boundary is the process environment, not a private ini (2026-09-22)

Compartmentalising LTspice's *settings* was measured and refused. On LTspice 26.0.0 with
`-ini <path>` (missing file, minimal file, and a copy of the user's own `LTspice.ini`),
the process opens its GUI main window — observed through `EnumWindows` as
`LTspice - [<ini stem>]` — and never exits, so a batch build hangs until the watchdog
kills it. Redirecting `APPDATA` to a fresh folder behaves the same way. A *seeded*
private settings file does run (593 ms warm, user's file byte-identical), but seeding
means importing the user's settings, which is the opposite of compartmentalisation. The
same measured shape is why `-I<path>` stays refused (D-006). The boundary is therefore
the process: an allowlisted environment and a rewritten `TEMP` (D-029), with the
simulator's own settings left where its vendor puts them.

Rejected: shipping an `-ini` profile (hangs the build); seeding from `%APPDATA%`
(imports the user's settings and still couples the copy to their profile); a timeout that
tolerates the GUI (a build that shows a window is not a batch build, and the log marker it
waits for never appears).

Evidence: `simulation/ltspice.py` module docstring (measured variants and the observation
method), `docs/STATUS.md` §B2; the smoke test still passes with the scrubbed environment
(V(out)@1 ms = 0.632119 V, deviation 0.019%, tolerance ±2%).

## D-031 — A finished model is reopened from disk, not remembered in a session (2026-09-22)

The window could re-run verification only for the model it had just built, so closing the
app lost the ability to verify a published model. `pipeline/make_model.model_summary`
reads the model directory that exists on disk (library, symbol, card, `results.json`,
spec and manifest) and refuses with `not_a_model_directory` rather than guessing;
`model open --out DIR [--verify]` and the window's `OPEN MODEL…` expose it, and
`--verify` runs the same path `model test` runs. Nothing is reported as verified unless
the verification actually ran.

Rejected: an in-memory session list (the failure mode is a restart); a second verification
implementation for reopened models (two answers to one question).

Evidence: `tests/e2e/test_reopen_model.py` (build offline, drop in-process state, reopen
from disk, re-verify); manual check on a model built in a previous session —
`model open --out build/datasheet-suite/ucc28251 --json` reports `UCC28251`, status
`UNKNOWN`, counts PASS 34 / FAIL 6 / UNKNOWN 84 / NOT_APPLICABLE 174.

## D-032 — Settings-as-data lives in a Qt-free module (2026-09-22)

`boardmodeler setup --json` is the documented way to read the resolved settings, and the
`.venv` the installer builds is deliberately Qt-free (size; `installer/vendor_env.py`).
The description functions therefore live in `settings_summary.py`, which imports no GUI
toolkit, and the dialog module re-exports them so there is one implementation. The config
path is read through the `boardmodeler.config` module at call time rather than bound at
import time, because callers and tests redirect it.

Rejected: printing a second, hand-rolled payload from the CLI (two sources of truth for
the same report); importing the dialog module and tolerating the failure (the shipped copy
must be able to answer this command).

Evidence: `src/boardmodeler/settings_summary.py`, `ui/setup_dialog.py`,
`cli.py` (`setup` branch); proved with PySide6 made unimportable — `setup --json` prints the
settings and exits 0 while `PySide6` never enters `sys.modules`.

## D-033 — Explicit simulator choice and full verification default (2026-09-24)

The app resolves LTspice only from the executable chosen in this copy's SETUP or
an explicit simulator argument. It never interprets an inherited `LTSPICE_EXE` as a
selection, searches installation directories, or probes a default library folder.
The BROWSE picker remains a user-initiated way to choose a file. The GUI opens with
FULL VERIFICATION selected; quick mode is an explicit structural draft whose
electrical status remains UNKNOWN. This supersedes the quick-default and optional
discovery decisions recorded above for older builds.

Model authoring in the general edition uses HTTPS text replies without command or
filesystem tools. The application writes candidate files and runs its own bounded
LTspice batch harness, then compares measured artifacts with frozen datasheet rows.
Its simulator child receives an allowlisted environment without the provider key.

Evidence: `simulation/ltspice.py`, `ui/setup_dialog.py`, `ui/model_maker.py`,
`config.py`, `tests/test_ltspice_explicit_only.py`, and
`tests/test_cli_doctor.py`.

## D-034 — Convergence before accuracy; repair names lines, not whole models (2026-09-24)

The TPS54332DDA build of 2026-09-24 spent two author turns (868 s, 182k tokens) on
models LTspice could not use: turn 1 read the internal node `en_ok` as a bare name
(LTspice: "No such parameter defined"), turn 2 held its PWM latch on a node whose only
path to ground was 1 TΩ and whose driving source read its own output ("trouble with
node en" after every operating-point method failed). The feedback the author received
named the failing probes, never the lines that caused them.

`authoring/convergence.py` now (1) lints every candidate for bare node names in
expressions, undefined identifiers, self-reading behavioural sources, nodes without a
≤1 GΩ DC path, and datasheet-floatable pins (for example EN, "float to enable") that
the model does not bias itself; (2) reads the LTspice log of the model that just ran
and quotes the rejected or non-convergent lines. The loop appends both as a targeted
repair section. The one repair made without the author is syntactic — a bare node
name inside a B-source expression becomes `V(node,GND)`, with the original bytes
archived under `evidence/bare-node-references/` — because it cannot change what the
model means. Nothing here edits limits, fixtures or verdicts: the harness is still the
only source of PASS or FAIL, and a floating EN in a fixture is treated as valid input.

The author prompt is bounded: testable rows in full (fixture JSON without the
planner's prose), at most 20 not-testable rows as one line each, pin prose clipped.
The same spec produced an 82 KB prompt before and 41 KB after. Reasoning effort is
chosen per stage where the provider documents the switch: `low` for datasheet
transcription, `high` for test planning and model authoring (it was `max` for all).

## D-035 — One shared core, enforced by a manifest (2026-09-24)

`shared_core.json` lists the 41 modules under `authoring/`, `documents/`,
`requirements/`, `simulation/` and `verification/` that must be byte-identical in
Spice Maker and Spice Maker Bob; `tests/test_shared_core.py` fails on drift and
`tools/shared_core.py --compare <sibling>` checks both checkouts. Provider wiring, the
author loop glue, OCR and simulator launch stay edition-specific. Bob keeps its
`.bob/` rules, `.bobignore` and tool-free Bob Shell; nothing in the core gives an
agent a shell or file tools.

## D-036 — Extraction reads the pages that carry specifications (2026-09-24)

`documents/relevance.py` scores each page (specification/pin/thermal headings,
number-with-unit density; mechanical, packaging, revision and notice pages score
negative) and extraction sends only the selected pages. Every page is accounted for in
`evidence/page-selection.json`: selected with its signals, skipped with a reason, or a
named gap. A page with no text layer goes to OCR when an engine is available and is
otherwise an explicit `extract_page_gap` — never silently dropped. A provider reply cut
off mid-JSON is refused as `extraction_response_truncated` before it can be cached.

## D-037 — A second PDF reader, not a stopped build (2026-09-24)

pypdf 6.19.0 raised `NameError: name '_LENGTH_LIMIT' is not defined` inside
`NumberObject.read_from_stream` while registering a readable datasheet (the LM358 PDF,
1 of 7 runs with a provider key in the environment, 0 of 7 without; the error names a class
attribute that exists, so it is not a property of the file). That one fault stopped the
whole build at "read". `documents/pdf.read_pdf` now reads the same inventory — page text,
raster image counts, `/Info` metadata — through pdfium when pypdf raises anything, and
raises pypdf's own error only when pdfium cannot read the file either. Page labels stay
undeclared (`{}`) on the pdfium path, because a label is reported only when the document's
own tree was read.

## D-038 — One gate fixture, then the rest in parallel (2026-09-24)

The harness runs the first fixture alone. A candidate that times out or does not converge
there is still sent back for repair with every other fixture deferred (UNKNOWN), as before.
Once the gate simulates, the remaining fixtures run concurrently
(`BOARDMODELER_HARNESS_WORKERS`, default `min(4, cores)`, `1` restores serial runs), and a
later timeout or convergence failure marks only its own rows UNKNOWN. Before, one 120 s
timeout deferred every remaining fixture — 8 TPS54332DDA rows in one recorded turn.

## D-039 — Readiness is visible before GO (2026-09-24)

The build window shows six lights — API KEY, MODEL (Bob edition: BOB SHELL), LTSPICE, PDF,
OCR, INTERNET — and a VERIFY KEY & TOOLS button. The lights open from local state only
(nothing is sent). VERIFY sends one request in exactly the shape a build sends (endpoint,
model id, reasoning switch, the app's User-Agent) and passes MODEL only when the reply parses
as the generator's `{"files": ...}` object; it then runs the LTspice RC smoke circuit. In the
Bob edition it first reads `bob run --help` offline and turns BOB SHELL red when a flag every
build passes is missing, then runs Bob Shell once with every tool group disabled. No key,
request or response text is ever put in a light's detail. Found while building it: without
the app's User-Agent the OpenCode endpoint answered HTTP 403, which reads as a rejected key.

## D-040 — Symbols follow the schematic convention (2026-09-24)

Generated `.asy` symbols place positive supplies on top, grounds, negative supplies and
exposed pads at the bottom, inputs and controls on the left, and outputs plus the
feedback/compensation network on the right. Numbered channels are grouped: an op-amp channel
reads IN+, OUT, IN− with the output between its own inputs. The old two-column heuristic
matched the hint "a" inside any name, which put POWERPAD among the inputs, VIN at the
bottom-left and VEE among the outputs. Geometry only: `SpiceOrder` still follows the
`.subckt` declaration, checked by `validate_symbol` and by a real LTspice netlist test.
`tools/render_symbol.py` draws an `.asy` the way LTspice places pin names, for review.

## D-041 — Seed recognized buck converters before author repair (2026-09-25)

A cited, buck-specific requirements set and the BOOT/VIN/EN/SS/VSENSE/COMP/GND/PH
pin family can select a deterministic peak-current buck template. The seed records
which values come from the datasheet and which remain template defaults, then goes
through the same LTspice product harness as any authored model. Seeding needs no
provider request. Any later author repair is bounded by measured feedback; an
unverified or partly failing seed remains UNKNOWN rather than being promoted to
PASS. The Bob edition retains its tool-free Bob Shell boundary and has no fallback
to a general-edition provider.

## D-042 — Sanity-check planned circuits before freezing (2026-09-25)

The planner checks a proposed buck fixture's external catch-diode direction,
output capacitor, compensation path and soft-start timing against the cited
device values before the fixture is frozen. A COMP shunt that cannot reach the
control threshold with the cited error-amplifier current, or a steady-state
window that ends before soft start, is refused with a concrete reason. This
does not change the limits or retroactively rewrite already frozen fixtures.
Current magnitudes are compared by absolute value when the cited characteristic
does not specify polarity; the signed simulator measurement is retained, and
explicitly cited direction or negative bounds still use signed comparison.
Switching frequency is measured from consecutive edges, including legacy plans
that use the same signal for trigger and measurement.

## D-043 — Keep template evidence scoped to measured rows (2026-09-25)

The TPS54332DDA seed measured 12 PASS, 4 FAIL and 3 UNKNOWN against 19 frozen
rows; the offline product builds in both editions ended UNKNOWN with the same
counts. A separate official TPS54331 datasheet produced a cited three-row
SpecSet that measured 3 PASS, 0 FAIL and 0 UNKNOWN in LTspice. The TPS54331
datasheet's 3.5 A current-limit figure is a minimum and 5.8 A is typical, so
the template's current-limit default was not presented as a cited maximum or
included in those three measured rows. These results demonstrate the bounded
seed path, not a verified full-device model or a completed release gate.

## D-044 — Treat unreadable Internet settings as off (2026-09-25)

Both editions now refuse provider and supporting-material requests when the
single Internet setting cannot be read. A damaged settings file cannot turn a
previously saved off choice into permission to send a request. The refusal
names the SETUP switch, environment override and invalid settings as possible
causes. Focused tests cover the unreadable-config path and the explicitly
enabled supporting-material path while network access is off.

## D-045 — Logic gates reference their own ground; a bench must touch node 0 (2026-09-25)

LTspice ignores an unused A-device input only when it sits on that gate's own common
(8th) node; on any other node it counts as logic low. The buck template tied unused inputs
to global `0` while each gate's common was the model's `GND` pin, and the fixture loader
had renamed a bench's only ground to `bm_fixture_ground`, which nothing tied to node 0. The
saved current-limit bench therefore never switched (1.21198e-9 A against 4.2 A). Now: the
template ties unused inputs to its `GND`; lint `a_device_input_ground` flags any model that
does otherwise; `GND` is renamed only when the bench also names node 0 (a real ground-
current sense); a bench with no connection to node 0 is refused as `fixture_floating_ground`.
Proof: `docs/evidence/2026-09-25-buck-slice/ground-proof/`.

## D-046 — Buck rules see through sense elements and pin aliases (2026-09-25)

The pre-freeze buck rules found the power stage only through an inductor on a pin literally
named PH. A 0.02 Ω PH-to-inductor sense resistor, or pins named SW/FB/AGND, made every rule
— soft-start timing, COMP shunt, catch diode — silently not apply. PH is now traced through
current-sense elements (≤ 1 Ω, 0 V sources) and terminal names are mapped through the
template's aliases first. Limits are unchanged; the saved 0.5 ms current-limit window is
rejected because cited SS charging needs 3.86 ms.

## D-047 — Template parameters come from what a row says, not its id (2026-09-25)

A fresh extraction names rows generically (`B002_REQ_016`), so the suffix-only mapping gave
the saved GUI build 23 template defaults and 0 cited values — a second buck would silently
have carried TPS54332 numbers. Each contract parameter now also has statement + unit
sources (suffix sources stay first for older frozen specs), a cited typical current limit
is preferred to a bounds midpoint, and the contract states pin-role aliases, ground-tie
pins and supported/unsupported behaviours. `authoring/buck_fixtures.py` builds the
current-limit bench deterministically from the matched pins and cited rows; it must pass
the same pre-freeze rules as an AI-planned bench. It is proven on TPS54332DDA and TPS54331
but not yet used by `bind()` to skip planning.

## D-048 — Model the card-level behavior the user needs to check (2026-09-25)

Spice Maker's target is a system-level LTspice sanity check for an I/O card, not an attempt
to reproduce every datasheet row. A useful model must expose wrong wiring, pin-rule
violations and implausible board behavior. The previous AI-written, row-by-row path has
not delivered a verified functional model; its results remain historical evidence and
are not promoted by this decision. The board-level power-up and fault checks return as
part of the system-model acceptance path, including an untied AGND/DGND pair and an
oscillator overloaded by five clock inputs.

For the exact package, every physical pin number and name must appear, and the `.subckt`
port order must match the symbol's `SpiceOrder`. A pinout may be published only after the
user confirms it or two independent sources agree. The explicit pinout-confirmation gate
also applies before a model receives `system-verified` status. Internal pin-to-pin ties
are forbidden unless a cited datasheet page states that the device makes that connection;
an external PCB connection must stay visible as a required-connection rule. In particular,
the TPS54332 POWERPAD must not be silently tied to GND inside its model.
A high-value leakage resistor may aid numerical convergence, but it cannot act as
a functional pin tie or make a missing PCB connection pass its required-connection check.

The must-be-right measurements are VREF; UVLO rise and fall; EN thresholds; soft-start
time; PG thresholds and delay; switching frequency; quiescent and shutdown current;
current limit at both minimum and maximum corners; and current-sense gain in A/V. Gain
is a measured slope over at least two COMP points above the pulse-skip threshold.
These values must meet cited datasheet minimum/maximum bounds, or ±10% when the only
cited value is typical. A failed or unmeasured requirement cannot become PASS by changing
its limit or test circuit.

Output ripple, switch-node and digital-output edges, load-step dip and recovery, and
startup shape are ballpark checks. Measured magnitudes and times must be within 0.5–2×
of the best available reference: first a vendor model, then a datasheet typical-application
figure, then a textbook estimate using the actual test-circuit parts. Edges must not be
ideal, switching ripple must not be zero, ringing must decay, and startup must rise
steadily unless the reference overshoots. A reference or signal that cannot be measured
leaves that check UNKNOWN rather than granting a pass.

Switching-regulator templates must provide `SW` and `AVG` modes with identical pins and
parameters. `SW` supplies ripple, edge and transient checks; `AVG` supports long
power-up and fault sweeps. Must-be-right checks run in both modes. Ripple and edge
checks on `AVG` return UNKNOWN, never a silent zero or an inferred PASS.

The planned generic path must give every IC a pin model from `PinDefinition` records
and existing primitives,
even without a family template. Its alarms cover absolute maximum ratings, required
connections, floating inputs, power through I/O while the supply is off, and overloaded
outputs. Each alarm needs a fault-injection test that fires and a clean-circuit test
that stays quiet. A generic pin model does not imply verified internal functional
behavior.

In the planned default path, code will build the model from templates or pin primitives
and build its fixed test checklist. AI may read and extract datasheet evidence; it will
not plan tests, write SPICE or repair candidates in that path. The earlier AI-authored
path must remain available behind an explicit flag. `system-verified` will be a stricter
status reached only after the pinout gate and real LTspice measurements support the
applicable claims; otherwise preserve FAIL, UNKNOWN and their reasons. Per-model time
is measured against a 5–10 minute goal without weakening these gates.

## D-049 — Verify fixed buck benches from raw cited rows (2026-09-25)

The M2 bench builder accepts the frozen characteristics only alongside the
matching raw requirement record. It checks `citation_verified`, document ID,
page, excerpt, and SI-normalized value before selecting the VREF, frequency,
gain, pulse-skip, current-limit, or SS-charge row. A page number in a derived
spec alone cannot establish a verified citation. Gain fits use distinct active
COMP points; a scalar current-sense-gain probe is an explicit coverage gap.
Current-limit benches use a resistive overload after calculated SS charging and
settling. Because the TPS54332 SS current is typical only, that calculation is
not a guaranteed latest silicon start time. The two current-limit corners are
subcircuit instance-parameter checks, not claims about silicon process corners.

Generated decks alone confer no electrical verdict. The evaluator records
measured metrics or UNKNOWN, preserving the M1 ripple failure and unresolved PH
edge. The LTspice executable is passed explicitly and the code-built runner
keeps artifacts inside ignored `runs/`; raw waveform files are hashed before
local removal. Evidence: `docs/evidence/2026-09-25-system-models-m2/REPORT.md`.

## D-050 — Keep required PCB ties external and cite each part (2026-09-25)

A template may expose an additional ground or pad terminal, but it may not
silently satisfy that terminal's board connection with an internal low-ohm
element. The TPS54332 PowerPAD remains distinct from GND; its 1 GΩ internal
resistor exists only for numerical convergence. A card-level static check uses
the verified raw `B001_PIN_POWERPAD` citation, physical pins 9 and 7, and the
declared/built netlist to require the actual PCB tie. The generic buck contract
does not reuse the TPS-specific citation for another part.

An internal `chk_powerpad` node supports a synthetic clean/open diagnostic.
The 0.1 V threshold and external 1 nA test injection are test equipment, not
TI limits; the ordinary model injects no diagnostic current. Both clean and
fault runs must yield LTspice raw data before an alarm is called measured.
This slice does not complete the full Card A `GND-02` suite case. Frozen
TPS54332 regressions check input hashes and spec digest as well as row IDs, so
changed limits cannot appear to be an unchanged result. Evidence:
`docs/evidence/2026-09-25-system-models-m3/REPORT.md`.
