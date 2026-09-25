# Peak-current buck template: TPS54332DDA, frozen-spec judgment

The deterministic `peak_current_buck_v1` candidate was generated from the frozen
TPS54332DDA `SpecSet` and run through the product's LTspice harness with **zero AI
author turns**. It produced a convergent, simulated model. The 19 frozen rows
finished in **103.5 seconds: 12 PASS, 4 FAIL, 3 UNKNOWN**. The earlier hand-written
prototype recorded 8 PASS, 8 FAIL, 3 UNKNOWN in 87.5 seconds; the earlier AI-only
build took 1,957 seconds and delivered no model. These are different runs and
their elapsed times are observational, not a performance guarantee.

The exact model SHA-256 is
`d9b0b5e3d4729168933f1a834e49d4e25588dfb6239cf420047c00f999d913e2`.
The frozen spec digest is
`499ed2442781bd4cad22041e7cb028bb6af9ec3df7bd34fe053750b9782b14d7`.
[`row-verdicts.json`](row-verdicts.json) records the per-row result, source page,
measurement, reason, local deck directory and SHA-256 hashes of each deck, log
and raw waveform. The frozen spec and raw waveforms are local build artifacts
under `models/T1-tps54332/`, which is git-ignored; their hashes are recorded
here, but those bytes are not shipped in this repository.

| Frozen row | Verdict | Measured or reason |
| --- | --- | --- |
| `B001_FEAT_VOUT_MIN` | FAIL | 0.799848 V; strict minimum 0.8 V |
| `B001_FEAT_FSW` | PASS | 1,000,000 Hz from consecutive PH edges |
| `B001_FEAT_IQ_SHUTDOWN` | PASS | supply-current magnitude 1.00121 µA |
| `B002_TPS54332DDA_UVLO_VIN` | PASS | first PH edge at VIN 3.52001 V |
| `B002_TPS54332DDA_ISHDN` | PASS | supply-current magnitude 1.00121 µA, below 4 µA max |
| `B002_TPS54332DDA_IOP_NONSW` | PASS | supply-current magnitude 83.0071 µA, below 120 µA max |
| `B002_TPS54332DDA_EN_TH` | PASS | first PH edge at EN 1.264 V |
| `B002_TPS54332DDA_VREF` | UNKNOWN | `recipe_not_settled` in its 5–10 ms window |
| `B002_TPS54332DDA_EA_ISOURCE_SINK` | PASS | 6.98999 µA |
| `B002_TPS54332DDA_SW_CURRENT_TO_COMP` | FAIL | 9 A/V against 12 A/V typical |
| `B002_TPS54332DDA_ILIM` | FAIL | peak inductor current 10.6092 A above 6.5 A max |
| `B002_TPS54332DDA_SS_CHARGE` | PASS | 1.9992 µA |
| `B002_TPS54332DDA_SS_VSENSE_MATCH` | FAIL | VSENSE − SS = −0.4 V |
| `B002_TPS54332DDA_MIN_ON_TIME` | UNKNOWN | no requested PH transition in the window |
| `B003_REQ_TPS54332_OUTPUT_CURRENT_MAX` | PASS | 3.49933 A at its declared operating point |
| `B003_REQ_TPS54332_SWITCH_CURRENT_LIMIT_MIN` | PASS | 5.35139 A above 4.2 A min |
| `B003_REQ_TPS54332_EN_THRESHOLD` | PASS | first PH edge at EN 1.264 V |
| `B003_REQ_TPS54332_ECOMODE_COMP_0P5V` | PASS | COMP 0.465924 V within the typical band |
| `B004_REQ_TPS54332DDA_DUTY_MAX` | UNKNOWN | `recipe_not_settled` in its 2–3 ms window |

The model implements the cited 0.6/0.4/0.2 V VSENSE frequency-foldback
thresholds with synchronized clock periods. Four additional real LTspice smoke
decks held VSENSE at 0.8, 0.5, 0.3 and 0.1 V. They measured 80, 40, 20 and 10
PH rising edges, respectively, over 80 µs: 1 MHz, 500 kHz, 250 kHz and 125
kHz. All four simulator processes exited 0. This checks the topology's clock
selection; it is separate from the frozen datasheet-row verdicts above.

The remaining outcomes are not silently promoted. The 0.799848 V result is
below an exact 0.8 V minimum, so it stays FAIL. The VREF output oscillates
around 0.8 V with a decaying ripple in the fixture's 5–10 ms window and did
not meet the harness's settled criterion. The COMP-to-ground 10 kΩ shunt in
the ILIM, SS-match and minimum-on-time fixtures permits at most 70 mV from
the specified ±7 µA error amplifier, below the 0.5 V Eco-mode COMP clamp;
these fixtures cannot exercise the stated switching condition faithfully.
The switch-current-to-COMP fixture forces COMP to 0.2 V, below the same
0.5 V Eco-mode clamp, while asking for a positive switch-current gain. The
maximum-duty fixture samples 2–3 ms even though its 10 nF SS capacitor at
2 µA has not reached the minimum 0.772 V reference until about 3.86 ms.
Those are reasons to reject similar *future* fixtures before freezing them,
not permission to rewrite this spec or convert any FAIL/UNKNOWN into PASS.

The JSON contract and each generated `template-parameters.json` distinguish
`cited_row`, `derived_from_bounds`, and `template_default`. A failed citation
is excluded from `cited_row`. The template has no electrical verdict until the
normal LTspice harness runs, and its model card must retain every measured
FAIL, UNKNOWN and untestable row.
