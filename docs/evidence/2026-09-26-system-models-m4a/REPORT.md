# M4a: explicit average buck candidate and measured smoke controls

Date: 2026-09-26. Scope: the TPS54332DDA peak-current buck family in the
general and Bob editions. This is the first split of M4, not full M4
acceptance or a release checkpoint.

## Model and provenance

`seed_from_spec(..., mode="SW" | "AVG")` now selects an explicit electrical
mode. The default is `SW`; its freshly rendered library is byte-identical to
the M3 switching model (SHA-256
`21b3c7f1f9ed14b0d4247d291b7ba6342fecfdf19acdef59ca699c603fdb2ae2`).
Both modes have the same nine physical ports in the same order and the same
cited-device parameter origins. Unsupported modes fail before rendering. The
AVG model reuses the bounded error amplifier, SS charge, and COMP-derived peak
current command, then drives PH through a continuous averaged stage. Its
`L_EXT` is an instance-overridable **external bench inductor** value, not a
TPS54332 characteristic. The separate PowerPAD pin and external PCB-connection
rule remain intact.

The frozen requirements, bindings, and spec digest were checked before each
edition's smoke batch; the general and Bob runs used the same local copies.
Only the explicitly supplied LTspice executable was used. The code-built fixtures
reuse the M2 external passives and stimuli. No AI service or network is called.

## Real LTspice smoke results

The [general record](smoke-general.json) and [Bob record](smoke-bob.json)
contain every deck, model, log, and raw waveform SHA-256, executable identity,
source row, measurement window, and elapsed time. All eight edition/mode/case
runs returned readable waveforms; raw and operating-point files were hashed
and removed from the ignored `runs/` directories. `MEASURED` means real
simulation data were parsed. The `smoke` field applies deliberately synthetic
fixture settling/current guards, **not** a datasheet PASS.

| Edition | Fixture | SW wall / points | AVG wall / points | SW ÷ AVG | AVG smoke |
| --- | --- | ---: | ---: | ---: | --- |
| General | Startup, 8.21 ms | 19.119 s / 669,860 | 0.772 s / 8,377 | 24.766× | OK |
| General | Controlled COMP sweep | 39.086 s / 1,326,163 | 0.886 s / 13,991 | 44.115× | OK |
| Bob | Startup, 8.21 ms | 17.133 s / 669,860 | 0.707 s / 8,377 | 24.233× | OK |
| Bob | Controlled COMP sweep | 34.709 s / 1,326,163 | 0.798 s / 13,991 | 43.495× | OK |

The general AVG startup's final 0.5 ms was 2.517763 V against the M2
2.517895 V target, with 1.000116 A mean external inductor current and no
resolved output movement in that window. Its 10–90% rise was measured from
the raw waveform. The synthetic smoke criterion requires every final sample
within ±2% of that target and no more than 2% peak-to-peak movement; both
editions met it. The AVG COMP sweep had eight stable active points from
0.55–0.90 V. Each 50 mV step increased time-weighted mean inductor current
by at least 0.569 A; the high-COMP plateau was about 5.008 A against the
5.35 A nominal seed limit. This check is distinct from the full cited
current-sense gain and min/max current-limit judgments planned for M4b.

The [first AVG startup implementation](smoke-rejected-startup.json) had a
2.413–2.684 V final window and 412,727 raw points. It was not accepted: those
measurements violate the later declared ±2% settling guard.
The final AVG candidate removes the hard Eco gate while slowing and damping
its control response. The final run above is a
freshly rendered model and new LTspice execution, not a reused trace.

## Frozen controls and remaining work

The [TPS54332 19-row rerun](tps-regression.json) took 128.368 s and retained
12 PASS, 4 FAIL, 3 UNKNOWN, with zero changed verdicts. The default switching
library SHA above is unchanged. The [LM358 control](lm358-regression.json)
reran 19 probe cases covering 32 rows in 16.067 s: all PASS, zero changed.
The four historical TPS row failures and three UNKNOWNs remain as documented
in M3. M1's switching ripple FAIL and PH-edge UNKNOWN also remain open.

## Input-power isolation diagnostic

The smoke runner initially recorded ±1.44 kW source-power spikes and a
2.412 W final-window source-power mean, below the 2.518 W resistive load.
That alone could not establish where the anomaly came from. A one-run
diagnostic added a zero-volt shunt after the top-level input capacitor, with
the same accepted model and startup stimulus. The [general result](power-general.json)
and [Bob result](power-bob.json) include deck, model, log, and raw hashes and
final-window branch measurements. Both editions reproduced the same values:

| 7.71–8.21 ms measurement | Result |
| --- | ---: |
| VIN and DUT-side VIN | 12 V throughout |
| DUT input current through shunt | 0.258431–0.258437 A |
| DUT pin power | 3.101169–3.101238 W; 3.101203 W mean |
| Resistive load power | 2.517631 W mean |
| Source-side capacitor current | −120 to +120 A |
| Source-power spikes above +50 W / below −50 W | 250 / 250 episodes |
| DUT pin-power spikes above +50 W / below −50 W | 0 / 0 episodes |

The source and capacitor branch currents balance with the steady DUT draw
to within about 1.7 µA at worst. The spikes are confined to the ideal
source/capacitor branch while its saved voltage is constant; numerical
oscillation is an inference from those measurements, not a claimed silicon
effect. The measured DUT pin power exceeds load power in this settled
fixture, but full energy consistency across startup, faults, loads, and
corners remains **UNJUDGED** for M4b. The diagnostic raw files were hashed
and removed; no raw waveform is committed.

AVG electrical switching frequency, ripple, and PH edges are **UNKNOWN** by
design; its FSW is a control parameter, not an observed switching waveform.
TPS54332 has no PG pin, so PG checks are **NOT_APPLICABLE**. The source-power
trace is recorded with its sign convention but remains **UNJUDGED** as a
whole-fixture power metric; the shunt isolates the DUT draw in the one
startup fixture above. The AVG current sensor observes its
high-side branch, which can differ from external inductor current during
freewheeling. That approximation, min/max corners, UVLO/EN, IQ, load steps,
SW shape qualification, and the full synthetic Card A fault matrix belong to
M4b/M8. No real board was supplied.

## Integration

The board-layer command passed **269 tests / 11 expected skips** in the
general edition (152.64 s) and **269 / 11** in Bob (147.25 s). The wider
authoring/models/pipeline/LTspice/shared-core command passed **756** in the
general edition (186.42 s) and **625 / 7 edition-specific skips** in Bob
(191.11 s). Both editions passed Ruff, changed-file formatting, diff checks,
and the **44-file byte-identical shared-core comparison**. The new model,
focused tests, smoke runner, and plan also match byte for byte across the two
editions. No TI vendor library, raw waveform, user credential, or generated
model output is committed.
