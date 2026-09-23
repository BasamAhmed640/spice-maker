# PLAN 1.5.0 — containment, safe agent deployment, real end-to-end testing

Derived from the owner's second defect list (`SPICE-MAKER-UPDATE.md`) and its grading:
**GUI buttons work (P/F), Install.exe works for the newest version (P/F), buttons work (P/F),
safety (adherence), functionality (adherence).**

Reproduction baseline on this machine (Python 3.14.2, LTspice 26.0.0 at
`%LOCALAPPDATA%\Programs\ADI\LTspice\LTspice.exe`, `main` @ `b98869d`, tree clean):
the 1.4.0 defects named in the owner's list are **not** reproductions of the 1.4.0 defects —
they are new requirements. What already exists is recorded below so no goal is claimed
without evidence.

| Owner's words | State at `b98869d` (measured) | Goal |
|---|---|---|
| "should not allow rogue agents" | Bob Shell is the default provider of this edition and launches a third-party CLI agent | **G1** |
| "Remove Bob from non bob version" | `agent_providers.CATALOG[0]` is `bob`; `authoring/backends.BobShellBackend` runs `bob.exe` | **G1** |
| "change to only one box for internet access" | 3+ separate network controls (`reinforce_check`, `allow_remote` ×2, `allow_bob_shell`) | **G2** |
| "Bash commands … excessively safe" | 3 unguarded spawns bypass `security/subprocess_guard`; `worker_client` inherits the whole parent env and has no timeout | **G3** |
| "NEVER access my Windows API key" | no registry/DPAPI/keyring today; LTspice still reads/writes `%APPDATA%\LTspice.ini` because no `-ini` is passed | **G4** |
| "set up its own virtual environment" | installer provisions `.venv` from vendored CPython (no network) — but nothing verifies it at runtime | **G5** |
| "no ability to reopen a spice model … rerun verifications" | `model test` exists on the CLI; the GUI only re-runs the model it just built in this session | **G6** |
| "Tests have not been extensive enough … UI, functionality" | 1515 tests, but no test clicks every button and no test runs extract → Install.exe → LTspice → model | **G7, G9** |
| "Install.exe works for the newest version" | tracked `Install.exe` is stamped 1.4.0 and was never launched at 1.4.0 in a recorded run | **G8** |

## Goals — each must be demonstrably met or explicitly reported unmet

**Safety (highest weight)**

* **G1 No rogue agent is reachable.** The general edition contains no path that executes a
  third-party agent CLI or any tool call an LLM produced. Bob is removed here: no catalog
  entry, no backend, no `bob_environment`, no policy switch, no installer/README claim. Every
  remaining provider is a plain HTTPS request to a host the vendor documents. Proof: zero
  `bob` references in shipped code; a meta-test that fails if a provider entry gains a
  subprocess wire; a meta-test that fails if any `subprocess` call site in `src/` is not one of
  the two sanctioned wrappers.
* **G2 Exactly one internet box.** One user-visible switch — `INTERNET ACCESS` in SETUP —
  governs every outbound byte (agent API, supporting-material search). Per-build full
  verification moves to the build window, where it belongs, and is not a network control.
  With the box off the product sends nothing and says so. Proof: a test that counts network
  controls in SETUP (exactly 1) and a test that runs a build with the box off under a socket
  audit hook and observes **zero** connection attempts plus a stated refusal.
* **G3 Command execution is one policy.** Every child process in shipped `src/` goes through
  one function: absolute executable pinned by name-allowlist, `shell=False` structurally
  impossible, argv shape validated, cwd pinned, env built from an allowlist (never
  `os.environ` wholesale), a timeout always present, output bounded. No agent-, PDF- or
  filename-derived string ever reaches argv. Proof: policy unit tests plus the AST meta-test.
* **G4 No Windows secret, and a compartmentalized simulator.** No registry, DPAPI, keyring,
  machine GUID or license query (keep the existing guard). LTspice runs with a private
  `-ini` inside the app folder and a private scratch directory, so it cannot read or write
  the user's LTspice profile; the app never writes into an LTspice installation directory.
  Deck directives that can reach outside the run folder (`.include`, `.lib`, `.inc`,
  `.wave`, `.savebias`, `.loadbias`, `.savestate`, `.loadstate` with absolute, UNC, `..` or
  URL paths) are refused before the simulator starts. Proof: per-directive tests, `-ini`
  argv assertion, and an audit hook that fails the test on any write outside the app root.
* **G5 The copy owns its interpreter.** `boardmodeler env --json` reports the interpreter,
  version, venv path and wheel provenance, and `CHECK ENVIRONMENT` shows it. Nothing ever
  installs into a system Python. Proof: command output on a fresh extracted copy + a test
  that the venv path is inside the copy.

**Functionality**

* **G6 Reopen a built model and re-verify it.** `boardmodeler model open --out DIR [--verify]`
  loads a model directory that exists on disk, prints its card, requirements and the last
  recorded verification, and re-runs verification on demand; the GUI gains **OPEN MODEL…**
  with the same behaviour, so a model can be verified after the app restarted. Proof: an
  end-to-end test that builds, drops all in-process state, reopens from disk, re-verifies and
  re-reads the updated card.
* **G7 Every button works.** A sweep that constructs every window and dialog, clicks every
  push button, checkbox and tool button with dialogs sandboxed, and fails on any exception,
  traceback or stuck modal — plus a real-window launch that is clicked through with the
  computer-use driver and screenshotted. Proof: `tools/gui_sweep.py` report + `tests/gui`.
* **G8 Install.exe works for the newest version.** The released ZIP is produced at 1.5.0, then
  a single recorded run proves the owner's journey on a clean folder: ZIP → extract →
  `Install.exe --silent --no-launch` → layout, `.venv`, launchers → set the LTspice path →
  build a model → open and re-verify it. Proof: `tools/verify_release_zip.py` PASS report with
  hashes and timings.
* **G9 The whole journey is a test, not a story.** `tests/e2e/test_download_install_model.py`
  encodes that journey (opt-in markers, skipped only by a positive capability probe, never a
  silent skip) and CI gains an installer job. Proof: test file runs green locally and the
  workflow names it.

## Constraints

* The repository's own hard rules stay in force: never write into an LTspice installation or
  library directory; never record an unobserved result; the spec is frozen and hashed; the
  agent may write only `model/<SUBCKT>.lib` and `model/<SUBCKT>.asy`; no telemetry; secrets
  never leave the folder, never enter argv, never into a log.
* The Bob-only edition (`spice-maker-bob`) keeps Bob. Removal is scoped to the general
  edition; shared files that both editions own must stay syncable
  (`tools/sync_shared_core.py`).
* No hardcoded model or provider. No new dependency that is not already vendored.
* Model quality, speed and honesty rules are unchanged; nothing in this plan may relax a
  tolerance, delete a probe or turn `UNKNOWN` into `PASS`.

## Grading map

| Grader's line | Goals that carry it |
|---|---|
| GUI buttons work (P/F) | G7, G2 |
| Install.exe works for newest version (P/F) | G8, G5 |
| Buttons work (P/F) | G7 |
| safety (adherence) | G1, G2, G3, G4, G5 |
| functionality (adherence) | G6, G8, G9, existing suite green |

## Order of work

1. **Wave 1 (parallel, disjoint files).** Bob removal (G1); execution policy module (G3);
   deck directive policy module (G4); GUI sweep harness (G7); release-zip harness (G8/G9).
2. **Wave 2 (parallel, after wave 1 lands).** Integrate the policy modules into the
   simulator, worker and UI; the single internet switch (G2); reopen-and-verify (G6).
3. **Wave 3.** `boardmodeler env` (G5), full gate (`ruff`, full pytest with LTspice), release
   build at 1.5.0, then the recorded end-to-end run from the GitHub download (G8), then
   `docs/STATUS.md`, `docs/DECISIONS.md`, README/INSTALL, SHA256SUMS, commit and push.

## Evidence rules for this plan

Every goal is reported in `docs/STATUS.md` with the exact command and its observed output.
A goal stays open if its proof did not run. Nothing is described as verified because it was
written; it is verified when the command that fails without it has passed.
