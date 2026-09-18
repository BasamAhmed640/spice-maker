"""Engine and status-propagation tests (Phase 1 steps 6, 9).

Every status the product can return is produced here by a constructed run, and
each honesty gate is shown to turn a would-be PASS into UNKNOWN/BLOCKED.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
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
from boardmodeler.pipeline.runner import RunArtifacts
from boardmodeler.simulation.limits import RunUsability
from boardmodeler.simulation.log import LogSummary
from boardmodeler.simulation.measures import RunDiagnostics
from boardmodeler.simulation.raw import RawFile
from boardmodeler.verification.engine import evaluate_case, gate_reason
from boardmodeler.verification.scenarios import REQUIRED_SCENARIO_IDS, scenario

EVIDENCE = EvidenceRef(
    doc_id="doc_synthetic",
    page=PageRef(pdf_page=3),
    section="Electrical Characteristics",
    excerpt="Output voltage regulation limits are 3.234 V to 3.366 V.",
    extraction=EvidenceExtraction.SYNTHETIC_FIXTURE,
)


def make_requirement(
    req_id: str = "REQ_SYNTH_ELEC_001",
    *,
    expression: dict | None = None,
    status: str = "active",
    citation_verified: bool = True,
    origin: RequirementOrigin = RequirementOrigin.TEST_FIXTURE,
    conflicts: list[str] | None = None,
) -> Requirement:
    return Requirement(
        req_id=req_id,
        applies_to="U1",
        kind=RequirementKind.ELECTRICAL,
        **{
            "class": RequirementClass.DOCUMENTED_LIMIT,
            "criticality": Criticality.CRITICAL,
            "origin": origin,
            "statement": "The 3V3 rail stays within its regulation limits.",
            "limits": {"min": 3.234, "typ": 3.3, "max": 3.366, "unit": "V"},
            "expression": expression
            or {
                "op": "between",
                "signal": "V(3V3)",
                "low": 3.234,
                "high": 3.366,
                "unit": "V",
                "interval": {"start_s": 5e-4, "end_s": 1e-3},
            },
            "evidence": [EVIDENCE],
            "citation_verified": citation_verified,
            "status": status,
            "conflicts": conflicts or [],
        },
    )


def make_case(
    requirement_ids: list[str] | None = None,
    *,
    kind: str = "satisfy",
    scenario_id: str = "nominal_startup",
    test_id: str = "T_nominal_startup_001",
) -> TestCase:
    return TestCase(
        test_id=test_id,
        requirement_ids=requirement_ids or ["REQ_SYNTH_ELEC_001"],
        scenario_id=scenario_id,
        scope="circuit_compliance",
        deck_template="decks/nominal.cir",
        expected=ExpectationSpec(kind=kind, detail="the 3V3 rail stays within its limits"),  # type: ignore[arg-type]
        measurement=["V(3V3)", "V(PG)"],
    )


def make_artifacts(
    *,
    rail: float = 3.3,
    usable: bool = True,
    blocked_reason: str | None = None,
    completed: bool = True,
    log_errors: list[str] | None = None,
    convergence: list[str] | None = None,
    expected_stop_s: float = 1e-3,
    t_end: float = 1e-3,
    raw_error: str | None = None,
    include_pg: bool = True,
) -> RunArtifacts:
    t = np.linspace(0.0, t_end, 1001)
    signals = {"V(3V3)": np.full_like(t, rail), "V(EN)": np.zeros_like(t)}
    if include_pg:
        signals["V(PG)"] = np.where(t > 3e-4, 3.3, 0.0)
    raw = RawFile(
        path=None,
        plotname="Transient Analysis",
        flags=["real"],
        variables=["time", *signals],
        variable_types=["time"] + ["voltage"] * len(signals),
        data=np.column_stack([t, *signals.values()]),
        points_per_step=[t.size],
    )
    diagnostics = RunDiagnostics(
        completed=completed,
        reached_s=float(t[-1]),
        expected_stop_s=expected_stop_s,
        truncated=float(t[-1]) < expected_stop_s,
        errors=list(log_errors or []),
        convergence_issues=list(convergence or []),
        raw_missing_reason=raw_error,
        observations={},
    )
    usability = RunUsability(usable=usable, blocked_reason=blocked_reason, detail="constructed")
    return RunArtifacts(
        run_id="run_unit_0001",
        test_id="T_nominal_startup_001",
        scenario_id="nominal_startup",
        run_dir=Path("runs/run_unit_0001"),
        deck_path=Path("runs/run_unit_0001/deck.cir"),
        deck_text="* deck\n.end\n",
        deck_sha256="0" * 64,
        batch=None,
        log=LogSummary(path=None),
        raw=raw if raw_error is None else None,
        raw_error=raw_error,
        diagnostics=diagnostics,
        usability=usability,
        blocked_reason=blocked_reason,
    )


# --------------------------------------------------------------------------- #
# happy path and basic failure


def test_satisfy_passes_with_measured_evidence() -> None:
    result = evaluate_case(
        make_case(), make_artifacts(), {"REQ_SYNTH_ELEC_001": make_requirement()}
    )
    assert result.status is Status.PASS
    assert result.measured["min(V(3V3))"] == pytest.approx(3.3)
    assert result.measured["max(V(3V3))"] == pytest.approx(3.3)
    assert result.requirement_ids == ["REQ_SYNTH_ELEC_001"]
    assert result.run_id == "run_unit_0001"
    assert "scenario nominal_startup" in result.detail
    assert result.unknown_reason is None


def test_satisfy_fails_and_reports_the_offending_value() -> None:
    result = evaluate_case(
        make_case(), make_artifacts(rail=2.9), {"REQ_SYNTH_ELEC_001": make_requirement()}
    )
    assert result.status is Status.FAIL
    assert result.measured["min(V(3V3))"] == pytest.approx(2.9)
    assert "2.9" in result.detail


def test_multiple_requirements_prefix_measured_values() -> None:
    case = make_case(["REQ_SYNTH_ELEC_001", "REQ_SYNTH_ELEC_002"])
    requirements = {
        "REQ_SYNTH_ELEC_001": make_requirement("REQ_SYNTH_ELEC_001"),
        "REQ_SYNTH_ELEC_002": make_requirement(
            "REQ_SYNTH_ELEC_002",
            expression={"op": "gt", "signal": "V(3V3)", "value": 3.0, "unit": "V"},
        ),
    }
    result = evaluate_case(case, make_artifacts(), requirements)
    assert result.status is Status.PASS
    assert "REQ_SYNTH_ELEC_001.min(V(3V3))" in result.measured
    assert "REQ_SYNTH_ELEC_002.min(V(3V3))" in result.measured


# --------------------------------------------------------------------------- #
# gates: each turns a would-be pass into UNKNOWN


def test_missing_requirement_is_unknown() -> None:
    result = evaluate_case(make_case(["REQ_NOT_IN_BASELINE"]), make_artifacts(), {})
    assert result.status is Status.UNKNOWN
    assert result.unknown_reason == "requirement_not_found"
    assert result.measured  # never empty


def test_conflicting_requirement_is_unknown_and_never_resolved_silently() -> None:
    requirement = make_requirement(
        status="conflict", conflicts=["SLVS982C p.7 says 0.795 V", "errata says 0.800 V"]
    )
    result = evaluate_case(make_case(), make_artifacts(), {"REQ_SYNTH_ELEC_001": requirement})
    assert result.status is Status.UNKNOWN
    assert result.unknown_reason == "requirement_conflict_unresolved"
    assert "errata" in result.detail


def test_unverified_citation_is_unknown_for_document_requirements() -> None:
    requirement = make_requirement(citation_verified=False, origin=RequirementOrigin.DOCUMENT)
    result = evaluate_case(make_case(), make_artifacts(), {"REQ_SYNTH_ELEC_001": requirement})
    assert result.status is Status.UNKNOWN
    assert result.unknown_reason == "citation_unverified"

    # A fixture/user requirement has no citation to verify, so it still evaluates.
    fixture_req = make_requirement(citation_verified=False, origin=RequirementOrigin.TEST_FIXTURE)
    ok = evaluate_case(make_case(), make_artifacts(), {"REQ_SYNTH_ELEC_001": fixture_req})
    assert ok.status is Status.PASS


def test_capability_gate_makes_a_requirement_unknown() -> None:
    result = evaluate_case(
        make_case(),
        make_artifacts(),
        {"REQ_SYNTH_ELEC_001": make_requirement()},
        capability_gate={"REQ_SYNTH_ELEC_001": "load_transients is unsupported by this model"},
    )
    assert result.status is Status.UNKNOWN
    assert result.unknown_reason == "model_capability_unsupported"
    assert "load_transients" in result.detail


def test_requirement_without_expression_is_unknown() -> None:
    requirement = make_requirement(expression={})
    requirement = requirement.model_copy(update={"expression": None})
    result = evaluate_case(make_case(), make_artifacts(), {"REQ_SYNTH_ELEC_001": requirement})
    assert result.status is Status.UNKNOWN
    assert result.unknown_reason == "requirement_has_no_expression"


def test_gate_reason_precedence() -> None:
    """A conflict is reported before a missing expression, and capability last."""
    conflicted = make_requirement(status="conflict", conflicts=["a vs b"]).model_copy(
        update={"expression": None}
    )
    conflict_reason = gate_reason(conflicted, {"REQ_SYNTH_ELEC_001": "nope"})
    assert conflict_reason is not None
    assert conflict_reason.startswith("requirement_conflict_unresolved")
    plain = make_requirement().model_copy(update={"expression": None})
    plain_reason = gate_reason(plain, None)
    assert plain_reason is not None
    assert plain_reason.startswith("requirement_has_no_expression")
    assert gate_reason(make_requirement(), None) is None


# --------------------------------------------------------------------------- #
# blocked propagation


def test_convergence_failure_blocks_instead_of_failing() -> None:
    artifacts = make_artifacts(
        usable=False,
        blocked_reason="sim_convergence_failure: Timestep too small",
        convergence=["Timestep too small; time = 1e-6"],
        completed=False,
    )
    result = evaluate_case(make_case(), artifacts, {"REQ_SYNTH_ELEC_001": make_requirement()})
    assert result.status is Status.BLOCKED
    assert result.blocked_reason == "sim_convergence_failure: Timestep too small"
    assert "BLOCKED" in result.detail


def test_missing_simulator_blocks() -> None:
    artifacts = make_artifacts(usable=False, blocked_reason="simulator_unavailable")
    result = evaluate_case(make_case(), artifacts, {"REQ_SYNTH_ELEC_001": make_requirement()})
    assert result.status is Status.BLOCKED
    assert result.blocked_reason == "simulator_unavailable"


def test_unreadable_raw_blocks() -> None:
    artifacts = make_artifacts(
        usable=False, blocked_reason="sim_output_unreadable: no 'Binary:' section", raw_error="x"
    )
    result = evaluate_case(make_case(), artifacts, {"REQ_SYNTH_ELEC_001": make_requirement()})
    assert result.status is Status.BLOCKED
    assert "sim_output" in str(result.blocked_reason)


# --------------------------------------------------------------------------- #
# not applicable


def test_all_not_applicable_yields_not_applicable() -> None:
    """A precondition that never occurs is reported, not silently passed."""
    case = make_case()
    # V(EN) stays low in the synthetic run, so the precondition is never met.
    requirement = make_requirement(
        expression={
            "op": "state_dependent",
            "when": {"signal": "V(EN)", "kind": "rise_above", "value": 1.0, "unit": "V"},
            "then": {"op": "gt", "signal": "V(3V3)", "value": 3.0, "unit": "V"},
        }
    )
    result = evaluate_case(case, make_artifacts(), {"REQ_SYNTH_ELEC_001": requirement})
    assert result.status is Status.NOT_APPLICABLE
    # Nothing to report as an unknown/blocked reason, but the detail must explain.
    assert "precondition" in result.detail


def test_partial_not_applicable_is_recorded_as_a_coverage_gap() -> None:
    case = make_case(["REQ_SYNTH_ELEC_001", "REQ_SYNTH_ELEC_002"])
    requirements = {
        "REQ_SYNTH_ELEC_001": make_requirement("REQ_SYNTH_ELEC_001"),
        "REQ_SYNTH_ELEC_002": make_requirement(
            "REQ_SYNTH_ELEC_002",
            expression={
                "op": "state_dependent",
                "when": {"signal": "V(EN)", "kind": "rise_above", "value": 1.0, "unit": "V"},
                "then": {"op": "gt", "signal": "V(3V3)", "value": 3.0, "unit": "V"},
            },
        ),
    }
    result = evaluate_case(case, make_artifacts(), requirements)
    assert result.status is Status.PASS
    assert "coverage gap" in result.detail
    assert "REQ_SYNTH_ELEC_002" in result.detail


# --------------------------------------------------------------------------- #
# fault-detection semantics


def test_violate_detected_passes_when_the_violation_is_observed() -> None:
    result = evaluate_case(
        make_case(kind="violate_detected", scenario_id="reset_early_release"),
        make_artifacts(rail=2.9),
        {"REQ_SYNTH_ELEC_001": make_requirement()},
    )
    assert result.status is Status.PASS
    assert "violation detected as expected" in result.detail
    assert result.measured["min(V(3V3))"] == pytest.approx(2.9)
    assert result.unknown_reason is None


def test_violate_detected_fails_when_no_violation_occurs() -> None:
    result = evaluate_case(
        make_case(kind="violate_detected"),
        make_artifacts(rail=3.3),
        {"REQ_SYNTH_ELEC_001": make_requirement()},
    )
    assert result.status is Status.FAIL
    assert "expected violation was not detected" in result.detail


def test_violate_detected_blocks_when_no_data_was_produced() -> None:
    artifacts = make_artifacts(usable=False, blocked_reason="simulator_unavailable")
    result = evaluate_case(
        make_case(kind="violate_detected"), artifacts, {"REQ_SYNTH_ELEC_001": make_requirement()}
    )
    assert result.status is Status.BLOCKED
    assert result.blocked_reason == "simulator_unavailable"


def test_violate_detected_fails_when_the_run_did_not_complete() -> None:
    artifacts = make_artifacts(
        usable=False,
        blocked_reason="sim_run_incomplete: unknown subcircuit",
        completed=False,
        log_errors=["deck.cir(2): This sub-circuit name is not defined."],
    )
    result = evaluate_case(
        make_case(kind="violate_detected"), artifacts, {"REQ_SYNTH_ELEC_001": make_requirement()}
    )
    assert result.status is Status.FAIL
    assert "did not complete" in result.detail


def test_violate_detected_with_unknown_instead_of_violation() -> None:
    artifacts = make_artifacts(include_pg=False)
    requirement = make_requirement(
        expression={"op": "gt", "signal": "V(absent)", "value": 1.0, "unit": "V"}
    )
    result = evaluate_case(
        make_case(kind="violate_detected"), artifacts, {"REQ_SYNTH_ELEC_001": requirement}
    )
    assert result.status is Status.UNKNOWN
    assert result.unknown_reason == "signal_not_saved"


# --------------------------------------------------------------------------- #
# scenario catalogue


def test_scenario_catalogue_covers_the_required_ids() -> None:
    assert len(REQUIRED_SCENARIO_IDS) == len(set(REQUIRED_SCENARIO_IDS))
    expected = {
        "nominal_startup",
        "nominal_shutdown",
        "slow_rail",
        "fast_rail",
        "staggered_rails",
        "en_before_vin",
        "pg_delayed",
        "pg_missing",
        "pullup_missing",
        "pullup_wrong_domain",
        "reset_early_release",
        "brownout_short_interrupt",
        "prebiased_output",
        "load_step",
        "overload",
        "partial_power",
        "externally_driven_unpowered_pin",
        "invalid_strap",
        "late_strap",
        "clock_missing",
        "clock_late",
        "repeat_power_cycles",
        "incomplete_discharge",
    }
    assert set(REQUIRED_SCENARIO_IDS) == expected
    for scenario_id in REQUIRED_SCENARIO_IDS:
        spec = scenario(scenario_id)
        assert spec.title and spec.description
        assert spec.intent in ("satisfy", "violate_detected")
    with pytest.raises(KeyError):
        scenario("not_a_scenario")


def test_fault_scenarios_expect_a_detected_violation() -> None:
    for scenario_id in ("reset_early_release", "pg_missing", "invalid_strap"):
        assert scenario(scenario_id).intent == "violate_detected"
    assert scenario("nominal_startup").intent == "satisfy"


def test_case_can_reference_every_scenario() -> None:
    """Every scenario id must be usable as a case's scenario id."""
    for scenario_id in REQUIRED_SCENARIO_IDS:
        case = make_case(scenario_id=scenario_id)
        spec = scenario(case.scenario_id)
        assert spec.scenario_id == scenario_id
