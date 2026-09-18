# Adaptation report

- source sha256: `b979024ddd6f8d158c5d5454985f74e4f8c97c75e6b9aedff1703bd0418a9268`
- adapted sha256: `752ba09f934886fb4d216810b004ca60d8785ee1eea750cecc948ac577b70a6d`
- tool: `boardmodeler.models.adapt`
- created: 2026-09-18T04:40:33Z

## Changes

- folded PSpice '+' continuation lines into 508 logical cards
- switch model _U8_S35: PSpice VSWITCH(Voff=0.2, Von=0.8) -> LTspice SW(Vt=0.5, Vh=0.3) [Vh is the half-band]
- switch model _U8_S31: PSpice VSWITCH(Voff=0.2, Von=0.8) -> LTspice SW(Vt=0.5, Vh=0.3) [Vh is the half-band]
- switch model _U8_S30: PSpice VSWITCH(Voff=0.2, Von=0.8) -> LTspice SW(Vt=0.5, Vh=0.3) [Vh is the half-band]
- switch model _U3_S26: PSpice VSWITCH(Voff=0.2, Von=0.8) -> LTspice SW(Vt=0.5, Vh=0.3) [Vh is the half-band]
- switch model _U3_S27: PSpice VSWITCH(Voff=0.2, Von=0.8) -> LTspice SW(Vt=0.5, Vh=0.3) [Vh is the half-band]
- switch model _U9_S19: PSpice VSWITCH(Voff=0.2, Von=0.8) -> LTspice SW(Vt=0.5, Vh=0.3) [Vh is the half-band]
- switch model _U4_S68: PSpice VSWITCH(Voff=0.2, Von=0.8) -> LTspice SW(Vt=0.5, Vh=0.3) [Vh is the half-band]
- switch model _U6_S2: PSpice VSWITCH(Voff=0.2, Von=0.8) -> LTspice SW(Vt=0.5, Vh=0.3) [Vh is the half-band]
- switch model _U6_S1: PSpice VSWITCH(Voff=0.2, Von=0.8) -> LTspice SW(Vt=0.5, Vh=0.3) [Vh is the half-band]
- switch model _u10_s2: PSpice VSWITCH(Voff=0.2, Von=0.8) -> LTspice SW(Vt=0.5, Vh=0.3) [Vh is the half-band]
- converted 59 PSpice ABM 'VALUE { {...} }' expressions to LTspice 'VALUE={...}'
- converted 10 VSWITCH models to LTspice SW
- stripped the 'Vdc' unit suffix from 8 cards
- raised the emission coefficient of 1 diode model(s) from N<0.05 to N=0.1 (LTspice fails with 'Time step too small' on PSpice N=0.01)
- added solver options (method=gear, trtol=10, gmin/abstol/vntol relaxed) because the ported model otherwise fails with 'Time step too small' at the initial timepoint

## Explicitly unchanged

- subcircuit port order and node names are unchanged
- the vendor's internal topology and parameter values are unchanged except as listed
- no behavioural claim about the model was derived from this transformation
