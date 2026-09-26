# M1: TPS54332DDA switching-model comparison with TI

Status: all ten side-by-side LTspice cases measured. Earlier vendor numbers in
the 2026-09-24 reference evidence are context, not M1 measurements.

## Sources and fixed method

The [TI TPS54332 datasheet](https://www.ti.com/lit/ds/symlink/tps54332.pdf)
specifies current-sense transconductance of 12 A/V (typical), peak current limit
of 4.2–6.5 A, and 0.8–1.2 MHz nominal switching frequency (electrical
characteristics, PDF p. 6). Its 2.5 V application is guidance, not a guaranteed
output-ripple limit. The reference circuit here is the existing **TI-derived**
deck in `docs/evidence/2026-09-24/vendor-reference/tps54332/`, with the same
passive components on both candidate models. The vendor PSpice transient library
is local only and is not copied into this repository. The deck has a simplified
custom catch diode and omits the 1 MOhm SS pulldown in TI Figure 8-1, so it is
TI-derived rather than an exact copy of that figure.

Our candidate is rendered again from the frozen TPS54332DDA requirements and
bindings using `load_tps54320_spec` and `seed_from_spec`. The saved GUI `g32`
model is excluded. The explicit LTspice executable runs through
`simulation.ltspice.run_batch`, which limits the child environment, serializes
simulator runs and enforces a timeout. No inference provider is used.

Source identity before the runs: frozen requirements SHA-256
`3884fd76976e071cd2ea81d5db64cb1bde17aecbaccc8f87399e7448646cc060`,
bindings `1b3d45e8977948ce2ed7f1079db2aaa598b543cd65a83b697af8455d6345ad57`,
TI reference-deck source `0af1d15d591e51bae65b882554990b6c97d128594591daaa26fd9c044d7c8a2f`,
and TI library `c5e22f1d7930ffe598c793dd254dd568aba9cc453ad0c59e6f37c3d43b844091`.
The fresh render has nine ports in package order:
`BOOT VIN EN SS VSENSE COMP GND PH POWERPAD`; its frozen-spec digest is
`499ed2442781bd4cad22041e7cb028bb6af9ec3df7bd34fe053750b9782b14d7`.
Mapped values include VREF 0.8 V, FSW 1 MHz, ILIM 5.35 A (midpoint of the
4.2–6.5 A cited range), GMCS 12 A/V, VECO 0.5 V and ISS 2 µA. These mappings
are inputs, not measured performance.

The COMP bench samples 0.50 V and then 50 mV steps above it, holding VSENSE at
0.8 V. The 0.50 V threshold and points at the measured current-limit plateau
are excluded from the linear gain fit; at least three active unsaturated points
are required. Every current sample and the fit range will be reported. The
resistive overload and 10 mOhm short are applied after soft-start. A separate
resistive step changes the load from about 1 A to about 3 A. Steady windows
begin after the 15 nF soft-start capacitor has had about 6 ms to charge. The
decks use VIN = 12 V and `.temp 25`, matching the switching-frequency row's
conditions. Startup needs a steadily rising envelope and a comparison of rise
time and overshoot; rise time alone cannot establish a plausible shape.

Declared comparison bands: gain 10.8–13.2 A/V (the 12 A/V typical with the
project's ±10% rule); peak current limit 4.2–6.5 A; nominal frequency
0.8–1.2 MHz. TI Table 7-2 divides frequency by eight when VSENSE is below
0.2 V. Applying that ratio to the separate nominal-frequency band gives a
derived 100–150 kHz guide, not a guaranteed short-circuit limit; the hard-short
PH frequency will be judged against the measured TI result and its VSENSE band.
Ripple, PH rise/fall times,
load-step dip/recovery, and startup
shape use the newly measured TI run as the reference, with the project's
0.5–2× ballpark band where a positive scalar comparison is meaningful.
Missing or unresolved measurements are UNKNOWN, never PASS. The TI vendor
library itself ties PowerPAD internally to ground; this side-by-side cannot
validate the required external pad connection, which M3 will address.

## Observed comparison

All ten corrected decks completed with an LTspice log and raw waveform. The
measured values below are taken from the ignored local `runs/m1` JSON records,
whose source/model hashes match the current manifest. `PASS` and `FAIL` refer
to the stated M1 check only; they do not confer a system-verified model.

| Check and measurement window | Ours | TI transient model | Reference or band | M1 verdict |
| --- | ---: | ---: | --- | --- |
| Reference output mean, 8.6–9.6 ms | 2.51748 V | 2.51501 V | Divider with 0.8 V typical reference targets 2.51789 V | PASS for this output setpoint; not a direct VREF pin measurement |
| Nominal PH frequency, 8.6–9.6 ms | 1.000000 MHz | 0.999998 MHz | Datasheet 0.8–1.2 MHz at 12 V, 25 °C | PASS |
| Gain, acquisition script's original 0.55–0.90 V fit | 9.363 A/V; 0.492 A maximum residual | 11.684 A/V; 0.050 A residual | 10.8–13.2 A/V (12 A/V typical ±10%) | **FAIL for ours**; the measured interval is not linear |
| Gain, separately labeled post-acquisition linear-segment fit | 11.988 A/V over 0.65–0.90 V (6 points) | 11.474 A/V over 0.55–1.05 V (11 points) | 10.8–13.2 A/V | PASS for the selected local linear segments; overall gain evidence is **MIXED** |
| Resistive-overload median cycle peak, 8.6–9.6 ms | 5.355 A | 6.703 A | 4.2–6.5 A current-limit threshold; ours/TI = 0.799× | Ours inside cited band; TI dynamic inductor peak above 6.5 A (threshold caveat below) |
| Hard-short median cycle peak, 8.6–9.6 ms | 5.356 A | 6.734 A | 4.2–6.5 A threshold; ours/TI = 0.795× | Ours inside cited band; TI dynamic peak above 6.5 A (same caveat) |
| Resistive-overload average inductor current | 4.067 A | 5.788 A | 0.5–2× TI = 2.894–11.577 A | PASS ballpark (0.703×) |
| Hard-short average inductor current | 4.075 A | 5.340 A | 0.5–2× TI = 2.670–10.681 A | PASS ballpark (0.763×) |
| PH frequency under resistive overload | 250.000 kHz | 499.999 kHz | Each model's own VSENSE band in TI Table 7-2; 0.5–2× TI | PASS at the 0.5× boundary; outputs enter different foldback bands |
| PH frequency under 10 mΩ short | 125.000 kHz | 125.000 kHz | VSENSE <0.2 V ⇒ nominal FSW/8; derived 100–150 kHz guide | PASS for measured /8 foldback |
| Output ripple peak to peak, 8.6–9.6 ms | **1.805 mV** | **13.110 mV** | 0.5–2× TI = 6.555–26.221 mV | **FAIL**: ours is 0.138× TI |
| Startup output 10–90% rise | 4.80501 ms | 4.80432 ms | 0.5–2× TI = 2.402–9.609 ms | PASS for rise time |
| 1 A→3 A resistive-step output dip | 62.855 mV | 79.514 mV | 0.5–2× TI = 39.757–159.028 mV | PASS ballpark (0.790×) |
| Step recovery into ±2% of final output | 11.254 µs | 10.288 µs | Ratio 1.094× | PASS for this **loose numerical band only**; full physical recovery UNKNOWN |
| PH 10–90% rising edge | unresolved (0 qualifying transitions) | 0.0325 ns (330 qualifying transitions) | TI result is itself nearly ideal | **UNKNOWN** physical edge realism |
| PH 90–10% falling edge | 0.883 ns | 0.894 ns | Numeric ratio 0.988× | **UNKNOWN** physical edge realism at sub-ns scale |

The current-limit row is a threshold specification, while this bench records a
cycle's **inductor-current peak** including ramp and any dynamic overshoot. The
TI transient model's 6.703/6.734 A peaks therefore do not prove that the
silicon violates its 6.5 A threshold maximum. M1 used one nominal model setting;
it did not exercise the min/max limit corners required by the eventual system
model acceptance rule. Likewise, the reference output is only an indirect
setpoint check, not a direct measurement of the 0.772–0.828 V VREF row.

### COMP sweep: all acquired points and both fit rules

The gain deck retains every TI-reference passive and adds the same 0.625 Ω
resistive shunt to each DUT. It forces VSENSE to 0.8 V and holds COMP for
500 µs at each 50 mV level. Each active plateau had 144 measured cycles in
the late window and passed the script's early/late peak-stability check. The
0.50 V pulse-skip threshold has no observed cycles and is never fitted.

| COMP (V) | Our peak (A) | TI peak (A) | Secondary fit selection |
| ---: | ---: | ---: | --- |
| 0.50 | no pulses | no pulses | threshold excluded |
| 0.55 | 1.7769 | 0.9338 | TI only |
| 0.60 | 1.7769 | 1.5824 | TI only |
| 0.65 | 1.8074 | 2.1900 | both |
| 0.70 | 2.4067 | 2.7481 | both |
| 0.75 | 3.0060 | 3.3317 | both |
| 0.80 | 3.6054 | 3.9138 | both |
| 0.85 | 4.2046 | 4.4694 | both |
| 0.90 | 4.8045 | 5.0597 | both |
| 0.95 | 5.3547 | 5.6119 | TI only |
| 1.00 | 5.3548 | 6.1797 | TI only |
| 1.05 | 5.3548 | 6.6717 | TI only |
| 1.10 | 5.3548 | 6.6717 | neither |

The acquisition script predeclared a fit across stable, apparently unsaturated
0.55–0.90 V points. Our 0.55 and 0.60 V peaks are identical despite the COMP
increase, and the 0.65 V point barely moves. Its eight-point 9.363 A/V fit is
outside the 10.8–13.2 A/V band and has a 0.492 A maximum residual: **FAIL**.
This measured low-COMP floor remains an open model-behavior defect. It is not
erased by the later fit.

After seeing that anomaly in our acquisition record, a separate deterministic
segment rule was written **before inspecting TI's fit** and applied to both
saved waveforms. It excludes COMP ≤0.50 V, unstable or <10-cycle points, a
three-point high-current plateau, and segments whose adjacent slopes deviate
more than ±20% from their median or whose regression residual exceeds
max(0.10 A, 5% of current span). It selects the longest qualifying contiguous
segment. Our six-point 0.65–0.90 V segment gives **11.9878 A/V**, intercept
−5.9847 A, maximum residual 0.00025 A. TI's eleven-point 0.55–1.05 V segment
gives **11.4737 A/V**, intercept −5.2978 A, maximum residual 0.0789 A. Both
local slopes are inside 10.8–13.2 A/V; this is a secondary local PASS, not a
claim that the whole active range is linear. Its fit script SHA-256 is
`378c1b5aa61f55ab8b22bf0bb7da2927b77294982be94edfe5d8d8382fb29cc1`;
the two fit JSONs include hashes of their gain, overload and short inputs.

Our 0.95–1.05 V high-current tail sits at 5.3547–5.3548 A and independently
matches the 5.355 A overload and 5.356 A short peaks. TI's 1.05 and 1.10 V
points both read 6.672 A, close to its 6.703/6.734 A fault peaks. That is
corroborating evidence of a high-current cap, but TI has only **two** flat gain
points: the declared three-point plateau detector reports `NOT_OBSERVED`.
Neither fit script automatically declares a current-limit verdict.

### Fault, startup and transient shape details

The overload and hard-short resistors switch in at 8 ms, after the 15 nF SS
capacitor's nominal 6 ms charge time. The 8.6–9.6 ms measurements are therefore
post-startup. Overload VSENSE stays 0.345–0.350 V for ours (the /4 band) and
0.483–0.505 V for TI (the /2 band); their 250/500 kHz PH frequencies agree
with the [datasheet's Table 7-2](https://www.ti.com/lit/ds/symlink/tps54332.pdf).
The short drives VSENSE below 0.2 V throughout the saved window: maxima are
0.0163 V (ours) and 0.0211 V (TI), with 125 kHz PH frequency in both.

Startup from the 12 V input ramp is steady at the 100 µs envelope scale:
both have zero downward 100 µs bins larger than 2% of final VOUT. The 1, 3,
5 and 6 ms output values are 0.460/1.299/2.139/2.518 V for ours and
0.425/1.268/2.105/2.515 V for TI. Reported maxima exceed the final means
by 2.10 mV and 6.66 mV respectively, comparable to each model's switching
ripple; those numbers cannot establish a meaningful startup overshoot ratio.
The early post-90%-rise peak-to-peak window still contains the tail of the
monotonic rise, so it cannot establish ringing decay. Startup **rise time and
envelope pass**, while overshoot/ringing are **UNKNOWN** under this bench.

The load step switches a 1.25 Ω resistor in parallel with the base 2.5 Ω
resistor at 8 ms. Pre-step output implies 1.007→3.021 A for ours and
1.006→3.018 A for TI. Their final 9.2–9.6 ms output windows are stable by
the script's two-half mean check. The recorded 11.254/10.288 µs recovery
times only mean the output re-entered ±2% of final voltage, a ~50 mV band
against 63/80 mV dips. Thus **full dip recovery time remains UNKNOWN**.
A secondary 2.1 µs-sampled derived trace suggests a later return near the
pre-step level, but its decimation and threshold choice cannot establish a
formal recovery time. Early/late peak-to-peak windows are 2.69/1.85 mV (ours) and
8.96/9.89 mV (TI); they are dominated by switching ripple and do not
prove or refute damping of load-step ringing.

PH edge timing is also unresolved as a realism check. With a 50 ns maximum
transient step, our rising edge has zero transitions meeting the analyzer's
interior-sample rule; TI's nominal 0.0325 ns rising edge is much shorter than
that step. The measured ~0.9 ns falling edges are numerically close, but a
sub-ns agreement between these waveforms does not prove realistic board
edges. No edge or ringing claim is promoted to PASS.

### Run identity, parity, timing and artifact hashes

The same non-model circuit text was hashed before inserting either `XU1` and
its `.include`; each pair has an identical common-circuit SHA-256:

| Case | Shared circuit SHA-256 |
| --- | --- |
| Reference | `1da41ef90642e4e3666e0f076340882b9abe07f69cce6211caf7d58716bab42b` |
| Gain | `dc94f31eb084cdb4c9b720d97f2529139abd219b2b1fee1a9befdae177e4c910` |
| Overload | `eab11bca4a4c6ccdb4a2211d89ae9bcf8bf831a9909e3f5f8298aaa000d65913` |
| Short | `774b4abc4913841e8f04152ebd6cd06a398f7016d65b38d62a50b0f808061c9c` |
| Load step | `0dc39e1354bd52a28b65e1403452bac62e1f8832210dc36784ff5ab3bc18b0ed` |

All ten run JSONs record the same fresh-model SHA-256
`881ed258fbc34ac34e62769d6d56eb84aa8e8460bcfccbf0249a8fd6ae919fb8`
for ours, the TI library SHA-256 shown above, and acquisition-script SHA-256
`5c4df9cc3c3a8bf6f641620af08db93407696cfd72b5f66f60f6d48bd8881895`.
Each run exited 0 with a log and raw waveform. Cumulative LTspice wall time
was **140.579 s ours + 1,085.376 s TI = 1,225.955 s (20.43 min)**. The
table gives full SHA-256 for deck, log, raw and operating-point raw. Main raw
files were hashed after measurement then deleted; these hashes identify the
observed local artifacts, not files published in Git. Decks/logs and the
small startup/step CSV traces remain in ignored local `runs/m1/`.

| Case | Model | LTspice (s) | Deck SHA-256 | Log SHA-256 | Raw SHA-256 | OP raw SHA-256 |
| --- | --- | ---: | --- | --- | --- | --- |
| Reference | ours | 23.108 | `758b9bb389d732841d66a98f7852462517efde78c7b46a8251629a58739e70b3` | `2ed3c59cf3a7ecd97da7d2a2dfc5214355b541f92c24b775b59e96c08642f27d` | `8e0602db429fdc712db74671efb4957ee480e3a33e1b310ecbb2b37b47b0e438` | `b7d6483fe4093406bbbc69e954d3fe95cb8eb999b87a7bdb7c31cb7b5edde6aa` |
| Reference | TI | 231.396 | `632ecfe7230b406d3c35812bc262a5d361809f8795b696a0856da418af4e1834` | `51c548bd1dc3d8697e20072ac5e4df660c61962ecaecc3ea443dc5d6bd947e38` | `b1c0a49593756560715bc59e6b78dfde477139b8721a65348445809af3671228` | `59ae87276e4d897d3f9b6cd04348b9715633237661cf702a58dbb922b03935f5` |
| Gain | ours | 32.425 | `53a269037b4f92ac32e924f5ed2ed3220314f55d7ed7e0cdd7ed9ceedf008c17` | `7b1d67039061a1ac20370c8aa24b624829d983b38d3b22dfa1149fd2f4d718ed` | `e5cb7cc4e34cbfae68f42fdb4cb38604ed975b5383af3cee49a5c4907ff935fa` | `7084926dbeb30345ded0a58258954d41223fa87ba6378aa43aac1c602553b781` |
| Gain | TI | 265.707 | `168eb4dac174359fd3df7d442a1f0791de8e3eaa95b9cd85642d2c3ba7f26c74` | `8b333f9b964dd0a4cf1d339a580f47532ddd118ea2551de3560eb7b5d18eb81f` | `232473b7f147c3e967446bae0e5edc18135af489a80a71dd4de040fc036b7b00` | `ace849fb9c681e1bc3e2dbf9fff3059d837c7e7d1ec6304e605de9ecedd0bd01` |
| Overload | ours | 29.235 | `62d5233a1c9d66b116b63f01ee35a314890616934809f21b5e038d42253bd611` | `85a493a7cb060bd37b89e0d2e375d043a61b62abdf8cdfd962a47cc9dd565528` | `8ab238317bda434a756fdf74b8f34a6111ba3ec06dc831355b0d46b58071dc45` | `d0593527e2ca8721db607e94254738388578d8c7cd23f1c1b7cfeb48c7e95288` |
| Overload | TI | 166.545 | `29b315ffe6f256042cefc0d7a8be0087324287f5edb6cc7a54036c11c4c84bda` | `81eacd2c7da3c1193221a7d748bb17f6e3979bdc78e680c941f9e47f652c917f` | `48b57c51d120d7b59b6a8723bb49d2a59c5f6b212f17ec0d6765ab7dbf8ca217` | `858701d70e8798494666a349ca88bff8cb048614c6d935c3fd9d46d8089a85af` |
| Short | ours | 29.064 | `7fbe458d1f5398704bedca9d4805d9ec18e06b879d74b61dafeee081708c1b19` | `06f5d5db56a919bf70a35597ac895eab29377a86ba30929b13a6e3a71399ee3b` | `edfda748543755ea41499c35666d617f464560849a2544fe7632bdc6ddf2b5b4` | `8b04b938f020d24364ea1f994d590386955cb612d6bd58cc45d4cf1a1103c263` |
| Short | TI | 183.551 | `406dbdca1c2d0688dbc189366e901acb5d1f13b7261c8298651fad37177fb262` | `42287d57832055cf47952a9c18cef1d906cf47a121b56d2022ff91b6096d38f0` | `d2736545ab71d707fd8f10c11239e81da1f0ec63433fbea914f6d6d89462c0b9` | `c047f5497c4fd16c5b079ea8960c5fa758c1ba739308587e3d169cff58f6deb1` |
| Load step | ours | 26.747 | `52217ce6a9bdc9bc7743d583108bcc97ede5c5ee638a722664af4fcfe63747f2` | `bfffcc730cad20e9413565388de80761c030374bf4a93a30f8576ebb41807418` | `8b65d569aa80a287327a83e3d78f8690e5cf8d95c419180c6dfb2386731b03e6` | `632c0706e27ae421bda914d6f2d9736d23ccfe0a154affb4ba3b60acb2000535` |
| Load step | TI | 238.177 | `0717ada99852c427a61b714f6c9290e73db885deb5802a80795b553dd474eab6` | `dc070144ccbec76dd33ddb23da6c00fe48c04fedb7cf8d158024aa187622a85d` | `e6af71fa62545a925495825a75486b095d08334928f8d3c20515182e17b419a1` | `da94779f1c658fe122116f0106b03c2cd1666401cda22464ea758880c18737af` |

### Superseded gain attempt: waveform time origin

The first gain decks used a nonzero `.tran` save start. LTspice rebased the raw
time axis to zero at that save point: both raw files covered 0–6.65 ms while the
analyzer looked for 7–13.5 ms. Both simulations finished, but the reported
gain points were empty, so **neither gain run has a valid verdict**. Their raw
SHA-256 values were
`a386063bb1758dc163a503f3dcfc40da3efa82b9a1c7135bb1a728b48761f4cb`
(ours, 30.075 s) and
`cf26ffb73b91e44f84b1ce200d8c48cbd78a147aea3d714482fbcc19a598a97c`
(TI, 243.349 s). The deck and JSON records are retained locally in the
ignored `superseded-time-origin/` folder. The script now saves from simulation
time zero; all ten cases were rerun under the corrected script hash.

## Board-level suite activation

The owner's 66-case [system test suite](../../SYSTEM_TEST_SUITE.md) first activates
static and pin cases at M3/M5, regulator and card-A cases at M4/M8, and later
family cases at M9+. M1 has no active board-level cases, so detection, clean-card
false alarms, boundary pairs, and per-card run time are **NOT APPLICABLE** here.
No synthetic card was presented as a tested real board.

## Repository checks and delivery

M1 changed only this report, the general edition's two `tools/` measurement
scripts, and milestone/status documentation in the two editions. No production
`src/` module, model contract, installer, vendor library, or frozen requirement
was changed. The original owner suite was retained verbatim as the prefix of
`docs/SYSTEM_TEST_SUITE.md` in both editions, followed by a 66-row per-case
`NOT BUILT` tracker. M1 activates no board case.

Focused checks with the explicitly selected LTspice executable and network-marked
tests excluded:

- General: `pytest -q -m 'not network' tests/authoring tests/models tests/pipeline
  tests/ltspice tests/test_shared_core.py` → **725 passed in 192.38 s**. The first
  full attempt was 724 passed / 1 failed because one existing LTspice load check
  returned `inconclusive` after an exit-code-15 cleanup despite log and raw files.
  That single case passed immediately in isolation, then the complete focused
  command passed on rerun. The intermittent first result remains recorded here.
- Bob: the same focused command → **594 passed, 7 edition-specific skips in
  168.30 s**. No live Bob authoring was attempted because it is not needed for
  this evidence run.
- Both: `ruff check src tests tools` passed, `git diff --check` passed; the two
  general measurement scripts passed `ruff format --check` and `py_compile`.
  `tools/shared_core.py --compare ../spice-maker-bob` reported **42 identical
  files**. No raw waveform remained under `runs/m1` after hashing.

An initial focused-test invocation set `BOARDMODELER_NO_NETWORK=1` and was
stopped when mocked API-backend tests correctly refused authoring before their
fake transport could run. The successful runs excluded `network`-marked tests
and did not set that override; the M1 model simulations themselves ran with
`BOARDMODELER_NO_NETWORK=1`. No inference call or API key access was made for
M1. A repository-wide `ruff check .` still reports two pre-existing style
issues in the historical `docs/evidence/2026-09-25-buck-slice/current-limit/ilim_proof.py`;
that evidence script was left unchanged, and all edited Python files lint clean.

GitHub commit IDs and push verification are recorded in the milestone tracker
and the top entries of each edition's `docs/STATUS.md`.
