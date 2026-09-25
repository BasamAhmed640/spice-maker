# Buck template slice: the two saved-run defects, corrected and measured (2026-09-25)

LTspice 26.0.0 selected explicitly; no provider request and no API key was used for any
result here. Every verdict below comes from a real LTspice run through the product harness
(`run_harness`) or, for the ground proof, through `simulation.ltspice.run_batch` with the
waveform read back from the `.raw`. Hashes of every committed file are in
[`SHA256SUMS.txt`](SHA256SUMS.txt); raw waveforms stay local and are identified by hash.

## 1. Logic gates on the wrong ground node

**Saved run.** The timed GUI build (`../2026-09-25-gui-build/REPORT.md`, model
`d9b0b5e3…`) measured **1.21198e-9 A** on its current-limit row (`B002_REQ_016`, required
4.2–6.5 A). Two things combined:

* The fixture loader renamed the planner's `GND` to `bm_fixture_ground` so that a ground-
  current sense node would stay distinct from node 0 — but this bench had no sense element,
  so after the rename **no element connected to node 0**: the whole bench floated.
* The model's LTspice A-device gates tied unused inputs to global node `0` while each gate's
  common (8th) terminal was the model's `GND` pin. LTspice ignores an unused input only on
  the gate's own common node; on another node it counts as logic low, so the AND gate that
  forms `run` stayed off even with `en_ok` and `uv_ok` both high.

**Proof** (`ground-proof/`, window 0.5–1 ms, `measured.json` has deck/log/raw hashes):

| Deck | Model gates | Fixture ground | PH rising edges | `run` | Peak switch current | LTspice |
|---|---|---|---|---|---|---|
| `a_saved.cir` (saved deck) | unused inputs on `0` | floating | **0** | 0 | 1.21198e-9 A (= saved value) | 1.6 s |
| `b_tied.cir` | unused inputs on `0` | tied to 0 by a 0 V source | **0** | 0 | 1.21198e-9 A | 1.4 s |
| `c_localgnd.cir` | unused inputs on `GND` | floating | 602 (voltages undefined: bench floats ~−1 V) | — | 5.3517 A | 4.6 s |
| `d_localgnd_tied.cir` | unused inputs on `GND` | tied to 0 | **500 = 1.000 MHz** | 1 | **5.3516 A** | 3.0 s |

`b` shows that tying the ground alone does not fix the model (the gate common is still a
different node from `0`); `c` shows the gate fix alone switches but leaves the bench
undefined. Both corrections are needed, and both are now in the product:

* `models/buck_switching.py`: every unused gate input sits on the model's `GND`.
* `authoring/convergence.py` lint `a_device_input_ground`: 10 findings on the saved model,
  0 on the corrected one — catches the same mistake in an AI-authored model.
* `authoring/circuit_probe.py`: `GND` is renamed only when the bench also names node 0
  (a real sense element); a bench with no connection to node 0 is refused as
  `fixture_floating_ground` instead of simulated.

## 2. Current limit measured before soft-start

**Saved run.** The bench read the peak from 0.5 ms to 1 ms. Its own cited rows (SS charge
2 µA, reference minimum 0.772 V) with its 10 nF SS capacitor need **3.86 ms** to reach the
reference. A pre-freeze rule for exactly this existed, but it looked for an inductor on PH;
the bench's 0.02 Ω sense resistor between PH and the inductor hid the power stage, so *every*
buck rule was skipped. Fixed in `authoring/test_planner.py`: PH is traced through current-
sense elements (≤ 1 Ω resistors, 0 V sources), and pins named SW/FB/AGND… are mapped
through the template's aliases before the rules run. The saved window is now rejected as
`buck_fixture_soft_start … (0.00386 s with 1e-08 F, 2e-06 A and 0.772 V)`; no limit changed.

**Corrected bench** (`authoring/buck_fixtures.py`, deterministic): 20 mΩ sense, load for
1.5× the highest cited limit, window from cited soft-start + 1 ms for 2 ms. It passes the
same pre-freeze rules an AI-planned bench must pass. Judged by the harness
(`current-limit/`):

| Part | Cited row | Model ILIM (origin) | Window | Measured peak | Verdict | Seed + bench | LTspice |
|---|---|---|---|---|---|---|---|
| TPS54332DDA | `B002_REQ_016`: 4.2 A min, 6.5 A max (p5) | 5.35 A (midpoint of that row) | 4.86–6.86 ms | **5.3513 A** | **PASS** | 0.012 s | 9.2 s |
| TPS54331 | `TPS54331_ILIM`: 3.5 A min, 5.8 A typ | 5.8 A (cited typical) | 4.86–6.86 ms | **5.8013 A** | **PASS** (≥ 3.5 A) | 0.013 s | 7.0 s |

What these PASSes mean: the switching model realises its current limit in a real switching
bench after soft-start. Because ILIM is itself taken from the same row, they are not an
independent check of the silicon. For TPS54331 the SS charge current is not in its cited
subset; the template default (2 µA) only places the measurement window and sets no limit.
The raw/log hashes are in each `*-proof.json`.

## 3. Reuse: parameters by statement, pins by role

* **Mapping.** On the saved GUI run's spec (generic ids `B002_REQ_nnn`) the old suffix-only
  mapping produced **0 cited parameters — all 23 were template defaults**
  (`mapping/before-template-parameters.json`). With statement + unit sources:
  **18 cited, 2 derived from cited bounds, 3 template defaults** (EN/UVLO hysteresis and base
  shutdown current have no source by design), in 10 ms (`mapping/after-…json`). A BOOT-UVLO
  row is excluded from the VIN UVLO parameter by its statement.
* **Pins.** `match_pins` maps BOOT/BST/CB, VIN/PVIN, EN, SS/SS_TR, VSENSE/FB, COMP/VC/ITH,
  GND/AGND, PH/SW/LX and ties PGND/PAD/EP/POWERPAD to GND. A pin the template does not model
  (RT, SYNC, PG, …) refuses the match with its name; a missing or duplicated role is named
  too. The rendered model keeps the part's own pin names.
* **Scope.** `peak_current_buck.json` now lists supported and unsupported behaviours
  (e.g. temperature and thermal shutdown, BOOT UVLO, RT/SYNC and PG are unsupported).

## 4. Nothing regressed on the frozen 19-row spec

The corrected template on the frozen TPS54332DDA spec: **12 PASS / 4 FAIL / 3 UNKNOWN in
109.8 s**, the same verdict for every row as the checkpoint (12/4/3 in 103.5 s)
(`regression/verdicts.json`). Those fixtures already grounded `GND` at node 0, so the gate
defect never showed there. The frozen FAILs and UNKNOWNs stay as measured.

## 5. Time, compared with the prior build

Prior GUI build (1,230 s total, from its saved files): extraction ~195 s, **AI test planning
~497 s for 21 fixtures**, seed + simulation ~236 s, **one AI repair turn 300 s (timed out,
nothing kept)**. Measured here: template seed 0.010 s; deterministic current-limit bench
0.012 s; its LTspice judgement 7–9 s, no repair needed for that row. The pipeline does not
yet use the deterministic bench to skip AI planning, so **no end-to-end build time was
re-measured and no build-level saving is claimed**.

## 6. Checks run

* `pytest tests/authoring tests/models tests/pipeline tests/ltspice tests/test_shared_core.py`
  with `LTSPICE_EXE`, both editions — results in `docs/STATUS.md`.
* `tools/shared_core.py --compare`: identical, 42 files (new: `authoring/buck_fixtures.py`).

## 7. Open

See `docs/TEMPLATE_CATALOG.md` (ranked next families and remaining buck work).
