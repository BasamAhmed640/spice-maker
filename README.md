# Spice Maker

**1.4.0: a model build no longer fails on its own clock.** A full-mode build used to fund its retries from what was left of one turn deadline, so a slow reasoning-class model left its own retry seconds to run in and that clock failure was published as the model's verdict. A retry now requires a viable budget, budget exhaustion is its own reported condition, and the supporting-material search declines immediately rather than spending an allowance it cannot use. Measured on the same datasheet and provider: cold 2828.7 s with 4 PASS rows, warm 1559.1 s with 34 PASS rows, 4 extraction cache hits, the model published both times. The published status is still `UNKNOWN` — the honest label for a partly verified model, because 34 simulator-observed PASS rows out of the testable set is not measured accuracy. [Defect remediation](docs/STATUS.md).

The rest of 1.4.0 is containment and usability. Credentials are a plain local file in this folder (`data/credentials.json`) with no DPAPI, no registry entry and no Credential Manager entry — **weaker at rest than what it replaced**, because anyone who can read the folder can read the key. LTspice is configured, never searched: first launch does not probe install locations, and discovery runs only on SETUP's FIND or `doctor --find-ltspice`. Outbound inference is pinned to the selected provider's documented host and refused before any request is sent, and the supporting-material search reads only the part vendor's sites and gives up immediately with a stated reason. The model window and SETUP are resizable, the doctor report is a scrollable view showing the whole report (it used to reach only its last 4000 characters), and an hourglass beside the timer animates only while a build runs. The installer provisions a real in-folder `.venv` from vendored wheels with no network access at install time, writes `Start.cmd`, `Boardmodeler.cmd` and a folder-local shortcut, and leaves an existing copy of the app untouched; that `.venv` has no Qt, so window-opening commands (`ui`, `setup`) go through `app\SpiceMaker.exe`. [Storage and fresh-start instructions](docs/PORTABLE_STORAGE.md).

**1.3.0: quick structural checks are now the GUI default.** Quick mode skips AI test-circuit planning and uses structural checks plus a five-second unpowered LTspice load when available. Electrical accuracy remains explicitly unverified. Enable **Full simulation verification (slower)** in SETUP, or use **Run full verification** after a quick build. [Modes and limitations](docs/QUICK_MODE.md).

**1.2.1 fixes streamed API completion and shows received-data progress.** [API fix and measured checks](docs/API_STREAM_PROGRESS.md).

**This build is portable.** Extract the GitHub **Code > Download ZIP** archive and run
**Install.exe** inside it. The animated installer puts the app in `app/` in that same
folder. Open `Start.cmd` next time. First launch asks you to choose LTspice and a model
folder inside this extracted folder, and enter your key. It never restores settings
or keys from an older installation. [Storage and fresh-start instructions](docs/PORTABLE_STORAGE.md).



**SAVE & CHECK KEY** now verifies a newly saved key in the background, with a 15-second wait and clear verified/rejected/unverified results. [Credential safety and check details](docs/API_KEY_CHECK.md).

Datasheet extraction and model verification now recover smaller requests, preserve failed-run feedback, and report untested numeric requirements honestly. See [coverage and reliability](docs/DATASHEET_ROBUSTNESS.md).

IC symbols now use a consistent local layout with verified model pin order. See
[standard symbols](docs/STANDARD_SYMBOLS.md).

LM358 now has reviewed datasheet extraction and real dual-amplifier checks. See
[measured coverage and limitations](docs/LM358_VALIDATION.md).

GO now shows a continuously updating **ELAPSED HH:MM:SS** clock, preserving the
final duration. See [what the agents and simulator do](docs/AGENT_WORKFLOW.md).

**Install on Windows:** click **Code → Download ZIP** with the **main** branch
selected. Extract the ZIP, open the extracted repository folder, and double-click
**Install.exe** beside this README. The installer is included in the ZIP.

The tracked **Install.exe** is still stamped 1.3.0 — it fixes the QtWidgets startup crash
and keeps the animated pepper setup — while the source tree is 1.4.0. A 1.4.0 installer
rebuild is pending, so the installer in this ZIP is the verified 1.3.0 build. Python is
bundled; LTspice and, when using IBM Bob, Bob Shell are separate prerequisites. See INSTALL.txt for instructions and SHA256SUMS.txt for the installer hash.

**Give it a datasheet and a part number; an agent authors an LTspice model. The GUI now defaults to local structural checks. Full simulation verification is optional. You get a `.lib`, a symbol and a card that states what was checked and what remains unverified.** That is the product. Everything below the fold is
supporting machinery, and the board/circuit/UI layers date from an earlier, wider spec.

```powershell
uv sync --all-extras
uv run boardmodeler doctor            # checks the configured LTspice with a real smoke test
uv run boardmodeler model build `
    --part TPS54320 --subckt TPS54320 `
    --requirements fixtures/regulator/tps54320/requirements.json `
    --bindings     fixtures/regulator/tps54320/probes.json `
    --out build/tps54320
uv run boardmodeler model test --out build/tps54320     # re-judge any time
uv run boardmodeler model install --out build/tps54320 --user-lib --apply
```

**Before the first run:** open **SETUP** in the window and paste an agent API key — that is
the whole authentication story; there is no login anywhere in this application. **IBM Bob** is
the default provider and needs Bob Shell installed
(`powershell -c "irm -Uri https://bob.ibm.com/download/bobshell.ps1 | iex"`, Node ≥ 24) plus a key from
bob.ibm.com → API keys with **Scope = Inference** (an *Inference* key needs no team id; a
*general* key does). The same row also takes a plain vendor key from OpenAI, Anthropic, Google,
DeepSeek, OpenRouter, xAI, Groq, Mistral or OpenCode Zen or OpenCode Go, and those run over HTTP with no
CLI and no extra install. Only the selected key is kept, as a plain local file in this folder
(`data/credentials.json`) — never in a config file, a manifest or a log line. Nothing is bound to
Windows and the file is **not encrypted**: anyone who can read this folder can read the key, so keep
the copy private. Setup is one page and holds only what persists: the LTspice
path (with a smoke test), the agent provider and key, the model id when the provider takes one,
the folder finished models go to, the read-only LTspice user library path, and the
web-reinforcement switch. Installing a finished model is always the window's **Install into
LTspice** action, which copies into this folder's `library/`; add its `sym` and `sub` folders to LTspice's search paths once. The main window holds nothing but the
part number, the datasheet, the save location, **GO** and the progress detail. Without an agent
the run stops immediately with `BLOCKED` naming what is missing — it never substitutes another
provider. The selected key now handles extraction and authoring. Four extraction tasks
share one request, repairs receive the current model, and verified repeat builds reuse
hashed simulator evidence without more author turns. The TPS54320 fixture has 38 rows:
9 bind to 8 regulator probes and 29 remain explicitly untested.

The new electrical I/O probes cover output levels, leakage and transitions at recorded
operating points. Vendor IBIS/AMI/Touchstone sources can also be imported with provenance;
high-speed channel/protocol validation remains external. See
[coverage, conditions, caching and limitations](docs/FAST_ACCURATE_MODELS.md).

## API calls and reasoning

OpenCode **Go (subscription)** and **Zen (pay as you go)** are separate provider choices.
Select Go for a Go subscription; its endpoint is `/zen/go/v1`, and Zen credit is never
used as an automatic fallback. Both choices use the existing OpenCode credential slot.
The Bob edition continues to accept only IBM Bob.

Extraction, model authoring and JSON repair use the selected provider's highest
configured reasoning setting: `max` for DeepSeek, OpenAI, Claude Opus and OpenRouter;
`high` for Gemini; `xhigh` for Grok 4.6. OpenCode's default DeepSeek model uses `max`.
An exhausted thinking budget is reported without silently disabling thinking. Models
without an exposed reasoning control (including the current Groq/Mistral defaults) and
Bob Shell retain their provider-controlled behavior; they cannot be labeled max.
These settings apply to the documented default models and compatible overrides.

## Install

Use **Code → Download ZIP** on **main**. Extract the archive, then double-click
**Install.exe** in the extracted repository folder. The pepper animation plays during
setup. The same folder contains INSTALL.txt and SHA256SUMS.txt.

Open SETUP once to select LTspice and save your API key. Python is bundled; LTspice and,
when using Bob, Bob Shell must be installed separately. This installer is unsigned.

To rebuild, run `installer\build.ps1 -Version 1.4.0`; see
[installer details](installer/README.md). The build refreshes the root installer,
instructions and checksum so committing those files updates Code → Download ZIP.

## Two builds of one product

The agent provider list is data (`src/boardmodeler/agent_providers.py`), so a restricted
build is selected by `build_flavor.BOB_ONLY`, with a restricted catalog (D-015):

|Build|Accepts|Use it for|
|---|---|---|
|`spice-maker` (this repository)|Bob **and** every vendor key in the catalog|Trying providers, comparing models, day-to-day work|
|`spice-maker-bob`|The IBM Bob API only|The Bob-native workflow: no provider row on SETUP, `BOB API KEY` only|

What each piece guarantees:

* **The spec is frozen before the agent starts.** Limits, tolerances, probe bindings and
  citations live in `spec/characteristics.json`; the agent may write only
  `model/<SUBCKT>.lib` and `.asy`. A changed spec aborts the build as `UNKNOWN(spec_tampered)`.
* **The harness owns the verdicts.** One probe deck per bound characteristic runs in real
  LTspice; the measured value is compared to the cited limit. A probe that cannot answer
  its question (no crossing, signal not saved, run truncated, port missing) is `UNKNOWN`
  with the reason — never `PASS`.
* **Unreachable rows stay visible.** Datasheet rows no probe can exercise (internal
  oscillator behavior, thermal response, package facts) are listed on `MODEL_CARD.md` with
  a `not_testable_reason`, so a reader sees the size of the claim, not a summary of it.

The older paths — circuit checking, the board demonstration, the desktop UI — remain in
the tree and are described further down; they are not on the model-authoring path.

## Honesty invariants

These are enforced in code, not by convention, and they are the reason to trust a
report:

* No `PASS` without an observed simulator artifact. A missing measurement, an
  uncovered assertion window, or a signal that never appears becomes `UNKNOWN`.
* A requirement whose behaviour the model's capability probe did not establish as
  `supported` is gated to `UNKNOWN` — an average vendor model cannot silently
  "pass" start-up.
* Every cited requirement is verified against the text of the page it cites; an
  invented citation fails verification and its dependent tests evaluate to
  `UNKNOWN`.
* A "must not occur" requirement additionally requires the run to have reached the
  end of its window with the signal observable: the absence of a crossing is not a
  pass on its own.
* Repair is capped and constrained: it may only touch `models/candidates/<n>/`, and
  relaxing a tolerance, deleting a test, editing evidence, or editing the circuit
  raises `RepairViolation` and stops the loop.
* Nothing is ever written into the LTspice installation, and vendor model bytes are
  never copied into an export.

## Setup

```powershell
uv sync --all-extras              # Python 3.14 venv with every dependency
uv run boardmodeler doctor --json # configured LTspice + smoke test, reader backend, OCR, credentials
uv run boardmodeler setup         # optional: record the LTspice path, provider, and data policy
uv run boardmodeler ui            # optional: the desktop application
```

`doctor` reports the real smoke-test measurement (an RC step's analytic 0.632 V,
±2 %), not just "found": if the simulator is absent or its outputs unusable, that is
what it says.

## Workflows

### The integrated board demonstration

A complete board — 12 V input, buck to 3V3, LDO to 1V8, reset circuit, straps,
sideband, and an unmodelled PCIe switch kept explicitly outside dynamic coverage —
is built from committed fixtures and checked end to end:

```powershell
uv run boardmodeler demo build --out build/demo
uv run boardmodeler circuit check --project build/demo --json --out build/demo-results.json
uv run boardmodeler run mutations --project build/demo --report build/mutation-report.json
```

`demo build` reports its requirement, test-case, and static-finding counts;
`circuit check` prints one line per test with its status; `run mutations` injects
each fault into its own copy, runs the check, and records whether the fault was
detected — hashing the original project before and after to prove the mutation did
not leak into it.

### Testing a circuit

```powershell
uv run boardmodeler run tests --project <project-dir> --scope circuit_compliance --json
uv run boardmodeler circuit check --project <project-dir> --circuit <schematic.asc> --fault-matrix
```

### Extracting from documents

```powershell
uv run boardmodeler extract --project <project-dir> --doc <datasheet.pdf> [--allow-remote]
```

`--allow-remote` is required for any provider that leaves the machine, and it is
checked against each document's own `remote_inference_allowed` flag and
classification; the disclosure (provider, endpoint, pages, characters) is printed
and recorded in the run manifest. The default provider is the offline fixture
replay, so repeat runs make zero requests.

### Exporting a model

```powershell
uv run boardmodeler export --project build/demo --out build/demo-export
```

The export carries only relative paths, hashes every file into `manifest.json`,
records the vendor model's hash while refusing to copy vendor bytes, and lists every
requirement with no dynamic test in `coverage.json`.

## Limits we state rather than hide

* **OCR is unavailable on this machine** (`tesseract` absent). Pages that need OCR
  produce an explicit evidence gap; OCR text is never presented as embedded text.
* **The ported TI vendor model is evidence, not a workhorse.** It simulates, but a
  2.1 ms application run hit a 600 s cap, so the dynamic tests and the demo use the
  generated behavioural template while the vendor model's capability record states
  exactly which behaviours its probes established (see `docs/DECISIONS.md` D-010).
* **Real PCIe-switch qualification is `BLOCKED`.** No public documentation exists for
  the device class the synthetic fixture stands in for, so the fixture is labelled
  `origin=TEST_FIXTURE` everywhere and cannot be presented as device data.
* **No temperature or statistical claims.** Without modelled temperature dependence
  there is no temperature validation, and no distributions are inferred from
  min/max limits.

## Layout

|Path|Contents|
|---|---|
|`src/boardmodeler/domain/`|Record schemas (pydantic), enums, hashing, ids, constrained expression AST|
|`src/boardmodeler/simulation/`|LTspice batch invocation, log parsing, `.raw` readers, backend selection|
|`src/boardmodeler/verification/`|Assertion evaluation, vacuous-pass guards, corners, scenarios, engine|
|`src/boardmodeler/schematic/`|`.asc` parse/generate, SPICE netlist parsing, neutral CSV model, static checks, mutations|
|`src/boardmodeler/models/`|Vendor-original store, behavioural templates, capability probes, symbol generation|
|`src/boardmodeler/requirements/`|Requirement extraction, validation, source-consistency review|
|`src/boardmodeler/documents/`|PDF text/page extraction, document store, OCR interface, chunking|
|`src/boardmodeler/providers/`|Fixture / HTTP inference / Bob providers behind one protocol|
|`src/boardmodeler/pipeline/`|Stage chain, baseline freeze, bounded repair, child-process worker|
|`src/boardmodeler/reporting/`|Export, model card, HTML report|
|`src/boardmodeler/security/`|Credentials, path guards, subprocess guard, data policy|
|`src/boardmodeler/ui/`|PySide6 desktop application (thin client over the same pipeline)|
|`fixtures/`|Committed test fixtures (synthetic switch contract, demo board)|
|`docs/`|`PLAN.md`, `STATUS.md`, `DECISIONS.md`, `INTERFACES.md`, `FAST_ACCURATE_MODELS.md`|

## Rules

See `AGENTS.md` — the short version: run `uv run pytest -q` before claiming
anything works, never write into the LTspice installation, never record a result
that was not observed, and label every synthetic fixture as synthetic.
