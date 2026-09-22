# PLAN 1.4.0 — reported-defect remediation

Derived from the owner's defect list (8 problems) and the ranked grading
(1 model quality, 2 speed to model, 3 usability/containment, 4 UI, 5 usage).

Reproduction baseline, measured on `main` @ `0c239c6` on this machine
(Python 3.14.2, LTspice 26.0.0, provider `deepseek`):

| Run | Mode | Result | Wall |
|---|---|---|---|
| LM358 (TI, 68 pages) | `--sanity` | model published, 1 author turn, `UNKNOWN` (honest "sanity checked; electrical accuracy unverified") | **181 s** |
| UCC28251 (TI, 58 pages) | full | **build failed** — `api_request_failed: timeout: the 24.1999 s budget for this turn was exhausted before attempt 2 of 3` → 0 PASS / 127 UNKNOWN | — |

## Root cause of defect 1 (and defect 2)

They are the same defect. The full pipeline derives a per-turn HTTP budget that
is far below what a datasheet-sized authoring turn needs, then treats exhaustion
as a *build failure*:

* `authoring/api_backend.py:769-783` — `_post_json` computes
  `deadline = monotonic() + timeout_s`, and if `remaining <= 0` raises
  `ProviderError("timeout", ...)`, aborting the turn.
* `sanity.py:368` calls `backend.author(...)` with **no** `timeout_s`, so the
  budget comes from the backend's construction (`make_model.py:1085`).
* `make_model.py:407` defaults the request's own `timeout_s` to `120.0`.
* The recorded UCC28251 run got **24.1999 s**, i.e. a *residual* of a larger
  budget, not a configured value.
* `reinforce.py:892-901` is bounded the same way and the recorded run shows
  `reinforce → skipped: search_budget_exceeded: ... did not finish within 45 s`.

So a full-mode build on a large datasheet is **arithmetically doomed**: the
turn budget is smaller than the vendor's own response latency, and the failure
is reported as a *model* outcome (`UNKNOWN`) rather than a *budget* outcome.

## Goals (each must be demonstrably met or explicitly reported unmet)

* **G1 No budget-induced failure.** A build never reports a model status because
  an *internal* wall-clock budget expired. Budget exhaustion must be surfaced as
  `BLOCKED` with its own reason, never silently as `UNKNOWN`, and must never be
  the normal case.
* **G2 Faster to model, by architecture not by caps.** Reduce wall time to a
  published model. No new artificial runtime limit may be introduced; existing
  ones that truncate work are removed or made explicit and generous.
* **G3 No Windows-key persistence.** No DPAPI, no Credential Manager, no
  registry, no AppData. LTspice path, API key and model directory all live in
  the project directory.
* **G4 Self-contained install.** The download/install step provisions the
  virtual environment and shortcut inside the extracted folder and does not
  disturb any other existing copy.
* **G5 Resizable, tidy UI.** Main window and setup are user-resizable; the
  doctor report is compact and scrollable instead of a 4000-char truncation.
* **G6 Explicit first run.** The user sets the LTspice executable and API key
  explicitly at least once; startup performs no install-path discovery.
* **G7 Hourglass indicator.** An hourglass sits beside the elapsed timer and
  animates while the build runs.
* **G8 Vendor-only egress, fast give-up.** Outbound HTTP is restricted to the
  endpoints named in `agent_providers.CATALOG`; unsupported destinations are
  refused immediately rather than retried.

## Constraints (from the repository's own hard rules)

`AGENTS.md` is binding, in particular: never write into the LTspice
installation; never record an unobserved result (`PASS` requires an observed
artifact with a hash); never relax a tolerance to make a model pass; no
telemetry; the spec is frozen and re-hashed every turn. `tests/gui/test_window_contract.py`
pins button colours (an enabled button must not be painted black), forbids
setup widgets in the model window, and forbids a menu bar.

## Both editions

`tools/sync_shared_core.py` copies shared `src/`, `tests/`, `installer/` and
`.github/` files between checkouts and **excludes** the flavour-specific set:
`build_flavor.py`, `agent_providers.py`, `authoring/api_backend.py`,
`providers/http_inference.py`, `providers/bob.py`, `ui/setup_dialog.py`,
`ui/model_maker.py` and a handful of tests.

Therefore every change is one of two kinds:

* **shared** (`sanity.py`, `loop.py`, `make_model.py`, `config.py`,
  `storage.py`, `security/credentials.py`, `simulation/ltspice.py`,
  `authoring/reinforce.py`, …) — mirrored by running
  `python tools/sync_shared_core.py ../spice-maker-bob --apply` from
  `spice-maker`.
* **flavour-specific** (the exclude list) — hand-adapted per edition. Bob's
  `api_backend.py` is 83 lines against 1032, and its `agent_providers.py` is 93
  against 316, so these are *not* filtered copies and must be edited in place.

## Workstreams

Disjoint file ownership; no two agents touch one file.

### Phase A (parallel)

* **A1 — Budget and speed (core authoring)**
  `authoring/api_backend.py`, `authoring/loop.py`, `pipeline/make_model.py`,
  `authoring/sanity.py`, `authoring/reinforce.py`.
  Remove the residual-budget abort; make the per-turn budget generous and
  explicit; distinguish budget exhaustion from model outcome; keep the
  observed-artifact rules intact.
* **A2 — Secrets and portable storage**
  `security/credentials.py`, `config.py`, `storage.py`.
  Replace DPAPI with project-directory storage for LTspice path, API key and
  model directory. Explicit first run. No fallback to any Windows store.
* **A3 — Installer, venv and shortcut**
  `installer/*`, `INSTALL.txt`.
  Provision the venv and a folder-local shortcut without touching other copies.
* **A4 — Network policy**
  `providers/http_inference.py`, `authoring/reinforce.py`,
  `agent_providers.py`, `security/policy.py`.
  Restrict destinations to the catalog's documented vendor endpoints; refuse
  anything else at once; bound give-up.

### Phase B (after A2 and A3)

* **B1 — UI**
  `ui/model_maker.py`, `ui/setup_dialog.py`, `ui/app.py`, `installer/assets/*`.
  Resizable main + setup windows, compact scrollable doctor, hourglass
  animation beside the timer, no startup discovery, and the window-contract
  test still green.

### Phase C — verification

* **C1 — Datasheet suite** (LM358, UCC28251, SN74LVC1GX04, charge pump):
  time to model, published artefacts, honest statuses.
* **C2 — Model quality**: LTspice load/parse and the harness verdicts.
* **C3 — UI and end-to-end**: real-window checks with computer use; installer
  and venv from a clean extraction.
* **C4 — Both editions**: bob builds and reports as the Bob-only edition.

## Verification commands (repository level)

```powershell
uv run pytest -q -m "not ltspice"      # simulator-free suite
uv run pytest -q                       # includes ltspice-marked tests
uv run ruff check . ; uv run ruff format --check .
black --line-length 100 .
uv run boardmodeler doctor --json
python tools/sync_shared_core.py ../spice-maker-bob          # drift check
python tools/sync_shared_core.py ../spice-maker-bob --apply  # mirror shared
```

## Known environment caveats on this machine

* Windows **Smart App Control is enabled**, so the `.venv\Scripts\*.exe`
  console shims are blocked (`os error 4551`). Use
  `uv run python -c "from boardmodeler.cli import main; main()" <args>` instead
  of `uv run boardmodeler <args>`.
* `tesseract` is absent, so OCR of image-only datasheet pages is an evidence
  gap, not a bug.
