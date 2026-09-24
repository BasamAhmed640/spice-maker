# Reference evidence — 2026-09-24

Reproducible LTspice evidence for the failed AI-generated TPS54332DDA model, plus vendor-model
reference checks. **AI-generated-model results and vendor-model results are kept in separate
sections and separate folders and must never be mixed or compared as if they were the same thing.**

- Simulator: LTspice 26.0.0 for Windows (`C:\Users\basam\AppData\Local\Programs\ADI\LTspice\LTspice.exe`), batch flag `-b`
  (confirmed by experiment with a trivial `.op` deck, and it is the flag `src/boardmodeler/simulation/ltspice.py` uses).
- Every run used `ltrun.py` (this folder): `subprocess.run([LTspice.exe, "-b", <absolute deck>], cwd=<deck dir>, timeout=...)`
  with a minimal environment containing only `SystemRoot, SYSTEMDRIVE, TEMP, TMP, USERPROFILE, LOCALAPPDATA, APPDATA`
  and `PATH=C:\Windows\System32` (key names are recorded, values never). No API keys are passed.
- Logs are decoded (UTF-16LE / UTF-8 / mixed detected per run) and saved as UTF-8 `*.log.txt`; the original
  `*.log` and every `*.raw` are deleted after measuring (the repo git-ignores `*.log`/`*.raw`). The sha256 of each
  original log is recorded next to the sha256 of its `.log.txt`.
- Nothing was downloaded. No git operations were run.

## Index

| Path | What | Result |
|---|---|---|
| `tps54332-baseline/TPS54332DDA.lib` | AI-generated model copied from the failed run (sha256 `45776014…88cd`) | — |
| `tps54332-baseline/deck.cir` | the run's only probe deck (`circuit_measurement-17397276a76f`), `.include` rewritten to `TPS54332DDA.lib` | — |
| `tps54332-baseline/deck.log.txt` | fresh LTspice log | — |
| `tps54332-baseline/rerun.json` | fresh re-run: hashes, elapsed time, exact failure lines | **FAIL** (failure reproduced); requirement **UNKNOWN** |
| `tps54332-baseline/baseline.json` | source-run statistics: status, counts, per-turn time/tokens, extraction time, prompt size, settings, wall time | **UNKNOWN** (0 PASS / 0 FAIL / 66 UNKNOWN / 93 NOT_APPLICABLE) |
| `vendor-reference/tps54332/tps54332_ti_reference.cir` + `.log.txt` | TI reference deck, re-run (vendor model) | **PASS** |
| `vendor-reference/tps54332/tps54332_ti_harness_8to9ms.cir` + `.log.txt` | previous session's harness deck + `.meas` for 8–9 ms (vendor model) | **PASS** |
| `vendor-reference/tps54332/result.json` | vendor TPS54332 measurements, bound and verdicts | **PASS** |
| `vendor-reference/lm358/result.json` | LM358 vendor-model search | **NOT_APPLICABLE** (model NOT_FOUND) |
| `ltrun.py` | the runner used for every simulation above | — |

## Generated-model runs after the change (see `REPORT.md`)

| Path | What |
|---|---|
| `REPORT.md` | the evidence report: failure analysis, changes, generated-model verdicts, vendor results (separate), speed, safety, unsupported types |
| `collect_build.py` | copies one finished build's model, harness report, per-turn timing/tokens, probe decks and logs (`.log.txt`) and SHA256 sums |
| `generated-models/<run>/summary.json` | status, counts, wall time, per-turn elapsed/tokens/outcomes, candidate hashes |
| `generated-models/<run>/probes/*/deck.cir` + `deck.log.txt` | every LTspice deck the harness ran and its log |

---

## Section A — AI-generated model (opencode) — TPS54332DDA

Source run (read-only): `C:\Users\basam\Documents\Codex\2026-09-24\spice-generator-update-for-both-versions\work\fresh-general-44a1d3c\spice-maker-44a1d3c9a041891f40e7351dc853fbbc347ff7a4\models\TPS54332DDA-opencode\`

### A1. Fresh re-run of the failing probe — **FAIL** (failure reproduced); requirement verdict **UNKNOWN**

- Lib `TPS54332DDA.lib` sha256 `45776014ce5cfeb67b6ab8d46fd3439aec9d8ca84143f19a42e21d20d73788cd`
  (identical to `build/model`, `build/publish-check` and candidate `0001`; candidate `0002` was `70434221…b18d`).
- Deck `deck.cir` sha256 `8601df5338ad7fa4b731b97ad4f66d0f58eeff6e850cd24d74603fcafe0e3cbd`
  (source deck sha256 `df6d2f08…d2cf`; only the `.include` line changed). Fixture: 5 V in, 1.6 Ω load, 25 °C, 10 ms transient.
- Log `deck.log.txt` sha256 `e4406dae3e7a3347ff7b50ed05afff08dc8457ee6693b70b90ee6422d7f9bf35` (original log was UTF-8; byte-identical).
- Elapsed: 1.178 s wall (LTspice-reported 0.678 s), return code 1.
- Exact failure lines:
  - `Convergence Failure:  Time step too small; initial timepoint: trouble with node "en"`
  - `Simulation Failed: Iteration limit reached`
  - preceded by: Direct Newton, Gmin stepping, source stepping and pseudo-transient all failed to find the operating point.
- No V(VOUT) data was produced, so the requirement (output voltage at the 0.8 V minimum set point) is **UNKNOWN**,
  matching the source harness.

### A2. Source-run baseline — overall status **UNKNOWN**

From `results.json`, `harness-report.json` and `build/candidates/*/*/result.json` (full detail in `baseline.json`):

| Item | Value |
|---|---|
| `results.json` status | `UNKNOWN` — "max_iterations=2 exhausted; still failing: circuit_measurement=UNKNOWN, …" |
| Counts | PASS 0 · FAIL 0 · UNKNOWN 66 · NOT_APPLICABLE 93 · BLOCKED 0 (159 rows) |
| `harness-report.json` | 27 outcomes, all UNKNOWN (model sha256 `45776014…88cd`) |
| Turn 1 (candidate 0001) | 512.27 s · prompt 24,490 · completion 75,585 · total 100,075 tokens · 27/27 UNKNOWN |
| Turn 2 (candidate 0002) | 355.57 s · prompt 25,981 · completion 55,988 · total 81,969 tokens · 27/27 UNKNOWN |
| Turns total | 867.85 s · prompt 50,471 · completion 131,573 · total 182,044 tokens |
| Extraction stage | 487 s (`extract` stage: "7/7 batches complete; 0 active; 487s elapsed") |
| `build/prompt.md` | 82,255 bytes |
| Request settings | provider `opencode_go` · max_iterations `2` · verification `full` (backend `api`, timeout_s 300) |
| Total wall time | **1964.9 s** = last − first file mtime over 59 files (00:47:50 datasheet PDF → 01:20:35 `results.json`, local time) |

---

## Section B — Vendor models (NOT the AI-generated model)

> Vendor-model reference only. These numbers say nothing about the AI-generated model above.
> Vendor model files are **not** copied into this repo; they are referenced by absolute path and sha256.

### B1. TI TPS54332 PSpice transient model (`TPS54332_TRANS`)

- Model: `C:\Users\basam\Documents\Codex\2026-09-24\spice-generator-update-for-both-versions\work\ti_verification\model\TPS54332_PSPICE_TRANS\TPS54332_TRANS.LIB`
  sha256 `c5e22f1d7930ffe598c793dd254dd568aba9cc453ad0c59e6f37c3d43b844091`
- Datasheet bound: settled V(out) ≥ 0.772 V × (1 + 10.2 kΩ / 4.75 kΩ) = **2.42977 V** (0.772 V minimum reference,
  datasheet PDF page 5, "Voltage reference 0.772 0.8 0.828 V"; divider from the datasheet 2.5 V design example),
  12 V in, 2.5 Ω load (~1 A).
- Both decks were run from copies in `%TEMP%\ltev0924\` (a short path: from the session scratch folder, whose deck
  path exceeds 260 characters, LTspice exited after 0.26 s without writing any log).

**Run A — TI reference deck `tps54332_ti_reference.cir` — PASS**

- Original: `…\work\ti_verification\tps54332_ti_reference.cir` (sha256 `4971e161bcb34d7b632f5a467f2437c38965283ce5e76afc64d351c8ac7b211c`), include
  `.include "model/TPS54332_PSPICE_TRANS/TPS54332_TRANS.LIB"`; the evidence copy (sha256 `0af1d15d591e51bae65b882554990b6c97d128594591daaa26fd9c044d7c8a2f`) differs only in
  that the include is the absolute path above. No `.temp` line → LTspice default 27 °C. Measures 10–12 ms.
- Elapsed 419.8 s wall (LTspice-reported 415.679 seconds.); log `tps54332_ti_reference.log.txt` sha256 `01c6ca2939cb7a117da947c4a10cd496625d1248ecb80ebff57b1356c12d2f5f` (original encoding utf-8).
- Measured: `vout_avg: AVG(V(out) )=2.51532354769 FROM 0.01 TO 0.012`; `vout_max: MAX(V(out) )=2.52204871178 FROM 0.011 TO 0.012`; `vout_min: MIN(V(out) )=2.5095102787 FROM 0.011 TO 0.012`; `ph_first: V(ph)=6  AT 0.0110000317901`; `ph_second: V(ph)=6  AT 0.0110010319065`; `fsw: 1/(ph_second-ph_first)=999883.591206`
- Check: vout_min (11–12 ms) = **2.509510279 V** vs ≥ 2.42977 V → **PASS** (margin 0.07974 V).
- Versus the older log from the previous session: vout_avg 2.51532354769 → 2.51532354769 (Δ 0); vout_max 2.52204871178 → 2.52204871178 (Δ 0); vout_min 2.5095102787 → 2.5095102787 (Δ 0); ph_first 0.0110000317901 → 0.0110000317901 (Δ 0); ph_second 0.0110010319065 → 0.0110010319065 (Δ 0); fsw 999883.591206 → 999883.591206 (Δ 0)

**Run B — previous session's harness deck, min V(out) over 8–9 ms at 25 °C — PASS**

- Source: `…\ti_verification\fresh_product_96d52a5\spice-maker-96d52a5\models\TPS54332DDA_official\harness\probes\circuit_measurement\deck.cir`
  (sha256 `b4809fc78c54dadfd12e36eb97ad553f95d354cb14649fbfb35e4c2ce37df1bb`); evidence copy `tps54332_ti_harness_8to9ms.cir` (sha256 `b18156fbcb344be6edc5b5d03668a836036ed397c15421b0510bf4720fe336d8`) points the include at the
  byte-identical TI LIB above and appends `.meas tran vout_min_8_9 MIN V(out) FROM=8m TO=9m` (+ `vout_avg_8_9`).
  Circuit, `.temp 25`, `.tran 0 9m 0 100n` unchanged.
- Elapsed 355.8 s wall (LTspice-reported 354.931 seconds.); log `tps54332_ti_harness_8to9ms.log.txt` sha256 `9f41e23698a4673f254d03e778d8a394a7ec439b98f884a0e58dcecb2f44fe85` (original encoding utf-8).
- Measured: `vout_min_8_9: MIN(V(out) )=2.51304654898 FROM 0.008 TO 0.009`; `vout_avg_8_9: AVG(V(out) )=2.51534033463 FROM 0.008 TO 0.009`
- Check: min V(out) 8–9 ms = **2.513046549 V** vs ≥ 2.42977 V → **PASS** (margin 0.08328 V).
- Previous session: 2.5130465489837532 V → **CONFIRMED** (fresh 2.51304654898 V, difference -3.75e-12 V; the log prints 12 significant digits)

### B2. LM358 vendor model — **NOT_APPLICABLE** (NOT_FOUND)

No official LM358 SPICE model exists locally, and nothing was downloaded. Searched (names, plus ASCII and UTF-16LE
content for "lm358" in model/netlist files under 5 MB): `%LOCALAPPDATA%\LTspice\lib\sub` (4,920 files),
`Documents\LTspice` (15,990), `%LOCALAPPDATA%\Programs\ADI\LTspice` (748), `lib.zip` (11,854 entries) and `examples.zip`
(4,282 entries), both read in memory with `zipfile`; `spice-maker\fixtures\**\originals\` (no such directory exists);
`src\boardmodeler\authoring\lm358_reference.py` (a datasheet-extraction recipe, no model path). The only LM358
file found is `fixtures\opamp\ti-lm358-rev-ab-page9.txt`, which is datasheet text. Details: `vendor-reference/lm358/result.json`.

---

## How to reproduce (PowerShell)

```powershell
$lt  = 'C:\Users\basam\AppData\Local\Programs\ADI\LTspice\LTspice.exe'
$ev  = 'C:\Users\basam\src\spice-maker\docs\evidence\2026-09-24'

# A1: AI-generated model failing probe (in place; writes deck.log.txt, deletes deck.log/deck.raw)
python "$ev\ltrun.py" "$ev\tps54332-baseline\deck.cir" 120 "$env:TEMP\baseline_run.json"
#   plain LTspice equivalent (leaves git-ignored deck.log/deck.raw next to the deck):
& $lt -b "$ev\tps54332-baseline\deck.cir"

# B1: vendor decks, run from a SHORT temp path (LTspice fails silently on >260-char deck paths)
$t = "$env:TEMP\ltev0924"
New-Item -ItemType Directory -Force "$t\vendorA", "$t\vendorB" | Out-Null
Copy-Item "$ev\vendor-reference\tps54332\tps54332_ti_reference.cir"      "$t\vendorA\"
Copy-Item "$ev\vendor-reference\tps54332\tps54332_ti_harness_8to9ms.cir" "$t\vendorB\"
python "$ev\ltrun.py" "$t\vendorA\tps54332_ti_reference.cir"      900 "$t\vendorA\run.json"   # ~5-8 min, ~685 MB .raw (auto-deleted)
python "$ev\ltrun.py" "$t\vendorB\tps54332_ti_harness_8to9ms.cir" 900 "$t\vendorB\run.json"   # ~4-7 min
Select-String -Path "$t\vendorA\*.log.txt", "$t\vendorB\*.log.txt" -Pattern '^vout_'
```

Section A2 numbers are read from the source run folder, e.g.:

```powershell
$run = 'C:\Users\basam\Documents\Codex\2026-09-24\spice-generator-update-for-both-versions\work\fresh-general-44a1d3c\spice-maker-44a1d3c9a041891f40e7351dc853fbbc347ff7a4\models\TPS54332DDA-opencode'
(Get-Content "$run\results.json" -Raw | ConvertFrom-Json) | Select-Object status, counts
Get-ChildItem "$run\build\candidates\*\*\result.json" | ForEach-Object { Get-Content $_ -Raw | ConvertFrom-Json | Select-Object turn, elapsed_s, usage }
$m = Get-ChildItem -Recurse -File $run | Measure-Object LastWriteTime -Minimum -Maximum; ($m.Maximum - $m.Minimum).TotalSeconds
```
