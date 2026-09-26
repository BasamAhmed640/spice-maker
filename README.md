# Spice Maker

Source and checked-in installer version: **1.7.0**. Check `SHA256SUMS.txt`
against the installer in the ZIP you download.
The [fresh GitHub ZIP installation and model check](docs/evidence/2026-09-25-release/REPORT.md)
passed on Windows, including GUI startup and a saved-model LTspice retest.

## Windows setup and model verification

From a GitHub **Code → Download ZIP** archive, extract the folder and run its included
`Install.exe`. The installer carries Python and pinned packages and creates `.venv` in
that extracted folder without using the computer's Python installation. Launch
`Start.cmd`, open SETUP, choose the existing LTspice executable with **BROWSE**, choose
a model folder in this copy, and save an API key for the selected provider. LTspice is
never searched for or selected from an inherited environment variable. The current
general edition sends authoring requests directly to the selected provider's HTTPS API;
it does not give the authoring model command or file-system tools.

The installer carries a CPython 3.14 runtime, hash-checked wheels and its generated
`env/requirements.txt`. It installs those pins into this folder's `.venv` with no
package download during installation. `Boardmodeler.cmd` uses that environment for
command-line work; `Start.cmd` opens the separately frozen GUI, because the `.venv`
does not include Qt.

For a Python 3.14 source setup using IBM Bob's standard project pattern, run these
commands in PowerShell from the extracted folder. They create and install into this
project's `.venv` only; they require an existing Python 3.14 and package access:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\boardmodeler.exe setup
.\.venv\Scripts\boardmodeler.exe doctor --json
```

For a measured model, open the model maker and keep **FULL VERIFICATION** selected before GO.
The optional quick choice performs structural checks and an unpowered load only; its
electrical status remains **UNKNOWN**. Full verification creates local test circuits,
runs the configured LTspice executable in batch mode, and compares observed waveforms
with frozen datasheet requirements. It reports a PASS only for measured rows that meet
their limits; it does not certify untested behavior. The command-line `model build`
performs full verification by default; `--sanity` opts into the quick checks.

```powershell
.\.venv\Scripts\boardmodeler.exe model build --part TPS54332DDA --datasheet .\TPS54332DDA.pdf --out .\models\TPS54332DDA --allow-remote
```

The model build requires a real TPS54332DDA datasheet PDF, a selected provider key,
and an LTspice path saved in this copy's SETUP. The generated `MODEL_CARD.md` records
the measured and untested rows. [IBM Bob's workspace rule format](https://bob.ibm.com/docs/shell/configuration/bobshell-custom-rules)
is represented by `AGENTS.md` and `.bob/rules/`; the application itself does not run Bob.

**1.7.0: template-first buck models.** For a supported buck-converter pinout, the app
starts with a known-convergent circuit template. It fills parameters from cited
datasheet rows where available, labels any remaining template defaults, and runs
the same LTspice harness before involving the agent. The agent receives measured
failures for bounded repair; if it cannot improve the result, the measured template
can still be delivered with its FAIL and UNKNOWN rows visible. Other device classes
continue through the existing authoring path. A template parameter is provenance,
not proof of accuracy: only simulator evidence can make a requirement PASS.

**1.5.0: containment, one network switch, and models you can reopen.** This edition now ships **no CLI agent at all** — IBM Bob lives only in the separate Bob-only build, so nothing in this copy installs or executes a third-party agent, and every provider is a plain HTTPS request to the host its vendor documents. Egress has exactly **one control**: SETUP's `INTERNET ACCESS`, which governs the provider API and the part vendor's own site for supporting material; with it off the app sends nothing and refuses a build with that reason (proved by a test that runs the refusal path behind a socket tripwire).

Every child process this program starts now goes through one policy module (`security/execution.py`): an absolute allowlisted executable, no shell, an argv shaped so no string from an agent, a datasheet or a filename can land in a flag position, a mandatory timeout, bounded output, and an **allowlisted child environment that is never a copy of the parent's**. That last point is what keeps your agent key away from the simulator: LTspice runs with fifteen OS variables and nothing else, so it cannot see `DEEPSEEK_API_KEY` or any other secret in your shell. The simulator is also still launched without `-I` or `-ini` — both were re-measured on LTspice 26.0.0 and both make it open its GUI window instead of running in batch (a fresh *or* seeded private settings file does it; a redirected `APPDATA` does too), which is why the boundary is the process environment rather than a private profile. A build is no longer a one-session artifact: **OPEN MODEL…** in the window and `boardmodeler model open --out DIR [--verify]` reopen a finished model directory from disk, show its recorded status and rows, and re-run verification on demand.

Testing followed the same rule — the graded claims are commands, not prose. `tools/gui_sweep.py` clicks every button in all five windows with dialogs, subprocesses and config paths sandboxed (it fails on any exception, traceback or stuck modal, and reports 0 processes spawned), `tools/verify_release_zip.py` drives the whole journey the owner asked for — download the GitHub ZIP, extract it, run `Install.exe --silent`, check the in-folder `.venv`, set the LTspice path, build a model with real simulator runs, then package a fresh ZIP — and a source-level meta-test fails if any `subprocess` call site in `src/` bypasses the policy module. [What changed and what was measured](docs/STATUS.md).

**1.4.0: a model build no longer fails on its own clock.** A full-mode build used to fund its retries from what was left of one turn deadline, so a slow reasoning-class model left its own retry seconds to run in and that clock failure was published as the model's verdict. A retry now requires a viable budget, budget exhaustion is its own reported condition, and the supporting-material search declines immediately rather than spending an allowance it cannot use. Measured on the same datasheet and provider: cold 2828.7 s with 4 PASS rows, warm 1559.1 s with 34 PASS rows, 4 extraction cache hits, the model published both times. The published status is still `UNKNOWN` — the honest label for a partly verified model, because 34 simulator-observed PASS rows out of the testable set is not measured accuracy. [Defect remediation](docs/STATUS.md).

The rest of 1.4.0 is containment and usability. Credentials are a plain local file in this folder (`data/credentials.json`) with no DPAPI, no registry entry and no Credential Manager entry — **weaker at rest than what it replaced**, because anyone who can read the folder can read the key. LTspice is configured only after the user chooses its path in SETUP; no install-location search is offered. Outbound inference is pinned to the selected provider's documented host and refused before any request is sent, and the supporting-material search reads only the part vendor's sites and gives up immediately with a stated reason. The model window and SETUP are resizable, the doctor report is a scrollable view showing the whole report (it used to reach only its last 4000 characters), and an hourglass beside the timer animates only while a build runs. The installer provisions a real in-folder `.venv` from vendored wheels with no network access at install time, writes `Start.cmd`, `Boardmodeler.cmd` and a folder-local shortcut, and leaves an existing copy of the app untouched; that `.venv` has no Qt, so window-opening commands (`ui`, `setup`) go through `app\SpiceMaker.exe`. [Storage and fresh-start instructions](docs/PORTABLE_STORAGE.md).

**1.3.0 introduced quick structural checks.** Quick mode skips AI test-circuit planning and uses structural checks plus a five-second unpowered LTspice load when available. Electrical accuracy remains explicitly unverified. The current GUI defaults to **FULL VERIFICATION** beside GO; you may explicitly uncheck it for a quick draft and run full verification later. [Modes and limitations](docs/QUICK_MODE.md).

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

The checked-in installer identifies itself as **1.7.0** in `INSTALL.txt` and
includes the template-first model path. Python is bundled; LTspice is a separate
prerequisite; this edition installs no CLI agent. `SHA256SUMS.txt` holds the
checksum of the checked-in installer.

**Give it a datasheet and a part number; an agent authors an LTspice model. The GUI defaults to full electrical verification. Quick structural checks are an explicit unverified draft choice. You get a `.lib`, a symbol and a card that states what was checked and what remains unverified.** That is the product. Everything below the fold is
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
the whole authentication story; there is no login anywhere in this application. **DeepSeek** is
the default provider. Every provider this edition accepts — OpenAI, Anthropic, Google,
DeepSeek, OpenRouter, xAI, Groq, Mistral, OpenCode Zen and OpenCode Go — is a plain HTTPS
request to the host that vendor documents: no CLI agent is installed or executed, and no
agent tool call runs on this machine. Bob exists only in the separate Bob-only edition. Only
the selected key is kept, as a plain local file in this folder
(`data/credentials.json`) — never in a config file, a manifest or a log line. Nothing is bound to
Windows and the file is **not encrypted**: anyone who can read this folder can read the key, so keep
the copy private. Setup is one page and holds only what persists: the LTspice
path (with a smoke test), the agent provider and key, the model id when the provider takes one,
the folder finished models go to, the read-only LTspice user library path, and the
single **INTERNET ACCESS** switch for provider requests and supporting material.
Installing a finished model is the window's **Install into LTspice** action,
which copies into this folder's `library/`; add its `sym` and `sub` folders to
LTspice's search paths once. The main window holds the
part number, the datasheet, the save location, **FULL VERIFICATION** beside **GO**,
and the progress and result controls. Without an agent
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
Bob exists only in the separate Bob-only edition, which accepts IBM Bob alone.

Extraction, model authoring and JSON repair use the selected provider's highest
configured reasoning setting: `max` for DeepSeek, OpenAI, Claude Opus and OpenRouter;
`high` for Gemini; `xhigh` for Grok 4.6. OpenCode's default DeepSeek model uses `max`.
An exhausted thinking budget is reported without silently disabling thinking. Models
without an exposed reasoning control (including the current Groq/Mistral defaults)
retain their provider-controlled behavior; they cannot be labeled max.
These settings apply to the documented default models and compatible overrides.

## Install

Use **Code → Download ZIP** on **main**. Extract the archive, then double-click
**Install.exe** in the extracted repository folder. The pepper animation plays during
setup. The same folder contains INSTALL.txt and SHA256SUMS.txt.

Open SETUP once to select LTspice and save your API key. Python is bundled; LTspice must be
installed separately; this edition installs and runs no CLI agent. This installer is unsigned.

**What is and is not a sandbox.** The project `.venv` isolates Python packages only, and a
`.bobignore` (Bob edition) only hides files from Bob's context. Neither is an OS sandbox: neither
restricts filesystem, network or process access by this app, LTspice or the model provider.
What does contain this app: LTspice runs only from the path you choose in SETUP (nothing
searches for it); the simulator gets an allowlisted environment, so no API key reaches it; the
agent is a plain HTTPS request that runs no tools and no shell on this machine; and only the
application writes files (decks and the candidate model). The key file, `data/credentials.json`,
is plain text inside this folder. The optional `sim` extra (`spicelib`, not in `requirements.txt`) checks LTspice's default
install locations when it is imported; the app imports it only after an LTspice path is set.

To rebuild, run `installer\build.ps1 -Version 1.7.0`; see
[installer details](installer/README.md). The build refreshes the root installer,
instructions and checksum so committing those files updates Code → Download ZIP.

## Two builds of one product

The agent provider list is data (`src/boardmodeler/agent_providers.py`), so a build ships the
catalog it sells, selected by `build_flavor.BOB_ONLY` (D-015). This repository is the general
edition: its catalog holds HTTPS API-key providers only, and no code path here can start a
local agent CLI. Bob is a separate product, built from the `spice-maker-bob` repository.

|Build|Accepts|Use it for|
|---|---|---|
|`spice-maker` (this repository)|The vendor HTTPS API keys in the catalog (default: DeepSeek)|Trying providers, comparing models, day-to-day work|
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

The old board demonstration and checker remain in the tree for internal
regression fixtures only. They are not product workflows. The desktop UI is
for generating and inspecting SPICE models. See [D-052](docs/DECISIONS.md):
there is no board import or user-facing board findings report.

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

The product workflow generates a `.lib` model, `.asy` symbol, cited model card,
and verification results. Model alarms are checked with small code-built
circuits and run inside the user's LTspice simulation. There is no board
checker or board report workflow.

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
uv run boardmodeler export --project <model-project-dir> --out <export-dir>
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
|`src/boardmodeler/schematic/`|Legacy internal test-circuit helpers; no board import feature|
|`src/boardmodeler/models/`|Vendor-original store, behavioural templates, capability probes, symbol generation|
|`src/boardmodeler/requirements/`|Requirement extraction, validation, source-consistency review|
|`src/boardmodeler/documents/`|PDF text/page extraction, document store, OCR interface, chunking|
|`src/boardmodeler/providers/`|Fixture and HTTP inference providers behind one protocol|
|`src/boardmodeler/pipeline/`|Stage chain, baseline freeze, bounded repair, child-process worker|
|`src/boardmodeler/reporting/`|Model export and card; legacy board HTML code is dormant|
|`src/boardmodeler/security/`|Credentials, path guards, subprocess guard, data policy|
|`src/boardmodeler/ui/`|PySide6 desktop application (thin client over the same pipeline)|
|`fixtures/`|Committed test fixtures (synthetic switch contract, demo board)|
|`docs/`|`PLAN.md`, `STATUS.md`, `DECISIONS.md`, `INTERFACES.md`, `FAST_ACCURATE_MODELS.md`|

## Rules

See `AGENTS.md` — the short version: run `uv run pytest -q` before claiming
anything works, never write into the LTspice installation, never record a result
that was not observed, and label every synthetic fixture as synthetic.
