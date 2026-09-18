"""Engine end-to-end on real LTspice output (Phase 1 step 12).

A known-good deck must produce PASS and a known-bad deck must produce FAIL with
the measured value visible. This is the test that proves the pipeline can tell
good from bad using the actual simulator rather than a mock.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from boardmodeler.domain.enums import (
    Criticality,
    EvidenceExtraction,
    RequirementClass,
    RequirementKind,
    RequirementOrigin,
    Status,
)
from boardmodeler.domain.records import (
    EvidenceRef,
    ExpectationSpec,
    PageRef,
    Requirement,
    TestCase,
)
from boardmodeler.pipeline.runner import RunContext, run_case, run_deck_tests
from boardmodeler.simulation.deck import deck_from_template
from boardmodeler.verification.engine import evaluate_case

pytestmark = pytest.mark.ltspice

TEMPLATE = Path(__file__).resolve().parents[1] / "decks" / "divider.cir"

DECK_VALUES = {
    "vin": 12.0,
    "r_upper": 26.364e3,
    "r_lower": 10e3,
    "c_out": 10e-6,
    "tstop": 2e-3,
    "tmax": 2e-6,
}

REQUIREMENT = Requirement(
    req_id="REQ_DIVIDER_ELEC_001",
    applies_to="U1",
    kind=RequirementKind.ELECTRICAL,
    **{
        "class": RequirementClass.DERIVED_VALUE,
        "criticality": Criticality.CRITICAL,
        "origin": RequirementOrigin.TEST_FIXTURE,
        "statement": "The divided rail settles at 3.3 V within its window.",
        "limits": {"min": 3.234, "typ": 3.3, "max": 3.366, "unit": "V"},
        "expression": {
            "op": "between",
            "signal": "V(out)",
            "low": 3.234,
            "high": 3.366,
            "unit": "V",
            "interval": {"start_s": 1e-3, "end_s": 2e-3},
        },
        "evidence": [
            EvidenceRef(
                doc_id="doc_synthetic_divider",
                page=PageRef(pdf_page=0),
                section="Synthetic test fixture",
                excerpt="The divider output is 12 V * R2 / (R1 + R2).",
                extraction=EvidenceExtraction.SYNTHETIC_FIXTURE,
            )
        ],
        "citation_verified": True,
        "status": "active",
    },
)


def make_case(name: str) -> TestCase:
    return TestCase(
        test_id=f"T_divider_{name}",
        requirement_ids=["REQ_DIVIDER_ELEC_001"],
        scenario_id="nominal_startup",
        scope="circuit_compliance",
        deck_template="tests/decks/divider.cir",
        expected=ExpectationSpec(kind="satisfy", detail="V(out) settles within 3.234-3.366 V"),
        measurement=["V(out)"],
        tolerance={},
        max_timestep_s=2e-6,
    )


def build(run_dir: Path, values: dict[str, float]) -> Path:
    return deck_from_template(TEMPLATE, run_dir / "deck.cir", values)


def test_known_good_deck_passes_and_known_bad_deck_fails(ltspice_exe: Path, tmp_path: Path) -> None:
    ctx = RunContext(project_dir=tmp_path, ltspice=ltspice_exe, timeout_s=90)
    case = make_case("good")

    good = run_case(
        ctx,
        case,
        build_deck=lambda run_dir: build(run_dir, DECK_VALUES),
        run_identifier="good",
    )
    assert good.usability.usable, good.detail
    assert good.raw is not None
    assert good.raw_sha256 and good.log_sha256
    assert good.diagnostics.completed

    good_result = evaluate_case(case, good, {"REQ_DIVIDER_ELEC_001": REQUIREMENT})
    assert good_result.status is Status.PASS, good_result.detail
    assert good_result.measured["min(V(out))"] == pytest.approx(3.3, abs=0.02)
    assert good_result.run_id == "good"

    # The same case against a divider that is 10% off must fail with the value.
    bad_values = {**DECK_VALUES, "r_upper": 30e3}
    bad_case = make_case("bad")
    bad = run_case(
        ctx,
        bad_case,
        build_deck=lambda run_dir: build(run_dir, bad_values),
        run_identifier="bad",
    )
    assert bad.usability.usable, bad.detail
    bad_result = evaluate_case(bad_case, bad, {"REQ_DIVIDER_ELEC_001": REQUIREMENT})
    assert bad_result.status is Status.FAIL, bad_result.detail
    assert bad_result.measured["max(V(out))"] == pytest.approx(3.0, abs=0.02)
    # The failure names the observed value and the violated limit.
    assert "below the lower limit 3.234" in bad_result.detail
    assert "3 V at" in bad_result.detail
    # The two runs must not share a directory or a deck.
    assert good.run_dir != bad.run_dir
    assert good.deck_sha256 != bad.deck_sha256


def test_run_deck_tests_keeps_one_directory_per_case(ltspice_exe: Path, tmp_path: Path) -> None:
    ctx = RunContext(project_dir=tmp_path, ltspice=ltspice_exe, timeout_s=90)
    cases = [make_case("a"), make_case("b")]

    def builder(case: TestCase, run_dir: Path) -> Path:
        values = dict(DECK_VALUES)
        if case.test_id.endswith("b"):
            values["r_upper"] = 30e3
        return build(run_dir, values)

    artifacts = run_deck_tests(ctx, cases, deck_builder=builder)
    assert len(artifacts) == 2
    names = [a.run_dir.name for a in artifacts]
    # Each run directory is traceable to its case and unique per execution.
    assert names[0].startswith("T_divider_a_") and names[1].startswith("T_divider_b_")
    assert len(set(names)) == 2
    for artifact in artifacts:
        assert artifact.deck_path is not None and artifact.deck_path.is_file()
        assert artifact.run_dir / "deck.cir" in list(artifact.run_dir.iterdir())

    results = [
        evaluate_case(case, artifact, {"REQ_DIVIDER_ELEC_001": REQUIREMENT})
        for case, artifact in zip(cases, artifacts, strict=True)
    ]
    assert results[0].status is Status.PASS
    assert results[1].status is Status.FAIL


def test_cancellation_before_the_run_blocks_without_starting_ltspice(
    ltspice_exe: Path, tmp_path: Path
) -> None:
    import threading

    ctx = RunContext(project_dir=tmp_path, ltspice=ltspice_exe, timeout_s=90)
    cancel = threading.Event()
    cancel.set()
    artifacts = run_case(ctx, make_case("cancelled"), cancel=cancel, run_identifier="cancelled")
    assert artifacts.batch is None
    assert artifacts.usability.usable is False
    assert artifacts.blocked_reason == "cancelled"
    assert not (tmp_path / "runs" / "cancelled" / "deck.cir").exists()
