"""Corrected current-limit circuit for a buck part, judged by the product harness.

usage: ilim_proof.py SPEC_KIND SPEC_PATH PART WORKDIR
  SPEC_KIND = pair  -> SPEC_PATH is a folder with requirements.json + bindings.json
  SPEC_KIND = set   -> SPEC_PATH is a SpecSet JSON (spec.json)
"""

import dataclasses
import hashlib
import json
import re
import sys
import time
from pathlib import Path

from boardmodeler.authoring.buck_fixtures import current_limit_recipe
from boardmodeler.authoring.circuit_probe import CircuitRecipe
from boardmodeler.authoring.harness import run_harness
from boardmodeler.authoring.spec import SpecSet, load_tps54320_spec
from boardmodeler.authoring.test_planner import _buck_fixture_issue
from boardmodeler.models.buck_switching import match_pins, seed_from_spec
from boardmodeler.authoring.pin_roles import physical_terminals

LTSPICE = Path(r"C:\Users\basam\AppData\Local\Programs\ADI\LTspice\LTspice.exe")
kind, source, part, work = sys.argv[1], Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
work.mkdir(parents=True, exist_ok=True)
if kind == "pair":
    spec = load_tps54320_spec(source / "requirements.json", source / "bindings.json", part=part, subckt=part)
else:
    spec = SpecSet.from_json(source.read_text(encoding="utf-8"))

t0 = time.perf_counter()
seed = seed_from_spec(spec)
assert seed is not None, "template did not match"
params = {p.name: p for p in seed.parameters}
lib = work / f"{part}.lib"
lib.write_text(seed.library_text, encoding="utf-8", newline="\n")
pins = match_pins(physical_terminals(spec.pin_map))


def row(pattern: str, unit: str):
    for ch in spec.characteristics:
        if ch.unit == unit and re.search(pattern, ch.statement, re.I) and ch.req_class != "ABSOLUTE_MAXIMUM":
            if ch.min_value is not None or ch.typ_value is not None or ch.max_value is not None:
                return ch
    return None


ilim = row(r"\bcurrent limit\b", "A")
vref = row(r"\b(?:voltage|feedback) reference\b|\breference voltage\b", "V")
assert ilim is not None and vref is not None
limit_high = ilim.max_value if ilim.max_value is not None else ilim.typ_value
iss = params["ISS"]
notes = []
if iss.origin != "cited_row":
    notes.append(
        f"SS charge current is not cited in this spec; the template default {iss.value:g} A only "
        "places the measurement window (it sets no limit)."
    )
recipe = current_limit_recipe(
    pins.roles,
    pins.ground_ties,
    vin=12.0,
    charge_current=iss.value,
    reference_min=vref.min_value if vref.min_value is not None else vref.typ_value,
    reference_typ=vref.typ_value,
    limit_high=limit_high,
    evidence=f"{ilim.statement} (page {ilim.source_page}).",
)
validated = CircuitRecipe.model_validate(recipe)
rows = [(ch.statement, {"min": ch.min_value, "typ": ch.typ_value, "max": ch.max_value, "unit": ch.unit}) for ch in spec.characteristics]
issue = _buck_fixture_issue(validated, rows, ilim.statement)
build_s = time.perf_counter() - t0
char = dataclasses.replace(ilim, probe="circuit_measurement", probe_params={}, probe_recipe=recipe, not_testable_reason=None)
single = dataclasses.replace(spec, characteristics=(char,))
t1 = time.perf_counter()
report = run_harness(model_lib=lib, subckt=part, spec=single, workdir=work / "harness", ltspice=LTSPICE, timeout_s=120.0)
harness_s = time.perf_counter() - t1
outcome = report.outcomes[0]
summary = {
    "part": part,
    "model_sha256": hashlib.sha256(lib.read_bytes()).hexdigest(),
    "row": ilim.char_id,
    "row_statement": ilim.statement,
    "row_page": ilim.source_page,
    "limits": {"min": ilim.min_value, "typ": ilim.typ_value, "max": ilim.max_value, "unit": ilim.unit},
    "parameter_ILIM": {"value": params["ILIM"].value, "origin": params["ILIM"].origin, "row": params["ILIM"].row_id},
    "parameter_ISS": {"value": iss.value, "origin": iss.origin, "row": iss.row_id},
    "window_s": [recipe["measurement"]["start"], recipe["measurement"]["end"]],
    "pre_freeze_check": issue or "accepted",
    "status": outcome.status,
    "measured": outcome.measured,
    "detail": outcome.detail,
    "unknown_reason": outcome.unknown_reason,
    "artifacts": outcome.artifacts,
    "seed_and_fixture_s": round(build_s, 4),
    "harness_s": round(harness_s, 2),
    "notes": notes,
}
(work / "ilim-proof.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
print(json.dumps({k: summary[k] for k in ("part", "row", "limits", "parameter_ILIM", "window_s", "pre_freeze_check", "status", "measured", "detail", "seed_and_fixture_s", "harness_s", "notes")}, indent=1, ensure_ascii=False))
