# M2: pre-freeze guards and deterministic buck benches

Status: in progress. [Milestone plan](../../SYSTEM_MODELS_PLAN.md) separates M2a
guards from M2b bench generation; neither is marked DONE here yet. M2 activates
no board-level case in the [system suite](../../SYSTEM_TEST_SUITE.md).

## Frozen source rows used for this run

The local TPS54332DDA frozen spec records verified citations to the
[TI TPS54332 datasheet](https://www.ti.com/lit/ds/symlink/tps54332.pdf):

| Row | Value | PDF page, zero-based |
| --- | --- | ---: |
| `B002_TPS54332DDA_SW_CURRENT_TO_COMP` | 12 A/V typical | 5 |
| `B003_REQ_TPS54332_ECOMODE_COMP_0P5V` | COMP 0.5 V pulse-skip boundary | 12 |
| `B002_TPS54332DDA_ILIM` | 4.2–6.5 A | 5 |
| `B002_TPS54332DDA_SS_CHARGE` | 2 µA typical only | 5 |
| `B002_TPS54332DDA_VREF` | 0.772/0.8/0.828 V min/typ/max | 5 |
| `B002_TPS54332DDA_FSW` | 800/1000/1200 kHz min/typ/max | 5 |

The cited 2 µA soft-start charge current has no min/max silicon bound in this
spec. A scheduling calculation using it must be labeled typical, then the
actual waveform must be checked before a current-limit verdict.

## Instance parameter override syntax proof

An ignored, code-built LTspice deck under `runs/m2-param/override.cir` checked
whether a subcircuit-local `.param` can be overridden on one instance. It
defined `P=2` inside the subcircuit, instanced one copy with `P=3` and another
without an override, and measured outputs **3.0 V** and **2.0 V** respectively.
The explicitly selected LTspice executable ran through Python
`simulation.ltspice.run_batch`, with `BOARDMODELER_NO_NETWORK=1`, exit 0 and a
log/raw waveform in **0.54 s**. This proves syntax for an `ILIM` instance
override in M2b; it does not establish a TPS54332 silicon process corner.

SHA-256: deck `044c105e0b892b725a2fd985106620623ce22503e099decd6df6ccdf6a0d9721`,
log `c6aa8ee62612f028e3251a992afbf0c02499e48ecc7e464a92dead5f5f678cb1`,
raw `cf12e0ab88a400a6a99fd8e093fc657c5199bfb93527d32def1096579e5550cb`,
operating-point raw `45fcd90766a8b4665d38e22d0e78962e92d5fcad474fb4b503a55ba5428c0793`.
Both raw files were removed after hashing.

## M2a: pre-freeze guard implementation

The shared planner now refuses a scalar current-sense-gain circuit measurement
for a buck. A slope needs at least two distinct COMP levels strictly above a
verified cited pulse-skip boundary; a missing or withheld citation leaves an
explicit untestable gap. The same rule applies to alias pins, while an unrelated
op-amp AC-gain recipe remains valid.

A current-limit recipe now refuses an ideal or behavioral output current source
in either orientation, including one behind a sense resistor. Its steady window
opens only after calculated SS charging using the highest cited reference and
the lowest positive cited SS charge current (minimum if given, else typical),
plus 1 ms of settling. The TPS54332 SS charge value is typical only; this is a
fixture scheduling rule, not a guaranteed silicon maximum. A missing SS
capacitor or the needed citations leaves an explicit gap. Startup waveform
observations may intentionally begin during the charge interval.

The agent's simulator-free focused check was **96 passed, 4 deselected**. Ruff
and `git diff --check` passed. General integration, Bob synchronization, and
electrical runs are still pending, so M2a is not marked DONE yet.

## M2b: deterministic benches and electrical measurements

`buck_system_fixtures.py` builds seven decks across six test families from the
frozen spec and the raw verified-requirement record. It checks the document ID,
PDF page, excerpt, `citation_verified` flag, and SI-normalized numeric values
before selecting a row. It checks the model's nine-port order before writing a
deck. The gain fit uses only COMP > cited 0.50 V and a stable contiguous segment;
the two overloads use resistive loads and explicit 4.2/6.5 A *instance parameter*
corners. The evaluator returns `MEASURED` or `UNKNOWN`, never a model PASS.

The local runner `tools/tps54332_m2_verify.py` requires `--ltspice-exe`,
`--requirements`, `--bindings`, and an ignored `runs/` output path. It rendered a
fresh TPS54332DDA library and ran LTspice through Python `run_batch`; no Bash,
AI, network request, or machine-wide LTspice search was involved. Seven actual
decks returned exit 0 with log and raw waveform. Total simulator wall time for
the seven accepted case records was **150.503 s**. Initial startup waveform
analysis exposed a missing evaluator metadata field and was `UNKNOWN`; after a
focused regression test and one-field fix, startup alone was rerun. The first
record remains under ignored `runs/m2/startup`; the accepted startup record is
under `runs/m2-startup-fixed/startup`.

| Case | Live result | Measurement and interpretation |
| --- | --- | --- |
| Gain | MEASURED | 11.426 A/V over five stable 0.75–0.95 V COMP points, within the 10.8–13.2 A/V band for the cited 12 A/V typical slope. The 0.55–0.75 V floor remains flat; overall gain behavior is **MIXED**, preserving the M1 failure. |
| Current limit, minimum setting | MEASURED | `ILIM=4.2` A instance; 499 post-startup cycles, median inductor peak 4.205 A; 500 kHz in its feedback foldback band. This checks template parameter response, not a silicon process corner. |
| Current limit, maximum setting | MEASURED | `ILIM=6.5` A instance; 499 cycles, median inductor peak 6.505 A; 500 kHz. Same template-corner caveat. The measured dynamic peak is not identical to the static current-limit threshold. |
| Output ripple | MEASURED | 1.837 mV peak to peak, 2.51750 V mean, stable window. M1's same-passive vendor comparison failed at 1.805 vs 13.110 mV; M2 does **not** promote ripple to PASS. |
| PH edges | **UNKNOWN** | 100 PH transitions each way; rising 10–90% has zero edges with two interior samples. Falling 90–10% is 0.885 ns, but this alone cannot establish nonideal edge realism. |
| 1→3 A resistive load step | MEASURED | 62.462 mV dip and 37.5 µs recovery using five-cycle bins that must stay within 10% of the dip from final voltage; observed low-frequency envelope decays. This replaces M1's loose ±2% recovery interpretation. |
| Startup | MEASURED on rerun | 4.804 ms 10–90% rise, zero downward 100 µs bins over 2% of final output, 2.08 mV maximum above final mean, stable final window, and decaying low-frequency envelope. The 2 µA SS scheduling input is cited typical only. |

Every accepted record contains its deck, log, raw and operating-point raw
SHA-256, executable/model/input hashes, exact source rows, window, status,
measurement data, and simulator time. The common frozen inputs were:
requirements `3884fd76976e071cd2ea81d5db64cb1bde17aecbaccc8f87399e7448646cc060`,
bindings `1b3d45e8977948ce2ed7f1079db2aaa598b543cd65a83b697af8455d6345ad57`,
fresh model `881ed258fbc34ac34e62769d6d56eb84aa8e8460bcfccbf0249a8fd6ae919fb8`.
The first six accepted records used `runs/m2`; the final startup record used
`runs/m2-startup-fixed`. Both runs rendered the **same model hash**. All raw and
operating-point raw files were hashed then removed; decks, logs and JSON records
remain local under ignored `runs/`.
After the evaluator fix, rerendering all seven decks from the current source
reproduced each accepted deck SHA-256 exactly.

| Accepted case | Wall (s) | Deck SHA-256 | Log SHA-256 | Raw SHA-256 |
| --- | ---: | --- | --- | --- |
| Gain | 41.017 | `296caf7054c5380a784b4c009da51909bf8f1005b65643a2ddf5c4867b9e00d0` | `1df4517cf5a9dbbea685bf8734ad9113f4592618118bf16af2dd1dced4b6a3a8` | `e40c4ed2b75aee62e78fb176c2fe3855af8fabf115287f80f322f0e77a33d234` |
| Limit min | 19.483 | `c871acf86bd2aff3d3d67a65f6ac92f070f153ddac00740a7f28c1ef1125af8e` | `448d772e9e46b21a96c534492db8b7c16560927980e7a6ce11dc62c71072598d` | `7b7e140678a1aa3a768beaaca797c91e76c710f237ac13900ff05d2fed79e051` |
| Limit max | 17.902 | `d22c4fd58cdb9c518d9747356df77197f25283d8f5bb0927f41c8cef3877042e` | `9a0f3a1e4d91d940268b5a250061b93187b722a5c9b0e7ac80d89af334eb85db` | `e148085541973b13181d14544e7d448a4ede536244e4d72f9e1ac3f934cb69e4` |
| Ripple | 18.802 | `787e0c41a3fb496c90e54e7d6f834b6aed4be306847fc97baf89b0d0760156ea` | `d384f1bbab193274aeb39cd6db32aea1a5f025d4d9f4776324541cf5fdb0efd3` | `429b07591c5ded6d07ebb3d6126aba581c02cb09351913ce1579f3857047a498` |
| Edge | 8.991 | `5229055eb89dd81d91d40b765e7f375308dd3edc7675e909630b5d1a64e6a350` | `e614ae88c01846ece45e5991fe8f78c7ecd8815a061724be42599141bb2c190b` | `8dd20fabeadf42cf35d5f2463d43ff949cce9595aa5c57fc283254789901d467` |
| Load step | 20.234 | `90166207b41ac8400d762974c1d90c963ffa15389e1cfa42c5e6d983243a6555` | `e93cacc1a7a959f0c93fd86d4c839eee6f61fa98f2e70bf935be87fad0d77026` | `2ea5ea944a7e6e5ab6480921345bc58f3a70fadf1b90878ee70d6f5413161479` |
| Startup rerun | 24.074 | `e47bc93678c8bb211b626b24d407fdf8c235c7c88e2c7f889025e663888760be` | `5564ce0cff6aababc170ff3b0fe7d1f8cf176ccef6f06c655002c1a836121b59` | `b92ff11178096c6cf1d5344a6c6baa46dafb111ba1a1db13f7db5f23611a1d03` |

The initial startup run also had a valid simulator waveform (raw SHA-256
`a20ee777ee3fc6ca565a4ed40a37fbbc1dcf2cfce5233e5bf0eaa31eae57b81c`),
but its evaluator threw `KeyError: 'nominal_fsw_hz'`; it is retained as an
analysis failure and is not counted as an accepted measurement.

## Integration checks

The general edition's focused regression set passed **751 tests in 180.06 s**.
Bob's passed **620 tests with 7 edition-specific skips in 193.07 s**. Both
used an explicit LTspice path and excluded the network-marked tests. The new
M2a/M2b controls passed 26 tests in the general edition, including the startup
evaluator regression. `ruff check src tests tools`, changed-file Ruff format
checks, `git diff --check`, and each shared-core manifest check passed. The
editions have **43 byte-identical shared core files**. M2 activates no
board-level system-suite case; none is claimed DETECTED here.
