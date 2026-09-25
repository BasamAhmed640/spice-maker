# TPS54332DDA full GUI build — 2026-09-25

The general edition's model maker ran from **GO** to saved deliverables with the
official local TI TPS54332 datasheet, full verification, the selected LTspice
26 installation and the configured OpenCode Go provider. This was one live
GUI run, starting 04:38:20 UTC and ending 04:58:50 UTC, **1,230.314 s**
(20m30s). No key value was recorded in this evidence.

| Outcome | Observed value |
| --- | --- |
| Overall status | **UNKNOWN** |
| Product row counts | 10 PASS, 1 FAIL, 53 UNKNOWN, 55 NOT_APPLICABLE; 119 total |
| Delivered files | `TPS54332DDA.lib`, `.asy`, `MODEL_CARD.md`, `results.json`, `harness-report.json`, `example.cir`, `install.md`, `template-parameters.json` |
| Library SHA-256 | `d9b0b5e3d4729168933f1a834e49d4e25588dfb6239cf420047c00f999d913e2` |
| Author repair | One bounded provider invocation timed out after 300 s; the simulator-measured template was retained |
| Measured failing row | `B002_REQ_016`, required 4.2–6.5 A, measured approximately 1.21198e-9 A on page 5's planned current-limit fixture |

The output is a convergent partial model, **not** a fully validated TPS54332DDA
model. It delivered a model where the earlier AI-only run took 1,957 s and
delivered none, but this GUI run missed the goal of a deliverable in a few
minutes. The fresh extraction bound a different set of rows than the frozen
19-row regression; their pass counts must not be compared as if they were the
same test plan. The 19-row frozen regression is documented separately in
[`../2026-09-25-buck-template/REPORT.md`](../2026-09-25-buck-template/REPORT.md).

The [finished GUI screenshot](finished.png) was taken before the stage-display
fix: it still showed `judge` as running after the saved result was UNKNOWN. That
display bug was observed here and is addressed in the subsequent source change;
this screenshot remains the unaltered evidence from the live run.
