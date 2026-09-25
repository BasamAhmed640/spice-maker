# TPS54331 second-device buck-template check — 2026-09-25

This is a compact, manual, citation-checked subset of TI's TPS54331 datasheet, not a full
datasheet extraction or a claim about hardware. The local source PDF has SHA-256
`cf72dfd0ac69eec645b7b493de628dc1c3aa66f5f925a2ea9be6bb5c38260730` (41 pages).
Every pin-map excerpt and numeric-row excerpt in [`spec.json`](spec.json) was found by
`excerpt_on_page` in the extracted text of its recorded zero-based PDF page. The pin map
is the eight-pin D package on PDF page 2. The three measured rows are on PDF pages 4–5.

The product's `seed_from_spec` rendered `TPS54331.lib` in **0.002 s**, with VREF
0.8 V and FSW 570 kHz taken from cited rows. ILIM is labeled `template_default`
(5.35 A): the datasheet's current-limit columns are **3.5 A minimum and 5.8 A
typical**, with no maximum from which the template's midpoint rule can derive
an ILIM value. This was checked against the PDF's positioned table text, not
inferred from the plain-text number order. Every parameter origin is in
[`template-parameters.json`](template-parameters.json). No provider was called
and no API key was used.

The product LTspice harness ran with the explicitly selected LTspice executable
(Windows file version 26.0.0.3).
Final run: **14.05 s; 3 PASS, 0 FAIL, 0 UNKNOWN, 0 BLOCKED**. Its model SHA-256 is
`75e2912cc84eaea5a10da502e13258b10c569cb05711b5aeb8437e0a850792e8` and the
frozen spec digest is `cf835332ac20f40f909aee5a8c0e3879746a864ca68901fb6ba7cddf11a57925`.
Each PASS has `.raw` and `.log` SHA-256 hashes in [`verdicts.json`](verdicts.json).

| Cited row | Frozen limit | Observed | Verdict |
|---|---:|---:|---|
| Shutdown VIN current, EN=0 V, VIN=12 V, nominal 25 C | ≤4 µA | −1.00121 µA raw; 1.00121 µA magnitude | PASS |
| PH switching frequency, VIN=12 V, 25 C | 456–684 kHz | 569.9999996 kHz | PASS |
| VSENSE reference | 0.772–0.828 V | 0.7998594 V | PASS |

The first bench used a 22 µF output capacitor. Its VSENSE waveform averaged 0.799774 V
but had 1.141 mV peak-to-peak ripple, exceeding the harness's settled-mean check; that
row was honestly `UNKNOWN(recipe_not_settled)` (2 PASS, 1 UNKNOWN, 17.22 s). Before freezing
the reported spec, the bench capacitor was changed to 47 µF; no cited limit or measurement
window changed. The final result above comes from a fresh LTspice run.

The cited current-limit threshold (3.5 A minimum, 5.8 A typical, PDF page 4) is
included as an **unbound** row. It is not used as a cited ILIM parameter and was
not measured: this compact check did not establish a fixture that isolates the
internal cycle-by-cycle threshold. These
three passes establish a second supported pinout and measured behavior only at the stated
conditions. They do not establish current limiting, startup across temperature, load
transients, or whole-device accuracy.
