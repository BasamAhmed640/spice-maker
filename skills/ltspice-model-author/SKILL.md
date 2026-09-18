---
name: ltspice-model-author
description: >
  Author an LTspice model for a real part from its datasheet, then let a deterministic
  harness judge it with real simulator runs. Use when asked to "make an LTspice model",
  "model this part", "generate a SPICE model from a datasheet", or to fix a model that
  failed its datasheet checks. The harness owns the limits and the part number's
  datasheet rows; the agent owns only the model text.
---

# Authoring a datasheet-grounded LTspice model

## What you produce

For a part (say `TPS54320`) with subcircuit name `<SUBCKT>`:

| File | Where | Requirement |
|---|---|---|
| `<SUBCKT>.lib` | `<workdir>/model/` | Self-contained `.subckt` with EXACTLY the declared ports, in the declared order. No `.include`, no absolute paths, no vendor files. |
| `<SUBCKT>.asy` | `<workdir>/model/` | LTspice symbol with `PINATTR SpiceOrder` matching the port order one-to-one. |

Nothing else you write is used. `spec/` is frozen: if you edit it, the build stops with
`spec_tampered` and your work is discarded.

## The four rules that cannot be bent

1. **The datasheet is the target, not the simulation.** Every covered row in `spec/characteristics.json`
   carries a limit, a unit, a page and a verbatim excerpt. Matching the simulator to a
   convenient value is failure, even if the waveform looks nice.
2. **No PASS without an observed run.** A probe that cannot complete (convergence failure,
   truncated `.tran`, a signal that was not saved, a port you did not declare) is reported
   `UNKNOWN` with the reason. Never present an unmeasured row as working.
3. **You may not relax anything.** You cannot edit limits, tolerances, the binding file, or
   delete a probe. The only knob you have is the model text.
4. **Say what you did not model.** If a datasheet row cannot be represented in a behavioural
   model (internal oscillator behavior, thermal response), it is already listed as not
   testable in the spec — do not fake it with an equivalent that quietly changes the part.

## Workflow

```bash
# 1. Read the spec the harness will judge you against
cat spec/characteristics.json      # every covered row: limits, unit, page, excerpt
cat spec/README.md                 # frozen-directory notice

# 2. Write the model, then test yourself
#    (in a full build the harness also runs automatically after each turn)
uv run boardmodeler model test --out <workdir>

# 3. When every covered row passes, the deliverables are written for the user
uv run boardmodeler model install --out <out-dir> --user-lib --apply
```

## Reading harness feedback

Feedback lines name the probe, what was measured, and what the datasheet requires, e.g.

```
uvlo_rise: measured VIN=3.71 V where VOUT enters regulation;
          requires min 4.0 / max 4.5 V (page 4: "VIN internal UVLO threshold VIN rising 4.0 4.5 V")
```

Translate that into a model change, not a measurement change:

| Symptom | Likely model cause |
|---|---|
| Threshold outside the cited band | The comment/reference that drives the comparator's trip point |
| Regulation off by a few percent | Internal reference value, feedback divider assumption, or error-amplifier gain/offset |
| Threshold never seen in the sweep | Startup logic requires an internal rail or the enable path above the swept range |
| `port_missing:<name>` | The `.subckt` line omits a port the deck binds — fix the port list; do not rename the deck's expectation |
| `signal_not_saved` / `no crossing` | The probe's conditions were not reachable (e.g. the part never enabled). Fix the enable/startup path, not the probe |
| `run_incomplete` | Numerical robustness: add a small series resistance, tame a discontinuity, avoid ideal zero-resistance loops |

## Model construction guidance

- Prefer a **behavioural** topology that reproduces the datasheet rows you are judged on:
  a controlled source for the regulation loop, a comparator with hysteresis for
  UVLO/enable, an open-drain stage for PG, and explicit leakage paths.
- Keep every internal node grounded through a large resistor (`1G`) so LTspice has no
  floating nodes; avoid ideal hard sources driving capacitors directly.
- Drive the output through a finite impedance that reflects the part's real behaviour
  (switching stage or pass element), never an ideal voltage source.
- Comment each block with the requirement id it implements. A reviewer must be able to
  map every line to a datasheet row or an explicit simplification.
- Keep the model deterministic: no random parameters, no `.step`, no time()-dependent
  behaviours that make the harness results unrepeatable.

## Reporting to the user

State, in this order: which rows were judged PASS with their measured values, which rows
could not be judged and why, and which datasheet rows have no simulation probe at all.
Never summarize the model as "working" beyond exactly those rows.
