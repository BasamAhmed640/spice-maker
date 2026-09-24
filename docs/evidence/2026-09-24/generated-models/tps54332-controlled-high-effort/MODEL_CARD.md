# TPS54332DDA — LTspice model card

Model: `TPS54332DDA.lib` · subcircuit `TPS54332DDA` · sha256 ``
Spec `587a27b80d49586b` from DOC_af3d92b2c665_TPS54332_3_5_A_28_V_1_MHz_Step_Down_DC_D · authored via opencode_go in 1 turn(s)

This model is the product of an agent's work judged by real LTspice runs against the datasheet rows listed below. A row is only `PASS` when a completed simulation produced the measured value shown; rows that could not be judged are `UNKNOWN` with the reason. Datasheet rows no probe can reach are listed as coverage gaps with the reason. Nothing else about this part is claimed.
PASS applies only at the operating points recorded in harness-report.json. A nominal sample inside a datasheet range does not validate the whole range. No temperature, process distribution, protocol, or high-speed channel qualification is inferred from these behavioral probes.

**Totals:** 0 pass · 0 fail · 27 unknown · 132 rows not testable by simulation out of 159 datasheet rows. The pass/fail/unknown totals here cover executed probes; the application also counts untested numeric requirements as UNKNOWN.

## Judged characteristics

| Requirement | Datasheet statement | Required | Measured | Status | Page | Detail |
| --- | --- | --- | --- | --- | --- | --- |
| `B001_REQ_024` | Adjustable output voltage down to 0.8 V. | min 0.8 | - | UNKNOWN | 0 | no probe reported for this characteristic |
| `B001_REQ_026` | Supports up to 3.5 A continuous output current. | max 3.5 | - | UNKNOWN | 0 | no probe reported for this characteristic |
| `B001_REQ_029` | Typical shutdown quiescent current: 1 μA. | typ 1e-06 / target 1e-06 | - | UNKNOWN | 0 | no probe reported for this characteristic |
| `B002_REQ_004` | Shutdown supply current is 1 μA typical, 4 μA max at EN = 0 V, VIN = 12 V, –40°C to 85°C. | typ 1e-06 / max 4e-06 / target 1e-06 | - | UNKNOWN | 5 | no probe reported for this characteristic |
| `B002_REQ_005` | Operating non-switching supply current is 82 μA typical, 120 μA max at VSENSE = 0.85 V. | typ 8.2e-05 / max 0.00012 / target 8.2e-05 | - | UNKNOWN | 5 | no probe reported for this characteristic |
| `B002_REQ_006` | Enable threshold (rising and falling) is 1.25 V min to 1.35 V max. | min 1.25 / max 1.35 | - | UNKNOWN | 5 | no probe reported for this characteristic |
| `B002_REQ_007` | EN input current at enable threshold – 50 mV is -1 μA typical. | typ -1e-06 / target -1e-06 | - | UNKNOWN | 5 | no probe reported for this characteristic |
| `B002_REQ_008` | EN input current at enable threshold + 50 mV is -4 μA typical. | typ -4e-06 / target -4e-06 | - | UNKNOWN | 5 | no probe reported for this characteristic |
| `B002_REQ_009` | Voltage reference is 0.772 V min, 0.8 V typ, 0.828 V max. | min 0.772 / typ 0.8 / max 0.828 / target 0.8 | - | UNKNOWN | 5 | no probe reported for this characteristic |
| `B002_REQ_013` | Error amplifier DC gain is 800 V/V typical at VSENSE = 0.8 V. (1) Specified by design. | typ 800 / target 800 | - | UNKNOWN | 5 | no probe reported for this characteristic |
| `B002_REQ_014` | Error amplifier unity gain bandwidth is 2.7 MHz typical with 5 pF capacitance from COMP to GND pins. (1) Specified by design. | typ 2.7e+06 / target 2.7e+06 | - | UNKNOWN | 5 | no probe reported for this characteristic |
| `B002_REQ_015` | Error amplifier source/sink current is ±7 μA typical at V(COMP) = 1.0 V, 100-mV overdrive. | typ 7e-06 / target 7e-06 | - | UNKNOWN | 5 | no probe reported for this characteristic |
| `B002_REQ_017` | Pulse-skipping Eco-mode switch current threshold is 160 mA typical. | typ 0.16 / target 0.16 | - | UNKNOWN | 5 | no probe reported for this characteristic |
| `B002_REQ_018` | Current limit threshold is 4.2 A min to 6.5 A max at VIN = 12 V. | min 4.2 / max 6.5 | - | UNKNOWN | 5 | no probe reported for this characteristic |
| `B002_REQ_020` | Slow-start (SS) pin charge current is 2 μA typical at V(SS) = 0.4 V. | typ 2e-06 / target 2e-06 | - | UNKNOWN | 5 | no probe reported for this characteristic |
| `B002_REQ_023` | Minimum controllable on time is 110 ns typical, 135 ns max at VIN = 12 V, 25°C. (1) Specified by design. | typ 1.1e-07 / max 1.35e-07 / target 1.1e-07 | - | UNKNOWN | 5 | no probe reported for this characteristic |
| `B002_REQ_026` | BOOT to PH pin UVLO threshold is 2.1 V typical; high-side MOSFET turns off when voltage falls below this. | typ 2.1 / target 2.1 | - | UNKNOWN | 8 | no probe reported for this characteristic |
| `B002_REQ_035` | Voltage reference initial accuracy is ±2%. | min -2 / max 2 | - | UNKNOWN | 9 | no probe reported for this characteristic |
| `B003_REQ-004` | TPS54332 minimum switch current limit is 4.2 A. | min 4.2 | - | UNKNOWN | 13 | no probe reported for this characteristic |
| `B003_REQ-005` | Enable threshold voltage is 1.25 V typical. | typ 1.25 / target 1.25 | - | UNKNOWN | 11 | no probe reported for this characteristic |
| `B003_REQ-010` | In light-load pulse-skipping Eco-mode, when peak inductor current is lower than the pulse skip threshold, COMP pin voltage falls to 0.5 V typical and the device enters Eco-mode; COMP pin voltage is clamped at 0.5 V typical internally, preventing high-side MOSFET switching. | typ 0.5 / target 0.5 | - | UNKNOWN | 12 | no probe reported for this characteristic |
| `B003_REQ-017` | When VSENSE pin voltage goes above 109% × Vref, the high-side MOSFET is forced off. | min 0.872 | - | UNKNOWN | 11 | no probe reported for this characteristic |
| `B003_REQ-018` | When VSENSE pin voltage falls below 107% × Vref, the high-side MOSFET is enabled again. | max 0.856 | - | UNKNOWN | 11 | no probe reported for this characteristic |
| `B004_REQ_TPS54332DDA_VREF_0P8V` | Internal reference voltage VREF is 0.8 V (typical). | typ 0.8 / target 0.8 | - | UNKNOWN | 17 | no probe reported for this characteristic |
| `B004_REQ_TPS54332DDA_VGGM_800` | VGGM is 800 (error amplifier voltage gain). | typ 800 / target 800 | - | UNKNOWN | 17 | no probe reported for this characteristic |
| `B005_REQ-MAX-DUTY-CYCLE` | The upper output voltage set point limit is constrained by the maximum duty cycle of 91%. | max 91 | - | UNKNOWN | 19 | no probe reported for this characteristic |
| `B005_REQ-MIN-ON-TIME` | The lower output voltage limit is constrained by the minimum controllable on time, which can be as high as 130 ns. | max 1.3e-07 | - | UNKNOWN | 20 | no probe reported for this characteristic |

## Datasheet rows with no simulation probe

These are declared gaps, not passes. Each needs bench measurement or a different tool to establish.

| Requirement | Datasheet statement | Why no probe | Page |
| --- | --- | --- | --- |
| `B001_REQ_001` | Operating input voltage on VIN pin: MIN 3.5 V, MAX 28 V. | Recommended operating input-voltage range is a fixture condition, not a DUT output measurement; must be enforced by supply. Not a measurable quantity. | 3 |
| `B001_REQ_002` | Operating junction temperature TJ: MIN –40 °C, MAX 150 °C. | Operating junction temperature range is thermal; only nominal 25 C is supported, wider range cannot be verified by a 25 C LTspice fixture. | 3 |
| `B001_REQ_003` | Absolute maximum input voltage VIN: –0.3 V to 30 V. Stress rating only. | unknown classification or absolute stress rating, not an operating target | 3 |
| `B001_REQ_004` | Absolute maximum input voltage EN: –0.3 V to 6 V. Stress rating only. | unknown classification or absolute stress rating, not an operating target | 3 |
| `B001_REQ_005` | Absolute maximum input voltage BOOT: 38 V. Stress rating only. | unknown classification or absolute stress rating, not an operating target | 3 |
| `B001_REQ_006` | Absolute maximum input voltage VSENSE: –0.3 V to 3 V. Stress rating only. | unknown classification or absolute stress rating, not an operating target | 3 |
| `B001_REQ_007` | Absolute maximum input voltage COMP: –0.3 V to 3 V. Stress rating only. | unknown classification or absolute stress rating, not an operating target | 3 |
| `B001_REQ_008` | Absolute maximum input voltage SS: –0.3 V to 3 V. Stress rating only. | unknown classification or absolute stress rating, not an operating target | 3 |
| `B001_REQ_009` | Absolute maximum output voltage BOOT-PH: 8 V. Stress rating only. | unknown classification or absolute stress rating, not an operating target | 3 |
| `B001_REQ_010` | Absolute maximum output voltage VPH: –0.6 V to 30 V. Stress rating only. | unknown classification or absolute stress rating, not an operating target | 3 |
| `B001_REQ_011` | Absolute maximum PH voltage, 10-ns transient from ground to negative peak: –5 V. Stress rating only. | unknown classification or absolute stress rating, not an operating target | 3 |
| `B001_REQ_012` | Absolute maximum source current EN: 100 μA. Stress rating only. | unknown classification or absolute stress rating, not an operating target | 3 |
| `B001_REQ_013` | Absolute maximum source current BOOT: 100 mA. Stress rating only. | citation_unverified: test circuit cannot be grounded in the cited page | 3 |
| `B001_REQ_014` | Absolute maximum source current VSENSE: 10 μA. Stress rating only. | citation_unverified: test circuit cannot be grounded in the cited page | 3 |
| `B001_REQ_015` | Absolute maximum source current PH: 9.25 A. Stress rating only. | citation_unverified: test circuit cannot be grounded in the cited page | 3 |
| `B001_REQ_016` | Absolute maximum sink current VIN: 9.25 A. Stress rating only. | unknown classification or absolute stress rating, not an operating target | 3 |
| `B001_REQ_017` | Absolute maximum sink current COMP: 100 μA. Stress rating only. | citation_unverified: test circuit cannot be grounded in the cited page | 3 |
| `B001_REQ_018` | Absolute maximum sink current SS: 200 μA. Stress rating only. | citation_unverified: test circuit cannot be grounded in the cited page | 3 |
| `B001_REQ_019` | Absolute maximum operating junction temperature: –40 °C to 150 °C. Stress rating only. | unknown classification or absolute stress rating, not an operating target | 3 |
| `B001_REQ_020` | Absolute maximum storage temperature: –65 °C to 150 °C. | unknown classification or absolute stress rating, not an operating target | 3 |
| `B001_REQ_021` | Stresses beyond those listed under Absolute Maximum Ratings may cause permanent damage to the device. These are stress ratings only, which do not imply functional operation of the device at these or any other conditions beyond those indicated under Recommended Operating Conditions. Exposure to absolute-maximum-rated conditions for extended periods may affect device reliability. | unknown classification or absolute stress rating, not an operating target | 3 |
| `B001_REQ_022` | ESD rating, Human body model (HBM), per ANSI/ESDA/JEDEC JS-001, all pins: 2 kV maximum. | unknown classification or absolute stress rating, not an operating target | 3 |
| `B001_REQ_023` | ESD rating, Charged device model (CDM), per JEDEC specification JESD22-C101, all pins: 500 V maximum. | unknown classification or absolute stress rating, not an operating target | 3 |
| `B001_REQ_025` | Integrated high-side MOSFET on-resistance: 80 mΩ (feature value). | Integrated high-side MOSFET on-resistance requires internal gate control and a ratio of VIN-PH voltage to inductor current during on-time; available measurement operations do not support this ratio or internal MOSFET forcing. | 0 |
| `B001_REQ_027` | High-efficiency at light loads with a pulse-skipping Eco-mode. | unknown classification or absolute stress rating, not an operating target | 0 |
| `B001_REQ_028` | Fixed 1-MHz switching frequency. | Fixed switching frequency requires frequency measurement; available operations provide period/time (delay) or AC unity frequency, not a reciprocal to Hz; no supported measurement operation for switching frequency. | 0 |
| `B001_REQ_030` | Adjustable slow-start limits inrush currents. | unknown classification or absolute stress rating, not an operating target | 0 |
| `B001_REQ_031` | Programmable UVLO threshold. | unknown classification or absolute stress rating, not an operating target | 0 |
| `B001_REQ_032` | Overvoltage transient protection. | unknown classification or absolute stress rating, not an operating target | 0 |
| `B001_REQ_033` | Cycle-by-cycle current limit protection. | unknown classification or absolute stress rating, not an operating target | 0 |
| `B001_REQ_034` | Frequency foldback protection. | unknown classification or absolute stress rating, not an operating target | 0 |
| `B001_REQ_035` | Thermal shutdown protection. | unknown classification or absolute stress rating, not an operating target | 0 |
| `B001_REQ_036` | Current mode control with internal slope compensation simplifies the external compensation calculations and reduces component count while allowing the use of ceramic output capacitors. | unknown classification or absolute stress rating, not an operating target | 0 |
| `B001_REQ_037` | A 0.1-μF bootstrap capacitor is required between BOOT and PH. If the voltage on this capacitor falls below the minimum requirement, the high-side MOSFET is forced to switch off until the capacitor is refreshed. | Bootstrap capacitor value/connectivity is an external component requirement, not a measurable DUT electrical response; it is included in support fixtures but cannot be verified as a DUT measurement. | 2 |
| `B001_REQ_038` | Pull EN below 1.25 V to disable. Float to enable. TI recommends programming the input undervoltage lockout with two resistors. | no numeric limit; this fixture interpreter measures numeric characteristics | 2 |
| `B001_REQ_039` | Slow-start pin. An external capacitor connected to this pin sets the output rise time. | unknown classification or absolute stress rating, not an operating target | 2 |
| `B001_REQ_040` | VSENSE is the inverting node of the gm error amplifier. | unknown classification or absolute stress rating, not an operating target | 2 |
| `B001_REQ_041` | COMP is error amplifier output, and input to the PWM comparator. Connect frequency compensation components to this pin. | unknown classification or absolute stress rating, not an operating target | 2 |
| `B001_REQ_042` | GND pin is ground. | unknown classification or absolute stress rating, not an operating target | 2 |
| `B001_REQ_043` | PH is the source of the internal high-side power MOSFET. | unknown classification or absolute stress rating, not an operating target | 2 |
| `B001_REQ_044` | GND pin must be connected to the exposed pad for proper operation. | unknown classification or absolute stress rating, not an operating target | 2 |
| `B001_REQ_045` | Junction-to-ambient thermal resistance RθJA: 48.7 °C/W. | Thermal resistance/packaging requirement; not an electrical circuit measurement at 25 C. | 4 |
| `B001_REQ_046` | Junction-to-case (top) thermal resistance RθJC(top): 52.4 °C/W. | Thermal resistance/packaging requirement; not an electrical circuit measurement at 25 C. | 4 |
| `B001_REQ_047` | Junction-to-board thermal resistance RθJB: 25.3 °C/W. | Thermal resistance/packaging requirement; not an electrical circuit measurement at 25 C. | 4 |
| `B001_REQ_048` | Junction-to-top characterization parameter ψJT: 8.4 °C/W. | Thermal resistance/packaging requirement; not an electrical circuit measurement at 25 C. | 4 |
| `B001_REQ_049` | Junction-to-board characterization parameter ψJB: 25.2 °C/W. | Thermal junction-to-board characterization parameter; not an electrical measurement and not verifiable at nominal 25 C. | 4 |
| `B001_REQ_050` | Junction-to-case (bottom) thermal resistance RθJC(bot): 2.3 °C/W. | Thermal junction-to-case thermal resistance; not an electrical measurement and not verifiable at nominal 25 C. | 4 |
| `B001_REQ_051` | TPS54332 DDA (SO PowerPAD, 8) package size is 4.9 mm × 6 mm. Package dimensions are outside pin-level circuit simulation and are not expanded into numeric rows. Footnote: The package size (length × width) is a nominal value and includes pins, where applicable. | unknown classification or absolute stress rating, not an operating target | 0 |
| `B001_REQ_052` | Legal notice: An IMPORTANT NOTICE at the end of this data sheet addresses availability, warranty, changes, use in safety-critical applications, intellectual property matters and other important disclaimers. PRODUCTION DATA. | unknown classification or absolute stress rating, not an operating target | 0 |
| `B002_REQ_001` | VIN operating range is 3.5 V to 28 V. | Recommended VIN operating range is a fixture condition, not an output measurement; unbound per instruction. | 5 |
| `B002_REQ_002` | Junction temperature operating range is –40°C to 150°C. | Junction temperature operating range is a thermal range and not a 25 C electrical measurement; unbound. | 5 |
| `B002_REQ_003` | Internal undervoltage lockout threshold (rising and falling) is 3.5 V. | unknown classification or absolute stress rating, not an operating target | 5 |
| `B002_REQ_010` | High-side MOSFET on resistance at BOOT-PH = 3 V, VIN = 3.5 V is 115 mΩ typical, 200 mΩ max. | citation_unverified: test circuit cannot be grounded in the cited page | 5 |
| `B002_REQ_011` | High-side MOSFET on resistance at BOOT-PH = 6 V, VIN = 12 V is 80 mΩ typical, 150 mΩ max. | citation_unverified: test circuit cannot be grounded in the cited page | 5 |
| `B002_REQ_012` | Error amplifier transconductance (gm) is 92 μmhos typical at –2 μA < ICOMP < 2 μA, V(COMP) = 1 V. | unsupported unit preserved without conversion | 5 |
| `B002_REQ_016` | Switch current to COMP transconductance is 12 A/V typical at VIN = 12 V. | Switch-current-to-COMP transconductance is an internal current-mode-control gain in A/V. No supported measurement operation directly yields a transconductance ratio from a single fixture; measuring peak switch current at one COMP voltage would require post-processing to divide by V(COMP), and the datasheet does not provide a unique zero-current offset or test condition to derive the slope from one point. Hence unsupported as a direct LTspice measurement. | 5 |
| `B002_REQ_019` | Thermal shutdown temperature is 165 °C typical. | Thermal shutdown temperature (165 °C typical) is a thermal characteristic requiring controlled die/ambient temperature beyond the allowed nominal 25 C LTspice fixture. Temperature is limited to 25 C here, so this is an explicit gap. | 5 |
| `B002_REQ_021` | SS to VSENSE matching is 10 mV max at V(SS) = 0.4 V. | SS-to-VSENSE matching is an internal error-amplifier/reference offset specified at V(SS)=0.4 V. Establishing it requires closed-loop or servo measurement of an internal offset between two internal nodes, which is not directly available as a pin voltage/current measurement with the supported operations; the datasheet also provides only a max limit without an explicit test circuit. | 5 |
| `B002_REQ_022` | TPS54332 switching frequency is 800 kHz min, 1000 kHz typ, 1200 kHz max at VIN = 12 V, 25°C. (1) Specified by design. | Switching frequency is a frequency-domain quantity. Supported LTspice measurement operations include delay (time) but no operation converts a measured period to Hz or directly measures frequency; using delay would yield seconds and not the normalized requirement unit Hz. Therefore this is an explicit gap. | 5 |
| `B002_REQ_024` | Maximum controllable duty ratio is 90% min, 93% typ at BOOT-PH = 6 V. (1) Specified by design. | Maximum controllable duty ratio is a percentage that requires simultaneous measurement of on-time and period (or a direct duty-cycle operation). Supported operations can measure a pulse time but not a ratio/percentage; additionally the condition BOOT-PH=6 V requires forcing a nonstandard bootstrap voltage. Hence unsupported as a direct fixture with the allowed operations. | 5 |
| `B002_REQ_025` | Continuous output current up to 3.5 A. | citation_unverified: test circuit cannot be grounded in the cited page | 8 |
| `B002_REQ_027` | Requires a 0.1-μF ceramic capacitor between the BOOT and PH pin. | This is an external component requirement (0.1 µF bootstrap capacitor between BOOT and PH), not a DUT electrical characteristic. It is included as a fixture component in switching recipes but cannot be a standalone LTspice measurement of the device. | 9 |
| `B002_REQ_028` | EN pin has an internal pullup current source; device operates by default when EN pin floats. | unknown classification or absolute stress rating, not an operating target | 9 |
| `B002_REQ_029` | After EN voltage exceeds 1.25 V, an additional 3 μA of hysteresis is added. | The 3 uA is a change in EN pin input current between disabled and enabled states; the available measurement primitives cannot compute a two-bias current difference on a single DUT, and hysteresis operation measures voltage thresholds, not current. | 10 |
| `B002_REQ_030` | VSTOP must always be greater than 3.5 V. | no numeric limit; this fixture interpreter measures numeric characteristics | 10 |
| `B002_REQ_031` | Slow-start time must be set between 1 ms and 10 ms. | Slow-start time between 1 ms and 10 ms is a design constraint on the external SS capacitor, not a directly measurable DUT electrical characteristic; it should be a fixture condition, not an output measurement. | 10 |
| `B002_REQ_032` | Slow-start capacitor must be no more than 27 nF. | Slow-start capacitor maximum 27 nF is an external component selection limit, not a DUT pin electrical characteristic measurable by the fixture. | 10 |
| `B002_REQ_033` | TPS54332 stops switching if input voltage drops below VIN UVLO threshold, or EN pin is pulled below 1.25 V, or a thermal shutdown event occurs. | unknown classification or absolute stress rating, not an operating target | 10 |
| `B002_REQ_034` | Built-in slope compensation is added to the switch current signal to prevent sub-harmonic oscillations when operating at duty cycles greater than 50%. | unknown classification or absolute stress rating, not an operating target | 10 |
| `B002_REQ_036` | Frequency foldback reduces switching frequency during start-up and overcurrent conditions. | unknown classification or absolute stress rating, not an operating target | 8 |
| `B002_REQ_037` | A ceramic capacitor with X7R or X5R grade dielectric is recommended for the BOOT to PH capacitor. | unknown classification or absolute stress rating, not an operating target | 9 |
| `B002_REQ_038` | Output voltage can be stepped down to as low as the reference voltage. | unknown classification or absolute stress rating, not an operating target | 8 |
| `B002_REQ_039` | Operates at 100% duty cycle as long as BOOT to PH pin voltage is greater than 2.1 V typically. | unknown classification or absolute stress rating, not an operating target | 9 |
| `B002_REQ_040` | TI recommends using an external VIN UVLO to add hysteresis unless VIN is greater than (VOUT + 2 V). | unknown classification or absolute stress rating, not an operating target | 9 |
| `B003_REQ-001` | TPS54332 recommended input voltage range is 3.5 V to 28 V. | operating envelope defines fixture conditions; it is not a measured DUT response | 13 |
| `B003_REQ-002` | TPS54332 maximum output current IO(maximum) is 3.5 A. | Maximum output current 3.5 A is a recommended continuous load condition, not a directly measurable DUT parameter; use as fixture condition, not a standalone measurement. | 13 |
| `B003_REQ-003` | TPS54332 typical switching frequency is 1000 kHz; the switching frequency is fixed at 1 MHz. | No frequency-counting or frequency-output measurement operation is available; delay returns time, and unit conversion to kHz would require inverse scaling not supported. | 13 |
| `B003_REQ-006` | With EN held below the enable threshold, the device is disabled and switching is inhibited even if VIN is above its UVLO threshold; IC quiescent current is reduced in this state. | unknown classification or absolute stress rating, not an operating target | 11 |
| `B003_REQ-007` | If EN voltage is increased above the enable threshold while VIN is above its UVLO threshold, the device becomes active, switching is enabled, and the slow-start sequence is initiated. | unknown classification or absolute stress rating, not an operating target | 12 |
| `B003_REQ-008` | The typical VIN UVLO threshold is not specified; the device can operate at input voltages down to the UVLO voltage. At input voltages below the actual UVLO voltage, the device does not switch. | unknown classification or absolute stress rating, not an operating target | 11 |
| `B003_REQ-009` | If EN is externally pulled up or left floating, when VIN passes the UVLO threshold the device becomes active. Switching commences when the soft-start sequence is initiated. | unknown classification or absolute stress rating, not an operating target | 11 |
| `B003_REQ-011` | Peak inductor current must rise above 160 mA for COMP pin voltage to rise above 0.5 V and exit Eco-mode. | no numeric limit; this fixture interpreter measures numeric characteristics | 12 |
| `B003_REQ-012` | The COMP pin has a maximum internal clamp, which limits the output current during overcurrent conditions. | unknown classification or absolute stress rating, not an operating target | 11 |
| `B003_REQ-013` | Switching frequency is 1 MHz when VSENSE ≥ 0.6 V. | no numeric limit; this fixture interpreter measures numeric characteristics | 11 |
| `B003_REQ-014` | Switching frequency is 1 MHz / 2 when 0.6 V > VSENSE ≥ 0.4 V. | no numeric limit; this fixture interpreter measures numeric characteristics | 11 |
| `B003_REQ-015` | Switching frequency is 1 MHz / 4 when 0.4 V > VSENSE ≥ 0.2 V. | no numeric limit; this fixture interpreter measures numeric characteristics | 11 |
| `B003_REQ-016` | Switching frequency is 1 MHz / 8 when 0.2 V > VSENSE. | no numeric limit; this fixture interpreter measures numeric characteristics | 11 |
| `B003_REQ-019` | If junction temperature exceeds 165°C, internal thermal shutdown forces the device to stop switching. | no numeric limit; this fixture interpreter measures numeric characteristics | 11 |
| `B003_REQ-020` | After die temperature decreases below 165°C, the device reinitiates the power-up sequence. | no numeric limit; this fixture interpreter measures numeric characteristics | 11 |
| `B003_REQ-021` | TPS54332 implements current mode control that uses the COMP pin voltage to turn off the high-side MOSFET on a cycle-by-cycle basis. | unknown classification or absolute stress rating, not an operating target | 11 |
| `B003_REQ-022` | Output voltage is externally adjustable using a resistor divider network; VOUT relationship is given by VOUT = VREF × (R5/R6 + 1) using external resistors R5 and R6. | unknown classification or absolute stress rating, not an operating target | 14 |
| `B003_REQ-023` | Average load current entering Eco-mode varies with the application and external output filters because the integrated current comparator catches the peak inductor current only. | unknown classification or absolute stress rating, not an operating target | 12 |
| `B004_REQ_TPS54332DDA_ROA_8P696M` | Error amplifier output resistance ROA is 8.696 MΩ. | citation_unverified: test circuit cannot be grounded in the cited page | 17 |
| `B004_REQ_TPS54332DDA_GMCOMP_12AV` | Error amplifier transconductance GMCOMP is 12 A/V. | Quantity is ambiguous/conflicting: context associates 12 A/V with switch current to COMP transconductance, not error amp gm (92 µS); internal switch-current signal is not accessible at a pin, so no established signal-to-pin mapping. | 18 |
| `B004_REQ_TPS54332DDA_RSENSE_1OVER12` | Current sense resistance RSENSE is 1 Ω / 12 (approximately 0.083333 Ω). | citation_unverified: test circuit cannot be grounded in the cited page | 17 |
| `B004_REQ_TPS54332DDA_FCO_MAX_75KHZ` | TI recommends that the maximum closed-loop crossover frequency be not greater than 75 kHz; internal circuit limitations limit the practical maximum crossover frequency to about 75 kHz. | System-level recommendation for closed-loop crossover frequency; requires a complete compensated power-stage design not specified in evidence; not a directly measurable DUT pin characteristic. | 17 |
| `B004_REQ_TPS54332DDA_FCO_LT_1_8_FSW_MIN` | The closed-loop crossover frequency must be less than 1/8 of the minimum operating frequency. | System-level relative guideline depending on minimum operating frequency and closed-loop design; no established testable pin mapping or compensation values. | 17 |
| `B005_REQ-BOOTSTRAP-CAP` | Every TPS54332 design requires a bootstrap capacitor, C4, of 0.1 μF located between the PH pins and BOOT pin; the capacitor must be high-quality ceramic with X7R or X5R grade dielectric for temperature stability. | External bootstrap capacitor value/placement requirement, not a DUT output measurement; capacitance cannot be verified at DUT pins. | 19 |
| `B005_REQ-CATCH-DIODE-CONNECTION` | The TPS54332 is designed to operate using an external catch diode between PH and GND. | unknown classification or absolute stress rating, not an operating target | 19 |
| `B005_REQ-PH-ABS-MAX-VOLTAGE` | The maximum voltage at the PH pin is VIN(max) + 0.5 V, used as the application absolute maximum rating for catch diode reverse voltage. | unknown classification or absolute stress rating, not an operating target | 19 |
| `B005_REQ-CATCH-DIODE-REVERSE-VOLTAGE` | The selected catch diode reverse voltage must be higher than the maximum voltage at the PH pin, which is VIN(max) + 0.5 V. | External catch diode selection requirement depending on application VIN(max); no DUT pin measurement establishes diode reverse voltage. | 19 |
| `B005_REQ-CATCH-DIODE-PEAK-CURRENT` | Peak current must be greater than IOUTMAX plus one half the peak-to-peak inductor current (text says 'on half'). | unknown classification or absolute stress rating, not an operating target | 19 |
| `B005_REQ-CATCH-DIODE-FORWARD-DROP` | Forward-voltage drop must be small for higher efficiencies. | unknown classification or absolute stress rating, not an operating target | 19 |
| `B005_REQ-CATCH-DIODE-POWER` | Check that the selected device is capable of dissipating the power losses. | unknown classification or absolute stress rating, not an operating target | 19 |
| `B005_REQ-VO-MAX-EQ32` | VO MAX = 0.91 × VIN MIN − IO MAX × RDS ON MAX + VD − IO MAX × RL − VD (Equation 32). | unknown classification or absolute stress rating, not an operating target | 19 |
| `B005_REQ-VO-MIN-EQ33` | VO MIN = 0.118 × VIN MAX − IO MIN × RDS ON MAX + VD − IO MIN × RL − VD (Equation 33). | unknown classification or absolute stress rating, not an operating target | 20 |
| `B005_REQ-OPERATIONAL-LIMITS-CHECK` | Any design operating near the operational limits of the device must be carefully checked to assure proper functionality. | unknown classification or absolute stress rating, not an operating target | 20 |
| `B005_REQ-POWER-DISSIPATION-CCM-ONLY` | The power dissipation estimate formulas must not be used if the device is working in discontinuous conduction mode (DCM) or pulse-skipping Eco-mode. | unknown classification or absolute stress rating, not an operating target | 20 |
| `B005_REQ-PCON-FORMULA` | Conduction loss: Pcon = Iout^2 × RDS(on) × VOUT/VIN. | unknown classification or absolute stress rating, not an operating target | 20 |
| `B005_REQ-PSW-FORMULA` | Switching loss: Psw = 0.55 × 10-9 × VIN^2 × IOUT × Fsw. | unknown classification or absolute stress rating, not an operating target | 20 |
| `B005_REQ-PGC-FORMULA` | Gate charge loss: Pgc = 22.8 × 10-9 × Fsw. | unknown classification or absolute stress rating, not an operating target | 20 |
| `B005_REQ-PQ-FORMULA` | Quiescent current loss: Pq = 0.082 × 10-3 × VIN. | unknown classification or absolute stress rating, not an operating target | 20 |
| `B005_REQ-PTOT-FORMULA` | Ptot = Pcon + Psw + Pgc + Pq. | unknown classification or absolute stress rating, not an operating target | 20 |
| `B005_REQ-TJ-FORMULA` | For given TA, TJ = TA + Rth × Ptot. | unknown classification or absolute stress rating, not an operating target | 20 |
| `B005_REQ-TAMAX-FORMULA` | For given TJMAX = 150°C, TAMAX = TJMAX − Rth × Ptot. | unknown classification or absolute stress rating, not an operating target | 20 |
| `B005_REQ-TJMAX-150C` | Maximum junction temperature TJMAX is 150°C (used for power dissipation estimate). | unknown classification or absolute stress rating, not an operating target | 20 |
| `B005_REQ-VIN-RANGE` | The devices are designed to operate from an input voltage supply range between 3.5 V and 28 V. | Recommended operating input-voltage range defines fixture conditions, not an output measurement; wider temperature range not verified at 25 C. | 22 |
| `B005_REQ-VIN-REGULATION` | This input supply must be well regulated. | unknown classification or absolute stress rating, not an operating target | 22 |
| `B005_REQ-INPUT-BULK-CAP` | If the input supply is located more than a few inches from the converter, additional bulk capacitance can be required in addition to the ceramic bypass capacitors. | unknown classification or absolute stress rating, not an operating target | 22 |
| `B005_REQ-INPUT-BULK-CAP-TYP` | An electrolytic capacitor with a value of 100 μF is a typical choice for additional bulk capacitance. | Typical external input bulk capacitor value, not a DUT measured characteristic. | 22 |
| `B005_REQ-VIN-BYPASS` | The VIN pin must be bypassed to ground with a low-ESR, ceramic bypass capacitor. | unknown classification or absolute stress rating, not an operating target | 22 |
| `B005_REQ-VIN-BYPASS-CAP-TYP` | The typical recommended bypass capacitance is 10 μF ceramic with an X5R or X7R dielectric, placed closest to the VIN pins and the source of the anode of the catch diode. | Typical external VIN bypass capacitor value, not a DUT measured characteristic. | 22 |
| `B005_REQ-GND-D-CONNECT` | The GND D pin must be tied to the PCB ground plane at the pin of the IC. | unknown classification or absolute stress rating, not an operating target | 22 |
| `B005_REQ-LOW-SIDE-SOURCE-CONNECT` | The source of the low-side MOSFET must be connected directly to the top-side PCB ground area used to tie together the ground sides of the input and output capacitors, as well as the anode of the catch diode. (Supplied TPS54332 page text; applicability to TPS54332DDA is as stated.) | unknown classification or absolute stress rating, not an operating target | 22 |
| `B005_REQ-PH-ROUTING` | The PH pin must be routed to the cathode of the catch diode and to the output inductor. | unknown classification or absolute stress rating, not an operating target | 22 |
| `B005_REQ-PH-LAYOUT` | Because the PH connection is the switching node, the catch diode and output inductor must be located very close to the PH pins, and the area of the PCB conductor minimized to prevent excessive capacitive coupling. | citation_unverified: test circuit cannot be grounded in the cited page | 22 |
| `B005_REQ-THERMAL-GROUND-AREA` | For operation at full rated load, the top-side ground area must provide adequate heat dissipating area. | unknown classification or absolute stress rating, not an operating target | 23 |
| `B005_REQ-GND-HEAT-PATH` | The TPS54332 uses a fused lead frame so that the GND pin acts as a conductive path for heat dissipation from the die. | unknown classification or absolute stress rating, not an operating target | 23 |
| `B005_REQ-THERMAL-VIAS` | Many applications have larger areas of internal or back side ground plane available, and the top-side ground area can be connected to these areas using multiple vias under or adjacent to the device to help dissipate heat. | unknown classification or absolute stress rating, not an operating target | 23 |
| `B005_REQ-LAYOUT-GUIDELINE` | It can be possible to obtain acceptable performance with alternate layout schemes; however this layout has been shown to produce good results and is intended as a guideline. | unknown classification or absolute stress rating, not an operating target | 23 |
| `B005_REQ-EMI-INTERNAL` | The internal design of the TPS54332 takes measures to reduce EMI: high-side MOSFET gate-drive is designed to reduce PH pin voltage ringing; internal IC rails are isolated to decrease noise sensitivity; a package bond wire scheme is used to lower parasitics effects. | unknown classification or absolute stress rating, not an operating target | 23 |
| `B005_REQ-EMI-EXTERNAL` | To achieve the best EMI performance, external component selection and board layout are equally important; follow the Detailed Design Procedure to prevent potential EMI issues. | unknown classification or absolute stress rating, not an operating target | 23 |

## Scope

- Behavioural model: it reproduces the judged rows above at the stated conditions and is not a transistor-level replica of the silicon.
- Anything not on this card (thermal behaviour, internal oscillator artifacts, EMI, fault timing corners, absolute-maximum survival) is outside the model's claimed scope.
- Exported files reference no vendor data unless the card says otherwise.

## Reproduce

```
uv run boardmodeler model test --out <this directory>
```

The harness reruns every probe in a fresh LTspice batch and rewrites this card.
