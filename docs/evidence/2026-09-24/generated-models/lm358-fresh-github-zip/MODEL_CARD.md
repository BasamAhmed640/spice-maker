# LM358 — LTspice model card

Model: `LM358.lib` · subcircuit `LM358` · sha256 `eac222c22a560aeae5103b9921332e4fada483421553ebf5589b6df43ba0522f`
Spec `c129543a5641cfeb` from DOC_58c89c68aff6_lm358_datasheet · authored via opencode_go in 2 turn(s)

This model is the product of an agent's work judged by real LTspice runs against the datasheet rows listed below. A row is only `PASS` when a completed simulation produced the measured value shown; rows that could not be judged are `UNKNOWN` with the reason. Datasheet rows no probe can reach are listed as coverage gaps with the reason. Nothing else about this part is claimed.
PASS applies only at the operating points recorded in harness-report.json. A nominal sample inside a datasheet range does not validate the whole range. No temperature, process distribution, protocol, or high-speed channel qualification is inferred from these behavioral probes.

**Totals:** 32 pass · 0 fail · 0 unknown · 10 rows not testable by simulation out of 42 datasheet rows. The pass/fail/unknown totals here cover executed probes; the application also counts untested numeric requirements as UNKNOWN.

## Judged characteristics

| Requirement | Datasheet statement | Required | Measured | Status | Page | Detail |
| --- | --- | --- | --- | --- | --- | --- |
| `LM358_VOS_CH1` | Input offset voltage magnitude at VS=5 V, VCM near 0 V, VO=1.4 V; amplifier 1 | max 0.007 | opamp_value=0.00301207 | PASS | 9 | LM358_VOS_CH1 [DOCUMENTED_LIMIT]: measured opamp_value=0.00301207 V within <= 0.007 V |
| `LM358_IB_CH1` | Input bias current magnitude at VS=5 V, VO=1.4 V; amplifier 1 | max 2.5e-07 | opamp_value=2e-08 | PASS | 9 | LM358_IB_CH1 [DOCUMENTED_LIMIT]: measured opamp_value=2e-08 A within <= 2.5e-07 A |
| `LM358_IOS_CH1` | Input offset current magnitude at VS=5 V, VO=1.4 V; amplifier 1 | max 5e-08 | opamp_value=2e-09 | PASS | 9 | LM358_IOS_CH1 [DOCUMENTED_LIMIT]: measured opamp_value=2e-09 A within <= 5e-08 A |
| `LM358_AOL_CH1` | Open-loop gain at VS=15 V, VO=6 V, RL=2 kohm; one point in the specified output range; amplifier 1 | min 25000 | opamp_value=99121.3 | PASS | 9 | LM358_AOL_CH1 [DOCUMENTED_LIMIT]: measured opamp_value=99121.3 V/V within >= 25000 V/V |
| `LM358_GBW_CH1` | Typical gain bandwidth product at VS=5 V; amplifier 1 | typ 700000 / target 700000 | opamp_value=699890 | PASS | 9 | LM358_GBW_CH1 [TYPICAL_VALUE]: measured opamp_value=699890 Hz, \|measured - typ 700000\| = 110.189 Hz versus the 10 % band 70000 Hz (typical-value comparison, not a min/max limit check) |
| `LM358_SR_RISE_CH1` | Typical unity-gain slew rate, rise, 1 V to 3 V step at VS=5 V; amplifier 1 | typ 300000 / target 300000 | opamp_value=299910 | PASS | 9 | LM358_SR_RISE_CH1 [TYPICAL_VALUE]: measured opamp_value=299910 V/s, \|measured - typ 300000\| = 90.4657 V/s versus the 10 % band 30000 V/s (typical-value comparison, not a min/max limit check) |
| `LM358_SR_FALL_CH1` | Typical unity-gain slew rate, fall, 1 V to 3 V step at VS=5 V; amplifier 1 | typ 300000 / target 300000 | opamp_value=300090 | PASS | 9 | LM358_SR_FALL_CH1 [TYPICAL_VALUE]: measured opamp_value=300090 V/s, \|measured - typ 300000\| = 90.4625 V/s versus the 10 % band 30000 V/s (typical-value comparison, not a min/max limit check) |
| `LM358_VOH_CH1` | Positive output headroom at VS=30 V, RL=10 kohm; amplifier 1 | max 3 | opamp_value=2.00003 | PASS | 9 | LM358_VOH_CH1 [DOCUMENTED_LIMIT]: measured opamp_value=2.00003 V within <= 3 V |
| `LM358_VOL_CH1` | Low output voltage at VS=5 V, RL=10 kohm; 25 C only; amplifier 1 | max 0.02 | opamp_value=0.005 | PASS | 9 | LM358_VOL_CH1 [DOCUMENTED_LIMIT]: measured opamp_value=0.005 V within <= 0.02 V |
| `LM358_VOS_TYP_CH1` | Typical offset magnitude; amplifier 1 | typ 0.003 / target 0.003 | opamp_value=0.00301207 | PASS | 9 | LM358_VOS_TYP_CH1 [TYPICAL_VALUE]: measured opamp_value=0.00301207 V, \|measured - typ 0.003\| = 1.20698e-05 V versus the 10 % band 0.0003 V (typical-value comparison, not a min/max limit check) |
| `LM358_IB_TYP_CH1` | Typical input bias current magnitude (current flows out of the input pins); amplifier 1 | typ 2e-08 / target 2e-08 | opamp_value=2e-08 | PASS | 9 | LM358_IB_TYP_CH1 [TYPICAL_VALUE]: measured opamp_value=2e-08 A, \|measured - typ 2e-08\| = 3.30872e-24 A versus the 10 % band 2e-09 A (typical-value comparison, not a min/max limit check) |
| `LM358_IOS_TYP_CH1` | Typical input offset current magnitude; amplifier 1 | typ 2e-09 / target 2e-09 | opamp_value=2e-09 | PASS | 9 | LM358_IOS_TYP_CH1 [TYPICAL_VALUE]: measured opamp_value=2e-09 A, \|measured - typ 2e-09\| = 8.6854e-24 A versus the 10 % band 2e-10 A (typical-value comparison, not a min/max limit check) |
| `LM358_AOL_TYP_CH1` | Typical open-loop gain at VS=15 V, VO=6 V, RL=2 kohm; amplifier 1 | typ 100000 / target 100000 | opamp_value=99121.3 | PASS | 9 | LM358_AOL_TYP_CH1 [TYPICAL_VALUE]: measured opamp_value=99121.3 V/V, \|measured - typ 100000\| = 878.746 V/V versus the 10 % band 10000 V/V (typical-value comparison, not a min/max limit check) |
| `LM358_VOH_TYP_CH1` | Typical high-output headroom at VS=30 V, RL=10 kohm; amplifier 1 | typ 2 / target 2 | opamp_value=2.00003 | PASS | 9 | LM358_VOH_TYP_CH1 [TYPICAL_VALUE]: measured opamp_value=2.00003 V, \|measured - typ 2\| = 2.8e-05 V versus the 10 % band 0.2 V (typical-value comparison, not a min/max limit check) |
| `LM358_VOL_TYP_CH1` | Typical low output at VS=5 V, RL=10 kohm; amplifier 1 | typ 0.005 / target 0.005 | opamp_value=0.005 | PASS | 9 | LM358_VOL_TYP_CH1 [TYPICAL_VALUE]: measured opamp_value=0.005 V, \|measured - typ 0.005\| = 4.99999e-09 V versus the 10 % band 0.0005 V (typical-value comparison, not a min/max limit check) |
| `LM358_VOS_CH2` | Input offset voltage magnitude at VS=5 V, VCM near 0 V, VO=1.4 V; amplifier 2 | max 0.007 | opamp_value=0.00301207 | PASS | 9 | LM358_VOS_CH2 [DOCUMENTED_LIMIT]: measured opamp_value=0.00301207 V within <= 0.007 V |
| `LM358_IB_CH2` | Input bias current magnitude at VS=5 V, VO=1.4 V; amplifier 2 | max 2.5e-07 | opamp_value=2e-08 | PASS | 9 | LM358_IB_CH2 [DOCUMENTED_LIMIT]: measured opamp_value=2e-08 A within <= 2.5e-07 A |
| `LM358_IOS_CH2` | Input offset current magnitude at VS=5 V, VO=1.4 V; amplifier 2 | max 5e-08 | opamp_value=2e-09 | PASS | 9 | LM358_IOS_CH2 [DOCUMENTED_LIMIT]: measured opamp_value=2e-09 A within <= 5e-08 A |
| `LM358_AOL_CH2` | Open-loop gain at VS=15 V, VO=6 V, RL=2 kohm; one point in the specified output range; amplifier 2 | min 25000 | opamp_value=99121.3 | PASS | 9 | LM358_AOL_CH2 [DOCUMENTED_LIMIT]: measured opamp_value=99121.3 V/V within >= 25000 V/V |
| `LM358_GBW_CH2` | Typical gain bandwidth product at VS=5 V; amplifier 2 | typ 700000 / target 700000 | opamp_value=699890 | PASS | 9 | LM358_GBW_CH2 [TYPICAL_VALUE]: measured opamp_value=699890 Hz, \|measured - typ 700000\| = 110.189 Hz versus the 10 % band 70000 Hz (typical-value comparison, not a min/max limit check) |
| `LM358_SR_RISE_CH2` | Typical unity-gain slew rate, rise, 1 V to 3 V step at VS=5 V; amplifier 2 | typ 300000 / target 300000 | opamp_value=299910 | PASS | 9 | LM358_SR_RISE_CH2 [TYPICAL_VALUE]: measured opamp_value=299910 V/s, \|measured - typ 300000\| = 90.4657 V/s versus the 10 % band 30000 V/s (typical-value comparison, not a min/max limit check) |
| `LM358_SR_FALL_CH2` | Typical unity-gain slew rate, fall, 1 V to 3 V step at VS=5 V; amplifier 2 | typ 300000 / target 300000 | opamp_value=300090 | PASS | 9 | LM358_SR_FALL_CH2 [TYPICAL_VALUE]: measured opamp_value=300090 V/s, \|measured - typ 300000\| = 90.4625 V/s versus the 10 % band 30000 V/s (typical-value comparison, not a min/max limit check) |
| `LM358_VOH_CH2` | Positive output headroom at VS=30 V, RL=10 kohm; amplifier 2 | max 3 | opamp_value=2.00003 | PASS | 9 | LM358_VOH_CH2 [DOCUMENTED_LIMIT]: measured opamp_value=2.00003 V within <= 3 V |
| `LM358_VOL_CH2` | Low output voltage at VS=5 V, RL=10 kohm; 25 C only; amplifier 2 | max 0.02 | opamp_value=0.005 | PASS | 9 | LM358_VOL_CH2 [DOCUMENTED_LIMIT]: measured opamp_value=0.005 V within <= 0.02 V |
| `LM358_VOS_TYP_CH2` | Typical offset magnitude; amplifier 2 | typ 0.003 / target 0.003 | opamp_value=0.00301207 | PASS | 9 | LM358_VOS_TYP_CH2 [TYPICAL_VALUE]: measured opamp_value=0.00301207 V, \|measured - typ 0.003\| = 1.20698e-05 V versus the 10 % band 0.0003 V (typical-value comparison, not a min/max limit check) |
| `LM358_IB_TYP_CH2` | Typical input bias current magnitude (current flows out of the input pins); amplifier 2 | typ 2e-08 / target 2e-08 | opamp_value=2e-08 | PASS | 9 | LM358_IB_TYP_CH2 [TYPICAL_VALUE]: measured opamp_value=2e-08 A, \|measured - typ 2e-08\| = 3.30872e-24 A versus the 10 % band 2e-09 A (typical-value comparison, not a min/max limit check) |
| `LM358_IOS_TYP_CH2` | Typical input offset current magnitude; amplifier 2 | typ 2e-09 / target 2e-09 | opamp_value=2e-09 | PASS | 9 | LM358_IOS_TYP_CH2 [TYPICAL_VALUE]: measured opamp_value=2e-09 A, \|measured - typ 2e-09\| = 8.6854e-24 A versus the 10 % band 2e-10 A (typical-value comparison, not a min/max limit check) |
| `LM358_AOL_TYP_CH2` | Typical open-loop gain at VS=15 V, VO=6 V, RL=2 kohm; amplifier 2 | typ 100000 / target 100000 | opamp_value=99121.3 | PASS | 9 | LM358_AOL_TYP_CH2 [TYPICAL_VALUE]: measured opamp_value=99121.3 V/V, \|measured - typ 100000\| = 878.746 V/V versus the 10 % band 10000 V/V (typical-value comparison, not a min/max limit check) |
| `LM358_VOH_TYP_CH2` | Typical high-output headroom at VS=30 V, RL=10 kohm; amplifier 2 | typ 2 / target 2 | opamp_value=2.00003 | PASS | 9 | LM358_VOH_TYP_CH2 [TYPICAL_VALUE]: measured opamp_value=2.00003 V, \|measured - typ 2\| = 2.8e-05 V versus the 10 % band 0.2 V (typical-value comparison, not a min/max limit check) |
| `LM358_VOL_TYP_CH2` | Typical low output at VS=5 V, RL=10 kohm; amplifier 2 | typ 0.005 / target 0.005 | opamp_value=0.005 | PASS | 9 | LM358_VOL_TYP_CH2 [TYPICAL_VALUE]: measured opamp_value=0.005 V, \|measured - typ 0.005\| = 4.99999e-09 V versus the 10 % band 0.0005 V (typical-value comparison, not a min/max limit check) |
| `LM358_IQ` | No-load supply current per amplifier (dual total divided by two), VS=5 V, VO=2.5 V; 25 C only | max 0.0006 | opamp_value=0.00035 | PASS | 9 | LM358_IQ [DOCUMENTED_LIMIT]: measured opamp_value=0.00035 A within <= 0.0006 A |
| `LM358_IQ_TYP` | Typical no-load supply current per amplifier at VS=5 V, VO=2.5 V | typ 0.00035 / target 0.00035 | opamp_value=0.00035 | PASS | 9 | LM358_IQ_TYP [TYPICAL_VALUE]: measured opamp_value=0.00035 A, \|measured - typ 0.00035\| = 5.42101e-20 A versus the 10 % band 3.5e-05 A (typical-value comparison, not a min/max limit check) |

## Datasheet rows with no simulation probe

These are declared gaps, not passes. Each needs bench measurement or a different tool to establish.

| Requirement | Datasheet statement | Why no probe | Page |
| --- | --- | --- | --- |
| `LM358_DRIFT` | Offset voltage drift and temperature limits | No qualified probe for this characteristic or its full operating range. | 9 |
| `LM358_PSRR` | Power supply rejection across the specified supply range | No qualified probe for this characteristic or its full operating range. | 9 |
| `LM358_SEPARATION` | Channel separation from 1 kHz to 20 kHz | No qualified probe for this characteristic or its full operating range. | 9 |
| `LM358_VCM` | Full common-mode input range including temperature dependence | No qualified probe for this characteristic or its full operating range. | 9 |
| `LM358_CMRR` | Common-mode rejection ratio | No qualified probe for this characteristic or its full operating range. | 9 |
| `LM358_IOS_DRIFT` | Input offset current temperature drift | No qualified probe for this characteristic or its full operating range. | 9 |
| `LM358_NOISE` | Input voltage noise density | No qualified probe for this characteristic or its full operating range. | 9 |
| `LM358_IO` | Output sourcing and sinking current across the specified conditions | No qualified probe for this characteristic or its full operating range. | 9 |
| `LM358_ISC` | Short-circuit output current | No qualified probe for this characteristic or its full operating range. | 9 |
| `LM358_OTHER_POINTS` | All supply/load/temperature corners outside the recorded nominal samples | No qualified probe for this characteristic or its full operating range. | 9 |

## Scope

- Behavioural model: it reproduces the judged rows above at the stated conditions and is not a transistor-level replica of the silicon.
- Anything not on this card (thermal behaviour, internal oscillator artifacts, EMI, fault timing corners, absolute-maximum survival) is outside the model's claimed scope.
- Exported files reference no vendor data unless the card says otherwise.

## Reproduce

```
uv run boardmodeler model test --out <this directory>
```

The harness reruns every probe in a fresh LTspice batch and rewrites this card.
