"""Re-validation of the committed TPS54320 fixture (Phase 2 step 2/3).

The fixture is *extracted* once (fixture-assisted, by reading the public
datasheet) and then re-validated **deterministically** on every test run:

* every requirement parses into the record schema (including the ``class`` alias),
* every citation is re-verified against the datasheet when the (git-ignored)
  original is present — a remembered quotation fails here,
* the absolute-maximum control entry is rejected as an operating limit,
* the pin map covers every physical pin, and its names are consistent with the
  vendor model's port list (so the symbol/model mapping cannot drift).

When the datasheet is absent the citation checks are skipped, but the schema,
class-coverage and pin-map checks still run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from boardmodeler.documents.pdf import excerpt_on_page, read_pdf
from boardmodeler.domain.enums import Status
from boardmodeler.domain.expressions import ALL_OPS
from boardmodeler.domain.ids import normalize_slug
from boardmodeler.domain.records import PinDefinition, Requirement
from boardmodeler.models.library import subckt_ports
from boardmodeler.requirements.model import validate_requirements
from boardmodeler.requirements.review import review, verify_citations
from boardmodeler.verification.assertions import EvalContext, evaluate

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "regulator" / "tps54320"
REQUIREMENTS_FILE = FIXTURE / "requirements.json"
PINMAP_FILE = FIXTURE / "pinmap.json"
DATASHEET = FIXTURE / "originals" / "tps54320_datasheet.pdf"
VENDOR_MODEL = FIXTURE / "originals" / "TPS54320_TRANS.lib"
ADAPTED_MODEL = FIXTURE / "adapted" / "TPS54320_TRANS_ltspice.lib"

#: The vendor model's port names as they appear in the subcircuit declaration.
MODEL_PORT_TO_PIN_NAME = {
    "BOOT": "BOOT",
    "COMP": "COMP",
    "EN": "EN",
    "ETPad": "PAD",
    "GND_1": "GND",
    "GND_2": "GND",
    "PH_1": "PH",
    "PH_2": "PH",
    "PVIN_1": "PVIN",
    "PVIN_2": "PVIN",
    "PWRGD": "PWRGD",
    "RT_CLK": "RT/CLK",
    "SS_TR": "SS/TR",
    "VIN": "VIN",
    "VSENSE": "VSENSE",
}


def load_requirements() -> list[Requirement]:
    raw = json.loads(REQUIREMENTS_FILE.read_text(encoding="utf-8"))
    return [Requirement.model_validate(item) for item in raw["requirements"]]


def load_pins() -> list[PinDefinition]:
    raw = json.loads(PINMAP_FILE.read_text(encoding="utf-8"))
    return [PinDefinition.model_validate(item) for item in raw["pins"]]


def test_requirements_parse_and_are_schema_valid() -> None:
    requirements = load_requirements()
    assert len(requirements) == 38
    ids = [r.req_id for r in requirements]
    assert len(ids) == len(set(ids))
    for requirement in requirements:
        assert requirement.origin.value == "DOCUMENT"
        assert requirement.evidence, requirement.req_id
        assert requirement.evidence[0].excerpt.strip()
        assert requirement.evidence[0].page is not None
        if requirement.expression is not None:
            assert requirement.expression.op in ALL_OPS


def test_every_fixture_expression_is_evaluable_by_the_engine() -> None:
    """Each requirement's expression must have a working evaluator.

    With no waveform the verdict is BLOCKED/UNKNOWN, but the point of this check is
    that dispatch reaches a real evaluator: an unimplemented op raises
    ``NotImplementedError`` from ``evaluate``.
    """
    for requirement in load_requirements():
        if requirement.expression is None:
            continue
        verdict = evaluate(requirement.expression, EvalContext())
        assert isinstance(verdict.status, Status), requirement.req_id
        assert verdict.detail.strip(), requirement.req_id


def test_requirement_classes_cover_the_plan() -> None:
    classes = {r.req_class.value for r in load_requirements()}
    assert classes == {"DOCUMENTED_LIMIT", "TYPICAL_VALUE"}
    # every limit triple that carries all three values is ordered
    for requirement in load_requirements():
        limit = requirement.limits
        if limit and limit.min is not None and limit.max is not None:
            assert limit.min <= limit.max, requirement.req_id


def test_deterministic_validation_passes_without_errors() -> None:
    report = validate_requirements(load_requirements())
    assert report.errors == [], [issue.message for issue in report.errors]
    assert report.counts["DOCUMENTED_LIMIT"] > 30
    # Exactly one warning is expected and deliberate: the datasheet prints the
    # hiccup cycle counts in the TYP column, so the wording heuristic infers a
    # limit while the requirement is classed TYPICAL_VALUE. The review layer turns
    # that into a non-blocking ambiguity item rather than resolving it silently.
    warnings = [issue for issue in report.issues if issue.severity == "warning"]
    assert [w.req_id for w in warnings] == ["REQ_TPS54320_TEMPORAL_033"]
    assert warnings[0].code == "class_inference_mismatch"


def test_table_conditions_are_carried_by_the_requirements() -> None:
    """A limit extracted from a table must keep the table's operating envelope."""
    by_id = {r.req_id: r for r in load_requirements()}
    reference = by_id["REQ_TPS54320_ELEC_020"]
    assert reference.conditions, "electrical-characteristics rows lose their TJ/VIN envelope"
    assert "TJ" in reference.conditions[0].text
    # The recommended-operating-conditions rows carry the free-air statement.
    operating = by_id["REQ_TPS54320_ELEC_002"]
    assert operating.conditions and "free-air" in operating.conditions[0].text
    # Requirements drawn from the prose sections carry their own condition (or none).
    assert by_id["REQ_TPS54320_PG_050"].conditions == []


def test_absolute_maximum_control_entry_is_rejected() -> None:
    raw = json.loads(REQUIREMENTS_FILE.read_text(encoding="utf-8"))
    control = Requirement.model_validate(raw["absolute_maximum_control"]["requirement"])
    report = validate_requirements([control])
    codes = {issue.code for issue in report.errors}
    assert "absolute_maximum_rejected" in codes, [i.message for i in report.errors]
    assert not report.ok


def test_pinmap_covers_every_physical_pin() -> None:
    pins = load_pins()
    numbers = [p.physical_pin for p in pins]
    assert numbers == [str(n) for n in range(1, 16)]
    assert len(pins) == 15
    by_name: dict[str, list[str]] = {}
    for pin in pins:
        assert pin.name and pin.function
        assert pin.direction in {"input", "output", "bidir", "power", "ground", "nc"}
        assert pin.output_topology in {
            "open_drain",
            "push_pull",
            "tri_state",
            "power",
            "input_only",
            "unknown",
        }
        by_name.setdefault(pin.name, []).append(pin.physical_pin)
    # The multi-pin nets are the ones the datasheet draws as shared pads.
    assert sorted(by_name["GND"]) == ["2", "3"]
    assert sorted(by_name["PVIN"]) == ["4", "5"]
    assert sorted(by_name["PH"]) == ["11", "12"]
    # Open-drain and active-low on the power-good output, float-to-enable on EN.
    pg = next(p for p in pins if p.name == "PWRGD")
    assert pg.output_topology == "open_drain" and pg.polarity == "active_low"
    en = next(p for p in pins if p.name == "EN")
    assert en.polarity == "active_high"
    assert "float" in (en.unused_pin_treatment or "")


@pytest.mark.skipif(not VENDOR_MODEL.is_file(), reason="vendor model original is git-ignored")
def test_pinmap_matches_the_vendor_model_ports() -> None:
    ports = subckt_ports(VENDOR_MODEL.read_text(encoding="utf-8", errors="replace"))
    assert ports, "no subcircuit ports found in the vendor model"
    mapped = [MODEL_PORT_TO_PIN_NAME.get(port) for port in ports]
    assert None not in mapped, f"unmapped vendor port in {ports}"
    pin_names = {p.name for p in load_pins()}
    assert set(mapped) <= pin_names, set(mapped) - pin_names
    # Every datasheet pin is represented in the model except the pin model does not expose.
    assert set(mapped) == pin_names
    assert len(ports) == len(mapped) == 15


@pytest.mark.skipif(not DATASHEET.is_file(), reason="datasheet original is git-ignored")
def test_every_citation_is_reverified_against_the_datasheet() -> None:
    document = read_pdf(DATASHEET)
    requirements = load_requirements()
    verified = verify_citations(requirements, {"doc_tps54320": _document_record(document)})
    assert verified, "verification returned nothing"
    assert all(verified.values()), [rid for rid, ok in verified.items() if not ok]
    # And the review layer agrees, with no blocking items for the fixture itself.
    outcome = review(requirements, {"doc_tps54320": _document_record(document)})
    assert not outcome.unverified
    assert set(outcome.verified) == {r.req_id for r in requirements}


def test_citation_verification_rejects_an_invented_quote() -> None:
    """The same check must fail for text that is not on the cited page."""
    if not DATASHEET.is_file():
        pytest.skip("datasheet original is git-ignored")
    document = read_pdf(DATASHEET)
    assert not excerpt_on_page(document, "The reference voltage is 0.85 V typically.", 4)
    assert excerpt_on_page(document, "Voltage reference", 4)


@pytest.mark.skipif(
    not VENDOR_MODEL.is_file(),
    reason="vendor model original is git-ignored (D-003); fetch it with tools/fetch_fixtures.py",
)
def test_adapted_model_keeps_the_vendor_port_order() -> None:
    original = subckt_ports(VENDOR_MODEL.read_text(encoding="utf-8", errors="replace"))
    adapted = subckt_ports(ADAPTED_MODEL.read_text(encoding="utf-8"))
    assert adapted[: len(original)] == original


def test_requirement_ids_follow_the_documented_pattern() -> None:
    for requirement in load_requirements():
        assert requirement.req_id.startswith("REQ_TPS54320_")
        suffix = requirement.req_id.removeprefix("REQ_TPS54320_")
        assert normalize_slug(suffix) == suffix


def _document_record(document: object):
    """A minimal DocumentRecord for the datasheet (path/hash come from provenance)."""
    from boardmodeler.domain.records import DocumentRecord

    provenance = json.loads((FIXTURE / "provenance.json").read_text(encoding="utf-8"))
    record = next(r for r in provenance["records"] if r["name"] == "tps54320_datasheet.pdf")
    return DocumentRecord(
        doc_id="doc_tps54320",
        title="TPS54320 datasheet",
        manufacturer="Texas Instruments",
        doc_type="datasheet",
        revision="SLVS982C",
        file_hash=record["sha256"] or "0" * 64,
        source_url=record["url"],
        provenance="downloaded_public",
        classification="public",
        remote_inference_allowed=True,
        page_count=document.page_count,
        page_labels={},
        text_extraction=document.text_extraction,
        path=str(DATASHEET),
        redistribution_allowed=False,
    )
