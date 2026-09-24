# Evidence report — convergence-first authoring and a shared core (2026-09-24)

Branch `convergence-shared-core` in both repositories. Every electrical claim below is
PASS, FAIL, UNKNOWN or NOT_APPLICABLE as observed in a real LTspice 26.0.0 run; nothing is
called verified unless the harness measured it against a cited datasheet row.
**Vendor-model results are in their own section and are not generated-model results.**

## 1. The failure, reproduced

The TPS54332DDA build from the previous session (fresh ZIP of `44a1d3c`, OpenCode Go,
`deepseek-v4.1-flash`, `reasoning_effort: max`, 2 turns) ended **UNKNOWN**: PASS 0, FAIL 0,
UNKNOWN 66, NOT_APPLICABLE 93. Re-running its probe deck today reproduces it
(`tps54332-baseline/rerun.json`): every operating-point method fails, then
`Convergence Failure: Time step too small; initial timepoint: trouble with node "en"`.

Root causes, read from the two candidates and their logs:

| Turn | Candidate defect | LTspice symptom |
|---|---|---|
| 1 (512 s, 100,075 tokens) | PWM latch on node `sw` whose only path to ground is `Rsw sw GND 1T`; state driven by a B source that reads `V(sw)`; nested hard `if()` everywhere | no operating point |
| 2 (356 s, 81,969 tokens) | `en_ok` written bare in five expressions | `No such parameter defined` at lines 43, 51, 56, 59, 75 |

The fixture leaves EN floating. The datasheet says "Float to enable", so the fixture is
valid and the model must bias EN itself. The harness fed the author only "27 probes
UNKNOWN". It never named the lines. Baseline cost: **1965 s wall time**, of which
extraction took 487 s, test planning about 608 s and two author turns 868 s. The author
prompt was 82,255 bytes.

## 2. What changed (both editions, one shared core)

| Change | Where |
|---|---|
| Static convergence lint: bare node names, undefined identifiers, self-reading sources, nodes without a ≤1 GΩ DC path, floatable pins the model does not bias | `authoring/convergence.py` (shared) |
| LTspice log diagnosis that quotes the rejected or non-convergent model lines, fed back as a "Targeted repair" section | `convergence.py` + `loop.py` |
| Deterministic syntax repair `name` → `V(name,GND)`, original bytes archived | `loop.py` |
| Bounded prompt (testable rows in full, ≤20 others as one line, pin prose clipped): 82 KB → 41 KB on the same frozen spec | `loop.py` |
| Per-stage reasoning effort: extraction `low`, planning `high`, authoring `high`, or `BOARDMODELER_AUTHOR_REASONING_EFFORT` (was `max` for all) | `backends.py`, `api_backend.py`, `providers/agent.py`, `test_planner.py` |
| Page relevance with provenance, OCR fallback or explicit gap, truncation refusal, pin-conflict checks | `documents/relevance.py`, `requirements/extract.py` (shared) |
| 41 core modules byte-identical across editions, enforced by manifest and test | `shared_core.json`, `tools/shared_core.py`, `tests/test_shared_core.py` |
| Startup performs no LTspice search (audit hook), no key reaches LTspice, credential scanner, fresh-ZIP checker | `tests/security/*`, `tools/scan_credentials.py`, `tools/fresh_zip_check.py` |

## 3. Generated-model results (AI-authored, judged by LTspice)

All builds ran from a clean worktree of the committed code with LTspice selected explicitly
in `data/config.json` (`doctor`: `source: config`, `searched: false`, smoke test PASS).

### LM358 (dual op amp): **PASS, 19 of 19 covered rows**, 1 author turn, 216 s end to end

`generated-models/lm358/`. Model sha256 is in `SHA256SUMS.txt`. Turn 1 used 9,048 prompt and
43,040 completion tokens at `high` effort. The rows are the app's reviewed LM358 table
(datasheet PDF page 9), not AI extraction, and every limit is frozen before authoring.
The table shows one value per row; channel 1 and channel 2 measured the same.

| Row | Measured | Datasheet limit | Verdict |
|---|---|---|---|
| VOS input offset voltage | 3.010 mV | ≤ 7 mV | PASS |
| IB input bias current | 20.0 nA | ≤ 250 nA | PASS |
| IOS input offset current | 2.00 nA | ≤ 50 nA | PASS |
| AOL open-loop gain | 98,875 V/V | ≥ 25,000 V/V and within ±10 % of the 100 V/mV typical (harness rule) | PASS |
| GBW | 700.1 kHz | 0.7 MHz typ ±10 % (harness tolerance) | PASS |
| Slew rate rise / fall | 0.2999 / 0.3001 V/µs | 0.3 V/µs typ ±10 % | PASS |
| Output swing from positive rail | 2.0 V | ≤ 3 V | PASS |
| Output swing, low | 5 mV | ≤ 20 mV | PASS |
| IQ quiescent current | 350 µA | ≤ 600 µA | PASS |

**Second LM358 run, built from the fresh GitHub ZIP of the pushed branch**
(`generated-models/lm358-fresh-github-zip/`): turn 1 (205 s) reached 17 PASS / 2 FAIL. Both
FAILs were AOL at 76,973 V/V: above the 25,000 minimum but outside the ±10 % band around
the 100 V/mV typical that the harness enforces. Turn 2 (150 s) repaired that from the
harness feedback. **PASS 19/19 after 2 turns, 356 s end to end**, 72k completion tokens in
total. Authoring is stochastic: the two runs produced different models, and both were judged
by the same frozen rows.

Scope: 25 °C nominal only; typ-only rows use the harness's ±10 % band. Temperature and
statistical behaviour are **NOT_APPLICABLE**, because none is modeled. There is no earlier
full-verification LM358 build to compare speed against: the local `real-1.5.0-lm358*`
runs were structural `sanity` builds, all UNKNOWN.

### TPS54332DDA (buck converter)

**Full cold build B** (datasheet → extraction → test planning → authoring → LTspice;
`generated-models/tps54332-full-cold/`, collected after turn 1 while turns 2–3 were still
running). The turn-1 model has 62 lines, 14 LTspice A-devices, no `if()` and no lint findings.
It converged in every fixture that finished. It was judged against the 14 rows this build's
planner froze:

| Row (datasheet p.5) | Measured | Limit | Verdict |
|---|---|---|---|
| Shutdown supply current, EN = 0 V, VIN = 12 V | 2.22 µA | ≤ 4 µA | **PASS** |
| Operating non-switching supply current, VSENSE = 0.85 V | 83.2 µA | ≤ 120 µA (typ 82 µA) | **PASS** |
| EN input current at threshold + 50 mV | −4.003 µA | typ −4 µA ±10 % | **PASS** |
| EN input current at threshold − 50 mV | −1.83 µA | typ −1 µA ±10 % | FAIL |
| Shutdown quiescent current (cover page, p.0) | 2.22 µA | typ 1 µA ±10 % | FAIL |
| Error-amplifier source/sink current | run killed at 120 s | typ ±7 µA | UNKNOWN (`run_timeout`) |
| Current limit, SS current, SS–VSENSE matching, max duty, IO max, switch-current limit, EN threshold, VREF | not run | cited limits | UNKNOWN (the harness defers the remaining fixtures after one timed-out run) |

This is the first AI-generated TPS54332DDA model in this project to pass any datasheet-cited
LTspice condition. It is **not verified as a whole**: 2 rows FAIL and 9 are UNKNOWN. The
regulation rows (VREF, current limit, duty cycle) are unmeasured, not passed. The two current
PASSes compare signed values against "≤" limits. Both also hold in magnitude (2.22 µA and
83.2 µA drawn), but the fixtures did not set `absolute`, so a large negative current would also
pass. This is recorded as an open harness issue below.

**Controlled reruns on the previous run's frozen spec** (same 27 fixtures and limits as the
failed baseline, so only authoring changed):

| Run | Effort | Turn 1 | Outcome |
|---|---|---|---|
| Baseline (previous session) | max | 512 s, 100,075 tokens | 27 UNKNOWN: no operating point |
| A (`tps54332-controlled-high-effort/`) | high | first API attempt used the whole 600 s turn budget while three builds shared the provider | UNKNOWN: no candidate written |
| A2 | low | 283 s, 51,027 tokens | 27 UNKNOWN: `Ven en_ok GND V=limit(...)` refused by LTspice ("Unknown parameter"). The targeted repair note quoted that line; the V=/I= normalizer added after this run now rewrites it before simulation.<br>Turn 2 was still running when this report was committed (no result claimed). |

Probe decks and logs for every fixture that ran are under each run's `probes/`.

## 4. Vendor-model results (NOT generated-model evidence)

TI's official `TPS54332_TRANS.LIB`, unmodified, re-run today (`vendor-reference/tps54332/`):
min V(out) at 8–9 ms = 2.51304655 V against the 2.42977 V lower bound at 12 V in, 2.5 Ω and
25 °C: **PASS**. TI's own reference deck gives 2.5095 V over 11–12 ms at 999.9 kHz: **PASS**.
No official LM358 model exists on this machine: **NOT_APPLICABLE** (searched locations in
`vendor-reference/lm358/result.json`). These runs say nothing about the AI-authored models.

## 5. Speed

Like-for-like comparisons are TPS54332DDA only. No full-verification LM358 baseline exists.

| Stage | Baseline (max effort everywhere) | After | Change |
|---|---|---|---|
| Extraction (35-page datasheet) | 487 s | 202 s (B, low effort); 214 s (final code, 17 of 35 pages sent, 3 image-only pages recorded as gaps) | −56 to −59 % |
| Test planning | ~608 s | 494 s (B) | −19 % |
| Author prompt | 82,255 B | 41,317 B (same frozen spec); 24,793 B (B) | −50 to −70 % |
| Author turn 1 | 512 s, 100k tokens, no operating point | 283 s, 51k tokens (A2, low); B turn 1 including its simulations 605 s, 63k completion tokens, **3 PASS** | −45 % time, −49 % tokens (A2) |
| Extraction → first judged model | ~1,607 s, 0 PASS | ~1,300 s, 3 PASS (B, under concurrent load) | faster, and the result is measurable |

Verification was not weakened. The same harness judged every run with frozen limits, and the
controlled reruns used the baseline's own fixtures. Timeouts and deferrals are UNKNOWN,
never PASS. The LM358 builds took 216 s (1 turn) and 356 s (2 turns) end to end.

## 6. Safety and packaging

| Check | Result |
|---|---|
| Startup and `doctor` with no LTspice path, audit hook over open/listdir/scandir/stat/exists/Popen/winreg | 0 LTspice accesses; doctor reports "not configured" (both editions) |
| Provider keys in the LTspice child environment (8 names, fake values) | absent (both editions) |
| `tools/scan_credentials.py` over both working trees and the evidence folder | 0 findings; values compared in memory, names only printed |
| Offline fresh ZIP (`git archive` of the branch): venv, pinned `requirements.txt`, startup, explicit `.op` run, credential scan | PASS / PASS (both editions) |
| Fresh GitHub ZIP of `convergence-shared-core` (download → extract → `py -3.14` venv → pinned install → startup without LTspice access → explicit `.op` → credential scan) | PASS 7/7 in both editions at `c6e032e` / `ed7b867` (ZIP sha256 `727d33de…`, `2184acfe…`); `shared_core.py --check` intact on GitHub's bytes; `.bob/rules*` and `.bobignore` present. Final commits: FINALZIP_PLACEHOLDER |
| Full suite, general (`-m "not gui and not network"`, LTspice enabled) | 1629 passed, 13 skipped, 0 failed (344 s) |
| Full suite, Bob (same selection) | 1215 passed, 148 skipped, 7 failed. All 7 fail identically at untouched `aebf457` (CLI doctor/run-tests fixtures); they are not caused by this change |
| Shared core | 41 files identical (`tools/shared_core.py --compare`) |

A `.venv` isolates Python packages and `.bobignore` hides files from Bob. Neither is an OS
sandbox (README, both editions). The optional `spicelib` extra probes default LTspice paths
when it is imported. The app imports it only after a path is configured, and it is not in
`requirements.txt`. `Install.exe` was **not** rebuilt on this branch: the tested install path
is the source ZIP + `.venv` + pinned `requirements.txt`.

## 7. What remains unsupported or open

- **Scanned datasheets:** OCR needs `tesseract`, which is not installed here. Scanned pages
  become explicit `extract_page_gap` findings (unit-tested) and are not extracted.
- **Microcontrollers, FPGAs, CPLDs and SoCs** are refused as BLOCKED by design; no analog
  probe can judge them.
- **Specs only in plots** (typical-characteristic curves) are not extracted; the pipeline is
  text-only.
- **Multi-package datasheets with conflicting pinouts** now stop with `pin_physical_conflict`
  instead of guessing.
- **Temperature and statistical claims** are NOT_APPLICABLE unless the model has explicit
  temperature dependence.
- **Open defect:** with a provider key configured, registering the LM358 PDF raised
  `NameError: _LENGTH_LIMIT` inside pypdf 6.19.0 `read_object` (3 of 3 runs). The same read
  succeeds with no key present. The LM358 build therefore used the app's own reviewed rows
  (`--requirements/--bindings`, produced by the keyless run). Root cause not found.
- **Open defect:** 7 pre-existing Bob CLI test failures (see section 6).
- **Open harness issue:** max-limit current rows are compared as signed values unless the
  planner sets `absolute`, so a large negative current would pass a "≤ 4 µA" row. Both current
  PASSes above also hold in magnitude. Separately, one timed-out fixture defers every
  remaining fixture of that turn to UNKNOWN, which cost 8 TPS54332 rows here.

## 8. Reproduce

    git clone -b convergence-shared-core https://github.com/BasamAhmed640/spice-maker
    py -3.14 -m venv .venv && .venv\Scripts\python -m pip install -r requirements.txt
    # SETUP (or data/config.json ltspice.path) selects LTspice explicitly
    set OPENCODE_API_KEY=...   (never committed)
    .venv\Scripts\python -m boardmodeler.cli model build --part LM358 ^
        --requirements inputs\lm358_requirements.json --bindings inputs\lm358_bindings.json ^
        --out models\C-lm358 --backend api --provider opencode_go --allow-remote --no-reinforce --iterations 3 --json
    .venv\Scripts\python tools\fresh_zip_check.py --repo BasamAhmed640/spice-maker --ref convergence-shared-core --ltspice <LTspice.exe>

Probe decks in `generated-models/*/probes/` `.include` the model by the absolute path used
at run time. Point the include at the copied `.lib` to re-run a deck by hand.
