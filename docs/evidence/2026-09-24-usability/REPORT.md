# Spice Maker 1.6.0 / Spice Maker Bob 1.5.0 — usability evidence (2026-09-24, evening)

Everything here was observed on the build machine: Windows 11, LTspice 26.0.0 selected
explicitly, OpenCode Go `deepseek-v4.1-flash`, IBM Bob Shell 2.0.4 (no Bob API key on
this machine). Nothing below is claimed beyond what the listed files show.

## 1. What was broken, as observed

| Symptom | Observation | Cause |
|---|---|---|
| A build stopped at "read" on a readable datasheet | `NameError: name '_LENGTH_LIMIT' is not defined` inside pypdf 6.19.0 `NumberObject.read_from_stream`, 1 of 7 registrations of the LM358 PDF with a provider key in the environment, 0 of 7 without | a fault inside pypdf's parser (the attribute exists); not the file |
| The key check could call a good key "rejected" | the check request without the app's User-Agent got HTTP 403 from the OpenCode endpoint; the same key with it: accepted | the endpoint's edge refuses Python's default agent |
| `OPENCODE_GO_API_KEY` was ignored | the provider read only `OPENCODE_API_KEY`/`BOARDMODELER_OPENCODE_API_KEY` | alias missing |
| One slow fixture voided the rest | one 120 s timeout deferred 8 TPS54332DDA rows to UNKNOWN (earlier session) | serial harness with blanket deferral |
| Symbols were hard to read | VIN bottom-left and GND on the right (TPS54332DDA); VCC bottom-left and VEE among the outputs (LM358) | a two-column heuristic whose hint `"a"` matched any name containing an "a" |
| No visible readiness | nothing in the build window said whether the key, the model's reply format or LTspice would work until GO failed | — |
| Bob: 7 failing CLI tests | stale test: it expected LTspice discovery through `LTSPICE_EXE`, which the product removed (D-033) | test, not product |

## 2. What changed

See `docs/DECISIONS.md` D-037 … D-040 (both repositories).

## 3. Evidence

### 3.1 VERIFY KEY & TOOLS (PASS/FAIL item)

`window-after-verify.png` is the real build window after pressing VERIFY with this
machine's key and LTspice (10.5 s end to end):

| Light | State | Detail (verbatim) |
|---|---|---|
| API KEY | ok | OpenCode Go (subscription) accepted the key |
| MODEL | ok | deepseek-v4.1-flash answered in the generator's JSON file format |
| LTSPICE | ok | LTspice.exe ran the RC smoke circuit |
| PDF | ok | text PDFs are read with pypdf, with pdfium as fallback |
| OCR | warn | Tesseract not found: scanned (image-only) pages are skipped and reported as gaps; text PDFs are unaffected |
| INTERNET | ok | INTERNET ACCESS is on |

Bob edition, this machine, local state (no request sent): API KEY fail (no Bob key saved),
BOB SHELL found at `pi-node\current\bob.CMD` and `bob run --help` lists all 7 flags a build
passes, LTSPICE selected, PDF ok, OCR warn, INTERNET off in that copy's SETUP. VERIFY with
a Bob key runs Bob Shell once with every tool group disabled (not exercised here: no key).

### 3.2 Symbols

`symbols/before.png` (the symbols the earlier builds actually published) and
`symbols/after.png` (this generator, same ports), drawn by `tools/render_symbol.py`.
`SpiceOrder` is unchanged; `tests/reporting/test_symbol_layout.py` checks geometry,
conventions and — with LTspice — that a real netlist keeps the `.subckt` port order.

### 3.3 A full AI build of TPS54332DDA on the new code (timed)

`tps54332-ai-build/`. Same request as the earlier 1906 s build (full verification, OpenCode
Go, 3 author turns, 120 s per simulation, no web reinforcement), one build on the key:

| Stage | Time | Outcome |
|---|---|---|
| read | 2 s | 35 pages, embedded text |
| extraction (6 cached batches, 3 at once) | 209 s | rows + pin map |
| test planning | 385 s (was ~494 s) | 19 fixtures frozen, 87 rows declared not testable |
| author turn 1 | 457 s | two `SRFLOP` A-devices with 7 nodes; LTspice rejected the subcircuit; 18 fixtures deferred |
| author turn 2 | 303 s | node count fixed but outputs wired as inputs; no operating point; 18 deferred |
| author turn 3 | > 600 s | `deadline_exhausted` — the turn's API budget ran out |
| **total** | **1957 s** | **UNKNOWN, 0 PASS, no model delivered** |

The free-form AI author did not produce a model the simulator accepts in three turns. This
is the honest state of the AI-only path for a switching regulator; it is why §3.4 exists.

### 3.3b A full LM358 build on the new code (timed)

`lm358-build/`. The reviewed LM358 rows (zero extraction calls), AI-authored model, full
verification: **PASS — 32 measured rows passed real LTspice runs, 0 FAIL, 0 UNKNOWN,
10 not applicable, 295 s end to end.** The published `LM358.asy` uses the new layout
(`lm358-build/LM358.symbol.png`). The op-amp path works; the switching-regulator path (§3.3)
does not yet.

### 3.4 Template prototype — same fixtures, zero AI turns (not yet in the product)

`tps54332-template-prototype/TPS54332DDA.lib` is a hand-written peak-current-mode buck whose
parameters are the cited rows (VREF, fsw, min on-time, Dmax, ILIM, 12 A/V, EA ±7 µA, SS 2 µA,
EN/UVLO thresholds, IQ, Eco-mode). Logic is LTspice A-devices (8 nodes each), no B-source
latch. Judged by the product harness against the TPS54332DDA build's own frozen fixtures
(`judge.py`): **7 PASS, 8 FAIL, 4 UNKNOWN in 77.7 s of simulation, 0 API calls.** A second draft (no leakage through the open switch, COMP clamped at the Eco-mode level) reached **8 PASS, 8 FAIL, 3 UNKNOWN in 87.5 s**; its IQ_SHUTDOWN FAIL is the harness's signed comparison (−1.001 µA against +1 µA typical), not the model.

| Row | Verdict | Measured |
|---|---|---|
| UVLO_VIN (≥ 3.5 V) | PASS | 4.455 V |
| ISHDN (≤ 4 µA) | PASS | 2.12 µA |
| IOP_NONSW (≤ 120 µA) | PASS | 83.1 µA |
| EA source/sink (7 µA typ) | PASS | 6.99 µA |
| SS charge (2 µA typ) | PASS | 1.999 µA |
| max output current (≤ 3.5 A) | PASS | 3.499 A |
| switch current limit (≥ 4.2 A) | PASS | 5.35 A |
| VOUT_MIN (≥ 0.8 V) | FAIL | 0.79984 V (finite EA gain) |
| FSW (1 MHz typ) | FAIL | 0 Hz — no PH edge in the fixture's window |
| IQ_SHUTDOWN (1 µA typ) | FAIL | 2.12 µA (EN pull-up counted on top of IQ) |
| EN_TH (1.25–1.35 V) | FAIL | 1.682 V |
| switch current → COMP (12 A/V typ) | FAIL | 9 A/V |
| ILIM (4.2–6.5 A) | FAIL | 10.6 A |
| SS → VSENSE matching (10 mV typ) | FAIL | −0.4 V |
| Eco-mode COMP (0.5 V typ) | FAIL | −0.034 V (COMP clamp at 0 V, not 0.5 V) |
| VREF, min on-time, EN threshold (typ), Dmax | UNKNOWN | not settled / transition missing |

Every current PASS above also holds in magnitude. Each FAIL names a specific parameter or
structure to correct; none is a convergence failure.

### 3.5 A harness bug the prototype exposed

The prototype's first judgement was 19 UNKNOWN: LTspice logged "Gmin stepping failed" and then
"Source stepping succeeded in finding the operating point" and simulated to the end, but the log
parser kept the first line as a convergence issue, so the gate fixture was called a
convergence failure and the other 18 were deferred. Fixed in `simulation/log.py` (shared core):
a failed stepping attempt followed by a success is an operating point found; later time-step
or singular-matrix failures still count (`tests/ltspice/test_log_operating_point.py`).

### 3.6 Tests

| Suite | Result |
|---|---|
| General edition, full suite incl. GUI, `LTSPICE_EXE` set (`4325c44`) | 1757 passed, 15 skipped, 0 failed |
| Bob edition, full suite incl. GUI (`e103f49`) | 1448 passed, 25 skipped, 4 failed — 3 stale doctor tests and 1 stale integration test; both fixed afterwards (`381f65f`, `8d2d909`); the doctor/run-tests files then 17/17 and `test_make_model.py` 55/55 |
| Shared core | identical: 41 files |

## 4. Critique of the method

* **Free-form AI authoring is the bottleneck and the failure point.** Each turn costs 5–10
  minutes at `high` reasoning effort and a switching regulator did not converge in three.
  Speeding up the harness (parallel fixtures) cannot fix time spent waiting for the model.
* **Test planning by the AI is useful but slow** (385 s) and it produces fixtures whose
  measurement windows assume behaviour (e.g. switching by 1 ms) that a model must match.
* **What works:** frozen, cited rows; one simulator verdict per row; parallel fixtures; lint
  that names the broken line; the refusal to publish a model LTspice rejects.
* **What should change next:** template-first authoring for recognised classes — the part's
  class picks a known-convergent template, the cited rows fill its parameters, the harness
  judges it in about a minute, and the AI only repairs specific FAIL rows. §3.4 is the
  evidence that this is faster (78 s vs 1957 s) and already better (7 PASS vs 0).

## 5. Critique of the symbols

Before: pins only on the left and right; VIN at the bottom-left and GND on the right for the
buck; VCC bottom-left and VEE among the outputs for the dual op-amp; pins of one amplifier
scattered. Pin names were visible (drawn inside the body) but the layout did not read like a
schematic. After: supplies on top, grounds/pads at the bottom, inputs left, outputs right,
one op-amp per block (IN+, OUT, IN−), boot and switch node together above the feedback pins.
Remaining limits: multi-unit parts are still one rectangle (no separate op-amp triangles), and
the preview PNG is a review tool (`tools/render_symbol.py`), not part of the deliverable.
