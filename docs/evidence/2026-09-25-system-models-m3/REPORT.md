# M3: exposed-pad connection and frozen regression

Status: model, cited clean/fault checks, real LTspice measurements, and both
editions' focused checks complete; both GitHub pushes are recorded below when
finished.
This is a TPS54332DDA synthetic-card-slice result, not a full Card A result or
a claim that the entire generated model is system verified.

## Source and design

The frozen `B001_PIN_POWERPAD` row has `citation_verified=true` and the excerpt
"PowerPAD 9 — GND pin must be connected to the exposed pad for proper operation."
The [TI TPS54332 Rev. D datasheet](https://www.ti.com/lit/ds/symlink/tps54332.pdf),
PDF page index 2, printed page 3, Table 5-1 identifies GND as pin 7 and
PowerPAD as pin 9. The verifier checks the frozen row, document ID, page,
excerpt, required physical pins, and source SHA-256 before making a cited
finding. The template contract now describes an **external PCB required
connection**; it does not attach that TPS-specific citation to every buck.

The freshly rendered nine-port model preserves `BOOT VIN EN SS VSENSE COMP GND
PH POWERPAD` in that order. Its previous 1 mΩ internal PowerPAD–GND tie is
gone. `RPOWERPAD_leak POWERPAD GND 1G` is a convergence leak only and cannot
pass the static PCB check. The model also exposes an internal `chk_powerpad`
test alarm when the pad differs from GND by more than **0.1 V**. That threshold
is an intentionally synthetic diagnostic, **not** a TPS54332 data-sheet limit.
The ordinary model does not inject a diagnostic current. The separate clean
and fault decks inject 1 nA to distinguish the PCB tie from the 1 GΩ leak.

Focused model tests cover the named pad, an alias, a part without a pad, and
unsupported pins. The new connection tests cover a clean neutral card, a
missing/wrong-net pad, a missing ground, unverified citations, and a mismatch
between card and netlist. A declared disconnected pad yields one cited finding
without a duplicate netlist warning.

## Static and LTspice clean/fault evidence

The [machine-readable pad result](pad-result.json) contains the two synthetic
neutral card files, deck/log/raw/operating-point SHA-256, the exact cited
finding, waveform measurements, and execution times. The two diagnostic decks
differ only by `Rpcb pad 0 1m`, the external PCB connection. Each deck maps
the card's DGND to SPICE node 0; both runs used the executable path explicitly.
The local `runs/m3-pad-final/` raw files
were hashed, then removed; the committed result contains hashes, not raw data.

| Synthetic card slice | Static check | Findings | Mean pad voltage, 80–100 µs | Internal `chk_powerpad` | LTspice wall |
| --- | --- | ---: | ---: | ---: | ---: |
| Clean: U1 pin 7 and pin 9 on DGND | PASS | 0 | 1.00×10⁻¹² V | 0 | 0.696 s |
| Fault: U1 pin 9 on NC_09 | FAIL | 1 | 1.00 V | 1 | 0.680 s |

The fault is `RC001_required_pin_connection`, naming **U1 TPS54332DDA,
PowerPAD pin 9 on NC_09, GND pin 7 on DGND**, requirement
`B001_PIN_POWERPAD`, PDF page 3. Both alarm waveforms were `MEASURED`; neither
result was inferred from deck generation. This proves the cited static rule
and the model-internal alarm on the synthetic slice. The 66-case suite's
`GND-02` remains **NOT BUILT** because the full reference Card A has not been
built and run. No other suite case is activated here.

| Artifact | Clean SHA-256 | Fault SHA-256 |
| --- | --- | --- |
| Deck | `9370ff7ec50eb9b30ee76568b667a8e4e833b88a50650059e4d744c5b031af82` | `420c4ddff9e631c445c66ac1c707f73e20144a05b858c78db8def28cb01c1193` |
| LTspice log | `951b3d2b6ce3bb32ab6ae80b1859cf014b5a2b49a35b8d1f6b1d48b5025a11fa` | `8f323cfddb515cf797a50bcd112db91d61bb4f9ffa26de15693e2869797f12d6` |
| Raw waveform | `7369aea41c8db4b02372da26f917a6f8e4e4360a549ff2cb81c2946bb94b8905` | `c6ba7e1368825d09cd430c32a2dd5e4ab478e0b197cdeff302b49fefc3cc98c2` |
| Operating point | `b59dfca7671eff97c69a6d3a6a4c5c60c3d72e4fdb72cb873b2e67b7c11c99f7` | `697a3b247814915955de2986b844b40ffd65950d9a1064802d27c97c7b1c9f86` |

The freshly rendered model SHA-256 was
`21b3c7f1f9ed14b0d4247d291b7ba6342fecfdf19acdef59ca699c603fdb2ae2`.
The frozen requirements SHA-256 was
`3884fd76976e071cd2ea81d5db64cb1bde17aecbaccc8f87399e7448646cc060`.

## Frozen TPS54332 regression

The [19-row verdict record](regression/verdicts.json) compares every covered
row with the committed buck-slice checkpoint. Before running, the tool required
the saved requirements SHA-256 above, bindings SHA-256
`1b3d45e8977948ce2ed7f1079db2aaa598b543cd65a83b697af8455d6345ad57`,
spec digest `499ed2442781bd4cad22041e7cb028bb6af9ec3df7bd34fe053750b9782b14d7`,
and the exact 19 row IDs. The model was rendered afresh from those inputs.
The real LTspice run took **121.398 s** and retained **12 PASS, 4 FAIL,
3 UNKNOWN** with **zero changed rows**. Deck/log/raw hashes for every case are
in the verdict record; raw files were removed after hashing.

The four persistent failures are output-minimum (`B001_FEAT_VOUT_MIN`), the
historical current-sense-gain recipe (`B002_TPS54332DDA_SW_CURRENT_TO_COMP`),
the historical current-limit recipe (`B002_TPS54332DDA_ILIM`), and SS/VSENSE
matching (`B002_TPS54332DDA_SS_VSENSE_MATCH`). The unresolved rows remain
VREF and maximum duty (`recipe_not_settled`), and minimum on-time
(`recipe_crossing_missing`). They were not promoted by the pad fix. The M1
vendor-comparison ripple **FAIL** and PH-edge **UNKNOWN** also remain open.

## LM358 control regression

The [LM358 verdict record](lm358/verdicts.json) reran the saved model with
the exact saved characteristics, model, and baseline-report hashes. Its **19
probe cases** cover **32 characteristic rows**; all 19 cases and all 32 rows
remained PASS, with no changed row, in **14.376 s**. The record includes deck,
log, and raw SHA-256 for each case; raw files were removed after hashing.
This is re-verification of the frozen LM358 model, not a new model-generation
claim.

## Integration and scope

The general and Bob editions have the same 44-file provider-neutral shared
core, including the new cited connection checker. The LTspice input path was
chosen explicitly; the diagnostic and regression scripts set
`BOARDMODELER_NO_NETWORK=1` and make no AI request. The TI library, raw files,
user key, and full Card A are not committed.

The general edition's focused suite passed **836 tests in 186.27 s**. Bob's
passed **705 with 7 edition-specific skips in 196.80 s**. Both editions passed
`ruff check src tests tools`, formatting checks for changed Python, the 44-file
shared-core manifest check, the byte-for-byte cross-edition comparison, and
`git diff --check`. Both pushed commit IDs are pending.
