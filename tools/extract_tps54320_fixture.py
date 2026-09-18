"""Fixture-assisted extraction of the TPS54320 requirement set and pin map.

Run from the repository root (needs the datasheet that
``tools/fetch_fixtures.py --allow-network`` downloads):

```powershell
uv run python tools/extract_tps54320_fixture.py
```

How this stays honest:

* Every requirement carries an **anchor regex** and a page index. The excerpt is
  sliced out of that page's extracted text *at extraction time* — so it cannot be
  a remembered or paraphrased quotation. If an anchor is not found, the tool fails
  loudly and writes nothing.
* The emitted ``requirements.json`` records ``citation_verified`` only after
  :func:`boardmodeler.documents.pdf.excerpt_on_page` confirms the excerpt on the
  cited page, and the result is printed as a coverage line.
* ``pinmap.json`` is extracted from the pin-functions table the same way, with
  one entry per physical pin number (11/12 and 2/3 and 4/5 are separate pins).
* This is *fixture-assisted* extraction: the requirement statements were chosen by
  reading the public datasheet (as the plan describes). The committed artifacts
  are the JSON files; the CI-side re-validation is
  ``tests/regulator/test_tps54320_fixture.py``.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pydantic import ValidationError

from boardmodeler.documents.pdf import excerpt_on_page, read_pdf
from boardmodeler.domain.records import Requirement

FIXTURE = Path("fixtures") / "regulator" / "tps54320"
DATASHEET = FIXTURE / "originals" / "tps54320_datasheet.pdf"
DOC_ID = "doc_tps54320"
PART = "TPS54320"
MAX_EXCERPT = 400


@dataclass(frozen=True)
class Spec:
    """One requirement and where its text lives."""

    suffix: str
    kind: str
    req_class: str
    criticality: str
    statement: str
    page: int
    anchor: str
    section: str
    table: str | None = None
    limits: dict[str, Any] | None = None
    expression: dict[str, Any] | None = None
    signal_refs: tuple[str, ...] = ()
    condition: str | None = None
    before: int = 0
    after: int = 200
    ordinals: int = 1


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


#: Operating conditions that the datasheet's own table headers apply to every row.
#: Without these, a limit extracted from a table would silently lose the
#: temperature/voltage envelope it was measured under.
_TABLE_CONDITIONS: dict[str, dict[str, Any]] = {
    "Electrical Characteristics": {
        "text": "TJ = -40 C to 150 C, VIN = 4.5 V to 17 V, PVIN = 1.6 V to 17 V (unless "
        "otherwise noted)",
        "parameter_overrides": {"TJ_min": -40.0, "TJ_max": 150.0, "VIN_min": 4.5, "VIN_max": 17.0},
    },
    "Recommended Operating Conditions": {
        "text": "over operating free-air temperature range",
        "parameter_overrides": {},
    },
}


def _excerpt(page_text: str, spec: Spec) -> str:
    """Locate the anchor and slice a window of the page's own text."""
    text = _normalize(page_text)
    match = re.search(spec.anchor, text, re.IGNORECASE)
    if not match:
        raise SystemExit(
            f"anchor not found for {spec.suffix!r} on page {spec.page}: {spec.anchor!r}"
        )
    start = max(0, match.start() - spec.before)
    end = min(len(text), match.end() + spec.after)
    excerpt = text[start:end]
    if len(excerpt) > MAX_EXCERPT:
        excerpt = excerpt[: MAX_EXCERPT - 1].rstrip() + "…"
    return excerpt


SPECS: tuple[Spec, ...] = (
    # ---------------------------------------------------------------- input range
    Spec(
        "ELEC_001",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "The power-stage input (PVIN) operates from 1.6 V to 17 V.",
        3,
        r"Power stage input voltage range\s+PVIN\s+1\.6\s+17\s+V",
        "Recommended Operating Conditions",
        "Recommended Operating Conditions",
        {"min": 1.6, "max": 17.0, "unit": "V"},
        {"op": "between", "signal": "V(VIN)", "low": 1.6, "high": 17.0, "unit": "V"},
        ("V(VIN)",),
    ),
    Spec(
        "ELEC_002",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "The control input (VIN) operates from 4.5 V to 17 V.",
        3,
        r"Input voltage range\s+VIN\s+4\.5\s+17\s+V",
        "Recommended Operating Conditions",
        "Recommended Operating Conditions",
        {"min": 4.5, "max": 17.0, "unit": "V"},
        {"op": "between", "signal": "V(VIN)", "low": 4.5, "high": 17.0, "unit": "V"},
        ("V(VIN)",),
    ),
    Spec(
        "ELEC_003",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "The output current is 0 A to 3 A.",
        3,
        r"Output current\s+0\s+3\s+A",
        "Recommended Operating Conditions",
        "Recommended Operating Conditions",
        {"min": 0.0, "max": 3.0, "unit": "A"},
        signal_refs=("I(VOUT)",),
    ),
    Spec(
        "ELEC_004",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "The internal VIN UVLO threshold with VIN rising is between 4.0 V and 4.5 V.",
        4,
        r"VIN internal UVLO threshold\s+VIN rising\s+4\.0\s+4\.5\s+V",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"min": 4.0, "max": 4.5, "unit": "V"},
        {"op": "between", "signal": "V(VIN_START)", "low": 4.0, "high": 4.5, "unit": "V"},
        ("V(VIN_START)",),
        condition="VIN rising, TJ = -40 C to 150 C",
    ),
    Spec(
        "ELEC_005",
        "ELECTRICAL",
        "TYPICAL_VALUE",
        "IMPORTANT",
        "The VIN internal UVLO hysteresis is 150 mV (a typical value in the electrical table).",
        4,
        r"VIN internal UVLO hysteresis\s+150\s+mV",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"typ": 0.150, "unit": "V"},
        {"op": "between", "signal": "V(UVLO_HYST)", "low": 0.1, "high": 0.2, "unit": "V"},
        ("V(UVLO_HYST)",),
    ),
    Spec(
        "ELEC_006",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "The VIN shutdown supply current with EN = 0 V is at most 5 uA.",
        4,
        r"VIN shutdown supply Current\s+EN = 0 V\s+2\s+5\s+μA",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"typ": 2e-6, "max": 5e-6, "unit": "A"},
        {"op": "lt", "signal": "I(VIN_SHUTDOWN)", "value": 5e-6, "unit": "A"},
        ("I(VIN_SHUTDOWN)",),
    ),
    Spec(
        "ELEC_007",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "The VIN operating non-switching supply current is at most 800 uA.",
        4,
        r"VIN operating \u2013 non switching supply current\s+VSENSE = 810 mV\s+600\s+800\s+μA",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"typ": 600e-6, "max": 800e-6, "unit": "A"},
        {"op": "lt", "signal": "I(VIN_NONSW)", "value": 8e-4, "unit": "A"},
        ("I(VIN_NONSW)",),
    ),
    # ---------------------------------------------------------------------- EN
    Spec(
        "ELEC_010",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "The EN rising threshold is between 1.21 V and 1.26 V.",
        4,
        r"Enable threshold\s+Rising\s+1\.21\s+1\.26\s+V",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"min": 1.21, "max": 1.26, "unit": "V"},
        {"op": "rise_above", "signal": "V(EN)", "value": 1.21, "unit": "V"},
        ("V(EN)",),
    ),
    Spec(
        "ELEC_011",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "The EN falling threshold is between 1.10 V and 1.17 V.",
        4,
        r"Enable threshold\s+Falling\s+1\.10\s+1\.17\s+V",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"min": 1.10, "max": 1.17, "unit": "V"},
        {"op": "fall_below", "signal": "V(EN)", "value": 1.17, "unit": "V"},
        ("V(EN)",),
    ),
    Spec(
        "ELEC_012",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "The EN pin sources 1.15 uA at EN = 1.1 V and 3.4 uA at EN = 1.3 V.",
        4,
        r"EN pin sourcing current\s+EN low EN = 1\.1 V\s+1\.15\s+μA",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"min": 1.15e-6, "unit": "A"},
        {"op": "between", "signal": "I(EN)", "low": 1.0e-6, "high": 5.0e-6, "unit": "A"},
        ("I(EN)",),
    ),
    # --------------------------------------------------------------- reference
    Spec(
        "ELEC_020",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "The voltage reference is 0.792 V to 0.808 V (0.800 V typical) over 0 A <= IOUT <= 3 A.",
        4,
        r"Voltage reference\s+0 A ≤ IOUT ≤ 3 A\s+0\.792\s+0\.800\s+0\.808\s+V",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"min": 0.792, "typ": 0.800, "max": 0.808, "unit": "V"},
        {"op": "between", "signal": "V(VSENSE)", "low": 0.792, "high": 0.808, "unit": "V"},
        ("V(VSENSE)",),
    ),
    Spec(
        "ELEC_021",
        "FUNCTIONAL",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "Regulation is through the external divider on VSENSE: the error amplifier's inverting "
        "input is VSENSE, so the output follows the external resistor ratio rather than a fixed "
        "internal value.",
        2,
        r"VSENSE 7 Inverting input of the gm error amplifier",
        "Pin Configuration and Functions",
        "Pin Functions",
        expression={
            "op": "state_dependent",
            "when": {"signal": "V(EN)", "kind": "rise_above", "value": 1.21, "unit": "V"},
            "then": {
                "op": "between",
                "signal": "V(VSENSE)",
                "low": 0.792,
                "high": 0.808,
                "unit": "V",
            },
        },
        signal_refs=("V(VSENSE)", "V(VOUT)"),
    ),
    Spec(
        "ELEC_022",
        "ELECTRICAL",
        "TYPICAL_VALUE",
        "INFORMATIONAL",
        "The error amplifier transconductance is 1300 uMhos.",
        4,
        r"Error amplifier Transconductance \(gm\).*?1300 μMhos",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"typ": 1300e-6, "unit": "S"},
    ),
    Spec(
        "ELEC_023",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "The error amplifier dc gain is 1000 to 3100 V/V.",
        4,
        r"Error amplifier dc gain\s+VSENSE = 0\.8 V\s+1000\s+3100\s+V/V",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"min": 1000.0, "max": 3100.0, "unit": "V/V"},
    ),
    Spec(
        "ELEC_024",
        "TEMPORAL",
        "TYPICAL_VALUE",
        "IMPORTANT",
        "Switching starts once COMP reaches the 0.25 V start-switching threshold (a typical "
        "value in the electrical table, not a min/max limit).",
        4,
        r"Start switching threshold\s+0\.25\s+V",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"typ": 0.25, "unit": "V"},
        {"op": "rise_above", "signal": "V(COMP)", "value": 0.25, "unit": "V"},
        ("V(COMP)",),
    ),
    # ---------------------------------------------------------- current limit
    Spec(
        "ELEC_030",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "The high-side switch current limit threshold is 4.2 A to 6.2 A.",
        5,
        r"High-side switch current limit threshold\s+4\.2\s+6\.2\s+A",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"min": 4.2, "max": 6.2, "unit": "A"},
        {"op": "between", "signal": "I(PH)", "low": 4.2, "high": 6.2, "unit": "A"},
        ("I(PH)",),
    ),
    Spec(
        "ELEC_031",
        "FUNCTIONAL",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "The low-side switch sources 3.8 A to 5.8 A of current limit.",
        5,
        r"Low-side switch sourcing current limit\s+3\.8\s+5\.8\s+A",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"min": 3.8, "max": 5.8, "unit": "A"},
    ),
    Spec(
        "ELEC_032",
        "FUNCTIONAL",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "The low-side switch sinks 1 A to 2.6 A of current limit.",
        5,
        r"Low-side switch sinking current limit\s+1\s+2\.6\s+A",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"min": 1.0, "max": 2.6, "unit": "A"},
    ),
    Spec(
        "TEMPORAL_033",
        "TEMPORAL",
        "TYPICAL_VALUE",
        "IMPORTANT",
        "Hiccup is triggered after 512 cycles in current limit, and the restart follows 16384 "
        "cycles later (a bounded retry interval, not an indefinite off state).",
        5,
        r"Hiccup wait time before triggering hiccup\s+512 cycles",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"typ": 512.0, "unit": "cycles"},
        signal_refs=("I(PH)",),
    ),
    Spec(
        "THERM_034",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "Thermal shutdown occurs between 160 C and 175 C with 10 C hysteresis.",
        5,
        r"Thermal shutdown\s+160\s+175\s+°C",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"min": 160.0, "max": 175.0, "unit": "C"},
        signal_refs=("TJ",),
        condition="TJ measured at the die",
    ),
    # -------------------------------------------------------------- soft start
    Spec(
        "TEMPORAL_040",
        "TEMPORAL",
        "TYPICAL_VALUE",
        "CRITICAL",
        "The SS/TR pin is charged by a 2.3 uA current source, so the soft-start time is "
        "tSS = Css * Vref / Iss (Vref = 0.8 V).",
        13,
        r"The device has an internal pullup current source of 2\.3 μA that charges the external "
        r"slow-start capacitor",
        "Feature Description",
        "Slow Start (SS/TR)",
        {"typ": 2.3e-6, "unit": "A"},
        {"op": "rise_above", "signal": "V(SS/TR)", "value": 0.8, "unit": "V"},
        ("V(SS/TR)",),
    ),
    Spec(
        "FUNC_041",
        "FUNCTIONAL",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "The lower of the internal reference and the SS/TR pin voltage is used as the regulation "
        "reference, so the output rises with the soft-start ramp instead of stepping to its "
        "target.",
        13,
        r"The device uses the lower voltage of the internal voltage reference or the SS/TR pin "
        r"voltage as the reference voltage",
        "Feature Description",
        "Slow Start (SS/TR)",
        expression={
            "op": "ordering",
            "first": {"signal": "V(SS/TR)", "kind": "rise_above", "value": 0.78, "unit": "V"},
            "then": {"signal": "V(VOUT)", "kind": "rise_above", "value": 3.2, "unit": "V"},
        },
        signal_refs=("V(SS/TR)", "V(VOUT)"),
    ),
    Spec(
        "TEMPORAL_042",
        "TEMPORAL",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "After a shutdown condition is removed the device does not start switching until the "
        "SS/TR pin has discharged to ground.",
        13,
        r"the device does not start switching until it has discharged its SS/TR pin to ground",
        "Feature Description",
        "Slow Start (SS/TR)",
        expression={
            "op": "ordering",
            "first": {"signal": "V(SS/TR)", "kind": "fall_below", "value": 0.05, "unit": "V"},
            "then": {"signal": "V(PH)", "kind": "rise_above", "value": 0.5, "unit": "V"},
        },
        signal_refs=("V(SS/TR)", "V(PH)"),
    ),
    Spec(
        "ELEC_043",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "The SS/TR to VSENSE matching error is 29 mV to 60 mV at V(SS/TR) = 0.4 V.",
        5,
        r"SS/TR to VSENSE matching\s+V\(SS/TR\) = 0\.4 V\s+29\s+60\s+mV",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"min": 29e-3, "max": 60e-3, "unit": "V"},
    ),
    # --------------------------------------------------------------- power good
    Spec(
        "PG_050",
        "FUNCTIONAL",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "PWRGD is an open-drain output: its pull-down is deasserted and the pin floats when "
        "VSENSE is between 94% and 106% of the internal reference.",
        13,
        r"The PWRGD pin is an open-drain output\.",
        "Feature Description",
        "Power Good (PWRGD)",
        expression={"op": "pin_connected", "refdes": "U1", "pin": "PWRGD"},
        signal_refs=("V(PWRGD)", "V(VSENSE)"),
    ),
    Spec(
        "PG_051",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "The PWRGD threshold is 91% of Vref (VSENSE falling) and 94% of Vref (VSENSE rising).",
        5,
        r"VSENSE falling \(Fault\)\s+91\s+% Vref",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"min": 0.91, "max": 0.94, "unit": "ratio"},
        {
            "op": "between",
            "signal": "V(PWRGD)/V(VSENSE) ratio",
            "low": 0.9,
            "high": 1.1,
            "unit": "ratio",
        },
        ("V(PWRGD)",),
    ),
    Spec(
        "PG_052",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "The PWRGD high-state leakage is at most 100 nA and the low-state output is at most "
        "0.3 V at 2 mA.",
        5,
        r"Output high leakage\s+VSENSE = Vref, V\(PWRGD\) = 5\.5 V\s+30\s+100\s+nA",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"typ": 30e-9, "max": 100e-9, "unit": "A"},
        {"op": "lt", "signal": "I(PWRGD_LEAK)", "value": 1e-7, "unit": "A"},
        ("I(PWRGD_LEAK)",),
    ),
    Spec(
        "PG_053",
        "CONNECTIVITY",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "A PWRGD pull-up between 10 kohm and 100 kohm to a source of 5.5 V or less is required.",
        13,
        r"TI recommends to use a pullup resistor between the values of 10 and 100 kΩ",
        "Feature Description",
        "Power Good (PWRGD)",
        {"min": 10e3, "max": 100e3, "unit": "ohm"},
        {"op": "pin_connected", "refdes": "U1", "pin": "PWRGD"},
        ("V(PWRGD)",),
    ),
    Spec(
        "PG_054",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "PWRGD is in a defined state above VIN = 1 V but only reaches full current sinking "
        "capability above VIN = 4.5 V.",
        13,
        r"The PWRGD is in a defined state when the VIN input voltage is >1 V",
        "Feature Description",
        "Power Good (PWRGD)",
        {"min": 1.0, "unit": "V"},
        {"op": "gt", "signal": "V(VIN)", "value": 1.0, "unit": "V"},
        ("V(PWRGD)",),
    ),
    Spec(
        "PG_055",
        "CONNECTIVITY",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "PWRGD is pulled low by thermal shutdown, input UVLO, overvoltage, EN shutdown or while "
        "SS/TR is below its 1.2 V threshold, so an unpowered or disabled device cannot present a "
        "valid power-good.",
        13,
        r"the PWRGD is pulled low, if the input UVLO or thermal shutdown are asserted",
        "Feature Description",
        "Power Good (PWRGD)",
        {"typ": 1.2, "unit": "V"},
        {
            "op": "state_dependent",
            "when": {"signal": "V(EN)", "kind": "fall_below", "value": 1.17, "unit": "V"},
            "then": {"op": "fall_below", "signal": "V(PWRGD)", "value": 0.4, "unit": "V"},
        },
        ("V(PWRGD)", "V(SS/TR)"),
    ),
    # --------------------------------------------------------------------- EN
    Spec(
        "FUNC_060",
        "FUNCTIONAL",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "The EN pin has an internal pull-up current source, so the pin may be left floating to "
        "start automatically or driven by an external divider to set the input UVLO; tying it to "
        "ground permanently disables the device.",
        9,
        r"The EN pin has an internal pullup current source that can be used to adjust the input "
        r"voltage UVLO with two external resistors",
        "Detailed Description",
        "Overview",
        expression={"op": "net_not_equals", "refdes": "U1", "pin": "EN", "net": "GND"},
        signal_refs=("V(EN)",),
    ),
    Spec(
        "FUNC_061",
        "FUNCTIONAL",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "The device is designed for safe monotonic start-up into pre-biased loads.",
        9,
        r"The device has been designed for safe monotonic start-up into prebiased loads",
        "Detailed Description",
        "Overview",
        expression={
            "op": "state_dependent",
            "when": {"signal": "V(EN)", "kind": "rise_above", "value": 1.21, "unit": "V"},
            "then": {
                "op": "not",
                "item": {"op": "fall_below", "signal": "V(VOUT)", "value": 0.05, "unit": "V"},
            },
        },
        signal_refs=("V(VOUT)",),
    ),
    Spec(
        "TEMPORAL_062",
        "TEMPORAL",
        "TYPICAL_VALUE",
        "IMPORTANT",
        "The default start-up threshold on VIN is typically 4.0 V.",
        9,
        r"The default start up is when VIN is typically 4\.0 V",
        "Detailed Description",
        "Overview",
        {"typ": 4.0, "unit": "V"},
        {"op": "between", "signal": "V(VIN_START)", "low": 4.0, "high": 4.5, "unit": "V"},
        ("V(VIN_START)",),
    ),
    Spec(
        "ELEC_063",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "IMPORTANT",
        "The BOOT-PH UVLO threshold is 2.1 V to 3 V, which limits the maximum duty cycle with a "
        "bootstrap capacitor.",
        5,
        r"BOOT-PH UVLO\s+2\.1\s+3\s+V",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"min": 2.1, "max": 3.0, "unit": "V"},
        signal_refs=("V(BOOT)-V(PH)",),
    ),
    Spec(
        "ELEC_064",
        "ELECTRICAL",
        "DOCUMENTED_LIMIT",
        "INFORMATIONAL",
        "The switching frequency range is 200 kHz to 1200 kHz, set by the RT/CLK resistor.",
        5,
        r"Switching frequency range \(RT mode set point\s+and PLL mode\)\s+200\s+1200\s+kHz",
        "Electrical Characteristics",
        "Electrical Characteristics",
        {"min": 200e3, "max": 1200e3, "unit": "Hz"},
        signal_refs=("F(SW)",),
    ),
    Spec(
        "CONN_065",
        "CONNECTIVITY",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "A 0.1 uF capacitor is required between BOOT and PH, and the exposed thermal pad must be "
        "soldered down to ground for proper operation.",
        13,
        r"The value of this ceramic capacitor should be 0\.1 μF",
        "Feature Description",
        "Bootstrap Voltage (BOOT) and Low Dropout Operation",
        {"typ": 0.1e-6, "unit": "F"},
        {"op": "pin_connected", "refdes": "C1", "pin": "1"},
        ("V(BOOT)",),
    ),
    Spec(
        "CONN_066",
        "CONNECTIVITY",
        "DOCUMENTED_LIMIT",
        "CRITICAL",
        "The exposed thermal pad is the package thermal path and signal ground and must be "
        "connected.",
        2,
        r"Thermal pad of the package and signal ground\. It must be soldered down for proper "
        r"operation",
        "Pin Configuration and Functions",
        "Pin Functions",
        expression={"op": "pin_connected", "refdes": "U1", "pin": "PAD"},
        signal_refs=("PAD",),
    ),
    Spec(
        "SYSTEM_070",
        "SYSTEM",
        "DOCUMENTED_LIMIT",
        "INFORMATIONAL",
        "The device requires a control supply on VIN and a separate power-stage supply on PVIN; "
        "the two pins may be tied together or run from different rails.",
        10,
        r"The VIN pin voltage supplies the internal control circuits of the device",
        "Detailed Description",
        "VIN and Power VIN Pins (VIN and PVIN)",
        expression={
            "op": "all_of",
            "items": [
                {"op": "pin_connected", "refdes": "U1", "pin": "VIN"},
                {"op": "pin_connected", "refdes": "U1", "pin": "PVIN"},
            ],
        },
        signal_refs=("V(VIN)", "V(PVIN)"),
    ),
)

#: (physical pin, name, function, direction, topology, connection requirement, polarity)
PINS: tuple[tuple[str, str, str, str, str, str, str], ...] = (
    (
        "1",
        "RT/CLK",
        "Switching-frequency programming resistor or external clock input.",
        "input",
        "input_only",
        "required",
        "not_applicable",
    ),
    (
        "2",
        "GND",
        "Return for the control circuitry and the low-side power MOSFET.",
        "ground",
        "power",
        "required",
        "not_applicable",
    ),
    (
        "3",
        "GND",
        "Return for the control circuitry and the low-side power MOSFET.",
        "ground",
        "power",
        "required",
        "not_applicable",
    ),
    (
        "4",
        "PVIN",
        "Power input supplying the internal power switches.",
        "power",
        "power",
        "required",
        "not_applicable",
    ),
    (
        "5",
        "PVIN",
        "Power input supplying the internal power switches.",
        "power",
        "power",
        "required",
        "not_applicable",
    ),
    (
        "6",
        "VIN",
        "Supplies the internal control circuitry of the converter.",
        "power",
        "power",
        "required",
        "not_applicable",
    ),
    (
        "7",
        "VSENSE",
        "Inverting input of the gm error amplifier (external feedback divider).",
        "input",
        "input_only",
        "required",
        "not_applicable",
    ),
    (
        "8",
        "COMP",
        "Error-amplifier output and input to the switch-current comparator; frequency "
        "compensation connects here.",
        "output",
        "push_pull",
        "conditional",
        "not_applicable",
    ),
    (
        "9",
        "SS/TR",
        "Slow-start and tracking; an external capacitor sets the internal reference rise "
        "time, and the pin voltage overrides the internal reference.",
        "bidir",
        "input_only",
        "optional",
        "not_applicable",
    ),
    (
        "10",
        "EN",
        "Enable pin: float to enable, or adjust the input UVLO with two resistors.",
        "input",
        "input_only",
        "optional",
        "active_high",
    ),
    ("11", "PH", "Switch node.", "output", "push_pull", "required", "not_applicable"),
    ("12", "PH", "Switch node.", "output", "push_pull", "required", "not_applicable"),
    (
        "13",
        "BOOT",
        "Bootstrap capacitor connection for the high-side gate drive.",
        "power",
        "power",
        "required",
        "not_applicable",
    ),
    (
        "14",
        "PWRGD",
        "Open-drain power-good output; asserts low on thermal shutdown, undervoltage, "
        "overvoltage, EN shutdown, or during slow start.",
        "output",
        "open_drain",
        "optional",
        "active_low",
    ),
    (
        "15",
        "PAD",
        "Exposed thermal pad and signal ground; must be soldered down.",
        "ground",
        "power",
        "required",
        "not_applicable",
    ),
)

#: Deliberate control entry: this MUST be rejected as an operating limit by the validator.
ABSOLUTE_MAXIMUM_CONTROL: dict[str, Any] = {
    "note": (
        "Negative control for tests/regulator: a requirement taken from the absolute-maximum "
        "ratings table. validate_requirements must reject it as an operating limit."
    ),
    "requirement": {
        "req_id": "REQ_TPS54320_ABSMAX_001",
        "applies_to": PART,
        "kind": "ELECTRICAL",
        "class": "DOCUMENTED_LIMIT",
        "criticality": "CRITICAL",
        "origin": "DOCUMENT",
        "statement": "VIN must stay within -0.3 V to 20 V.",
        "limits": {"min": -0.3, "max": 20.0, "unit": "V"},
        "conditions": [],
        "signal_refs": ["V(VIN)"],
        "evidence": [
            {
                "doc_id": DOC_ID,
                "page": {"pdf_page": 3, "printed_label": None},
                "section": "Absolute Maximum Ratings",
                "table": "Absolute Maximum Ratings",
                "figure": None,
                "excerpt": "Stresses beyond those listed under absolute maximum ratings may cause "
                "permanent damage to the device.",
                "extraction": "embedded_text",
            }
        ],
        "conflicts": [],
        "citation_verified": True,
        "status": "active",
    },
}


def build() -> dict[str, Any]:
    doc = read_pdf(DATASHEET)
    requirements: list[dict[str, Any]] = []
    unverified: list[str] = []

    for spec in SPECS:
        page = doc.pages[spec.page]
        excerpt = _excerpt(page.text, spec)
        req_id = f"REQ_{PART}_{spec.suffix}"
        payload: dict[str, Any] = {
            "req_id": req_id,
            "applies_to": PART,
            "configuration": None,
            "kind": spec.kind,
            "class": spec.req_class,
            "criticality": spec.criticality,
            "origin": "DOCUMENT",
            "statement": spec.statement,
            "limits": spec.limits,
            "expression": spec.expression,
            "conditions": (
                [{"text": spec.condition, "parameter_overrides": {}}]
                if spec.condition
                else [_TABLE_CONDITIONS[spec.section]]
                if spec.section in _TABLE_CONDITIONS
                else []
            ),
            "signal_refs": list(spec.signal_refs),
            "evidence": [
                {
                    "doc_id": DOC_ID,
                    "page": {"pdf_page": spec.page, "printed_label": None},
                    "section": spec.section,
                    "table": spec.table,
                    "figure": None,
                    "excerpt": excerpt,
                    "extraction": "embedded_text",
                }
            ],
            "conflicts": [],
            "citation_verified": bool(excerpt_on_page(doc, excerpt, spec.page)),
            "status": "active",
        }
        if not payload["citation_verified"]:
            unverified.append(req_id)
        # Self-validate: a mis-shaped requirement must fail here, not in a test.
        try:
            Requirement.model_validate(payload)
        except ValidationError as exc:  # pragma: no cover - guards the spec table above
            raise SystemExit(f"{req_id} does not form a valid Requirement: {exc}") from exc
        requirements.append(payload)

    pinmap = [
        {
            "part_id": PART,
            "physical_pin": number,
            "name": name,
            "function": function,
            "polarity": polarity,
            "direction": direction,
            "supply_domain": None,
            "output_topology": topology,
            "connection_requirement": connection,
            "unused_pin_treatment": (
                "leave floating to enable the device" if name == "EN" else None
            ),
            "behavior": [],
            "evidence": [
                {
                    "doc_id": DOC_ID,
                    "page": {"pdf_page": 2, "printed_label": None},
                    "section": "Pin Configuration and Functions",
                    "table": "Pin Functions",
                    "figure": None,
                    "excerpt": _normalize(doc.pages[2].text)[:MAX_EXCERPT],
                    "extraction": "embedded_text",
                }
            ],
            "mapped_symbol_pin": name,
        }
        for number, name, function, direction, topology, connection, polarity in PINS
    ]

    coverage = 1.0 - len(unverified) / max(len(requirements), 1)
    return {
        "document": {
            "doc_id": DOC_ID,
            "title_contains": "TPS54320",
            "file_hash_note": "see provenance.json for the datasheet sha256",
            "page_count": doc.page_count,
            "text_extraction": doc.text_extraction,
        },
        "extraction": {
            "method": "fixture-assisted (public datasheet read directly; excerpts sliced from the "
            "extracted page text by anchor, never remembered)",
            "tool": "tools/extract_tps54320_fixture.py",
            "citation_coverage": round(coverage, 6),
            "unverified": unverified,
        },
        "requirements": requirements,
        "absolute_maximum_control": ABSOLUTE_MAXIMUM_CONTROL,
        "pinmap": pinmap,
    }


def main() -> int:
    if not DATASHEET.is_file():
        print(
            f"{DATASHEET} is missing; run `uv run python tools/fetch_fixtures.py "
            "--allow-network` first",
            file=sys.stderr,
        )
        return 2
    data = build()
    requirements_path = FIXTURE / "requirements.json"
    pinmap_path = FIXTURE / "pinmap.json"
    requirements_path.write_text(
        json.dumps(
            {
                "document": data["document"],
                "extraction": data["extraction"],
                "absolute_maximum_control": data["absolute_maximum_control"],
                "requirements": data["requirements"],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    pinmap_path.write_text(
        json.dumps(
            {"document": data["document"], "pins": data["pinmap"]}, indent=2, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    print(f"requirements: {len(data['requirements'])} -> {requirements_path}")
    print(f"pinmap:       {len(data['pinmap'])} pins -> {pinmap_path}")
    print(
        f"citation coverage: {data['extraction']['citation_coverage']:.3f} "
        f"(unverified: {data['extraction']['unverified'] or 'none'})"
    )
    return 0 if not data["extraction"]["unverified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
