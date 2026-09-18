"""Test evaluation engine (Phase 1 step 9 + status propagation).

Turns :class:`~boardmodeler.pipeline.runner.RunArtifacts` into a
:class:`~boardmodeler.domain.records.TestResult` by evaluating the expressions of
every linked requirement, applying the honesty gates, and honouring the case's
expectation kind.

Gates applied before any expression is evaluated (each produces UNKNOWN with a
machine-readable reason):

* ``requirement_not_found`` — the case references a requirement that does not exist.
* ``requirement_conflict_unresolved`` — the requirement is marked ``conflict``.
* ``citation_unverified`` — a document-sourced requirement whose excerpt could not
  be found on the cited page.
* ``model_capability_unsupported`` — the model's capability probe does not cover
  the behaviour this requirement needs (Phase 2 step 6).
* ``requirement_has_no_expression`` — nothing to evaluate.

Combination rule: FAIL dominates (a definite violation is conclusive), then
BLOCKED, then UNKNOWN, then NOT_APPLICABLE, then PASS. Every verdict's measured
values are preserved in ``TestResult.measured``, so a result is never empty of
evidence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from boardmodeler.domain.enums import Status
from boardmodeler.domain.records import ModelCapability, Requirement, TestCase, TestResult
from boardmodeler.pipeline.runner import RunArtifacts
from boardmodeler.verification.assertions import Connectivity, EvalContext, Verdict, evaluate
from boardmodeler.verification.scenarios import SCENARIOS

__all__ = [
    "GATE_PREFIX",
    "RequirementEvaluation",
    "evaluate_case",
    "evaluate_requirements",
    "fallback_reason",
    "gate_from_capability",
    "gate_reason",
]

GATE_PREFIX = "model_capability_unsupported"


def gate_from_capability(
    capabilities: Sequence[ModelCapability],
    requirement_behaviors: Mapping[str, str],
) -> dict[str, str]:
    """Requirements a model cannot support, keyed by requirement id.

    ``requirement_behaviors`` maps a requirement to the capability behavior it
    depends on (e.g. ``REQ_X -> "startup"``). A requirement is gated unless at
    least one available model reports that behavior as ``supported`` — a
    ``not_tested``/``unknown``/``unsupported`` model is exactly the case a vendor
    "average model" would otherwise slide through.
    """
    gate: dict[str, str] = {}
    if not capabilities:
        return gate
    for requirement_id, behavior in requirement_behaviors.items():
        states = [
            f"{capability.model_id}: {behavior}={capability.behaviors.get(behavior, 'unknown')}"
            for capability in capabilities
        ]
        if any(
            capability.behaviors.get(behavior, "unknown") == "supported"
            for capability in capabilities
        ):
            continue
        gate[requirement_id] = (
            f"no available model supports the {behavior!r} behavior ({'; '.join(states)})"
        )
    return gate


@dataclass(frozen=True)
class RequirementEvaluation:
    """One requirement's verdict inside a case."""

    requirement_id: str
    verdict: Verdict
    gated_reason: str | None = None

    @property
    def status(self) -> Status:
        return self.verdict.status


def gate_reason(requirement: Requirement, capability_gate: Mapping[str, str] | None) -> str | None:
    """Why this requirement cannot be evaluated at all, or ``None``."""
    if requirement.status == "conflict":
        detail = "; ".join(requirement.conflicts) or "conflicting sources were not resolved"
        return f"requirement_conflict_unresolved: {detail}"
    if requirement.origin.value == "DOCUMENT" and not requirement.citation_verified:
        return (
            "citation_unverified: the excerpt for this requirement was not found on the "
            "cited page, so it is not treated as device data"
        )
    if capability_gate is not None:
        reason = capability_gate.get(requirement.req_id)
        if reason:
            return f"{GATE_PREFIX}: {reason}"
    if requirement.expression is None:
        return "requirement_has_no_expression: the requirement carries no machine-checkable form"
    return None


def evaluate_requirements(
    case: TestCase,
    artifacts: RunArtifacts,
    requirements: Mapping[str, Requirement],
    *,
    connectivity: Connectivity | None = None,
    supply_domains: Mapping[str, str] | None = None,
    capability_gate: Mapping[str, str] | None = None,
) -> list[RequirementEvaluation]:
    """Evaluate every requirement linked by ``case`` against ``artifacts``."""
    context = EvalContext(
        raw=artifacts.raw,
        usability=artifacts.usability,
        connectivity=connectivity,
        supply_domains=supply_domains or {},
        tolerances=case.tolerance,
    )
    evaluations: list[RequirementEvaluation] = []
    for requirement_id in case.requirement_ids:
        requirement = requirements.get(requirement_id)
        if requirement is None:
            evaluations.append(
                RequirementEvaluation(
                    requirement_id=requirement_id,
                    verdict=Verdict(
                        status=Status.UNKNOWN,
                        detail=f"requirement {requirement_id} is not part of the frozen baseline",
                        unknown_reason="requirement_not_found",
                    ),
                )
            )
            continue
        reason = gate_reason(requirement, capability_gate)
        if reason is not None:
            evaluations.append(
                RequirementEvaluation(
                    requirement_id=requirement_id,
                    verdict=Verdict(
                        status=Status.UNKNOWN,
                        detail=f"{requirement.statement} — not evaluated: {reason}",
                        unknown_reason=reason.split(":", 1)[0],
                    ),
                    gated_reason=reason,
                )
            )
            continue
        assert requirement.expression is not None  # guaranteed by gate_reason
        verdict = evaluate(requirement.expression, context)
        evaluations.append(RequirementEvaluation(requirement_id=requirement_id, verdict=verdict))
    return evaluations


def _merge_measured(evaluations: Sequence[RequirementEvaluation]) -> dict[str, float | str]:
    measured: dict[str, float | str] = {}
    multi = len(evaluations) > 1
    for evaluation in evaluations:
        for key, value in evaluation.verdict.measured.items():
            measured[f"{evaluation.requirement_id}.{key}" if multi else key] = value
    return measured


def _combined_status(evaluations: Sequence[RequirementEvaluation]) -> Status:
    statuses = [e.status for e in evaluations]
    if not statuses:
        return Status.UNKNOWN
    if Status.FAIL in statuses:
        return Status.FAIL
    if Status.BLOCKED in statuses:
        return Status.BLOCKED
    if Status.UNKNOWN in statuses:
        return Status.UNKNOWN
    if all(status is Status.NOT_APPLICABLE for status in statuses):
        return Status.NOT_APPLICABLE
    return Status.PASS


def _detail_for(status: Status, evaluations: Sequence[RequirementEvaluation]) -> str:
    ordering = {
        Status.FAIL: 0,
        Status.BLOCKED: 1,
        Status.UNKNOWN: 2,
        Status.NOT_APPLICABLE: 3,
        Status.PASS: 4,
    }
    ordered = sorted(evaluations, key=lambda e: ordering[e.status])
    parts = [f"{e.requirement_id}: {e.verdict.detail}" for e in ordered[:3]]
    if len(ordered) > 3:
        parts.append(f"... and {len(ordered) - 3} more requirement(s)")
    return f"[{status.value}] " + " | ".join(parts)


def evaluate_case(
    case: TestCase,
    artifacts: RunArtifacts,
    requirements: Mapping[str, Requirement],
    *,
    connectivity: Connectivity | None = None,
    supply_domains: Mapping[str, str] | None = None,
    capability_gate: Mapping[str, str] | None = None,
) -> TestResult:
    """Judge one case against one run and return its :class:`TestResult`."""
    evaluations = evaluate_requirements(
        case,
        artifacts,
        requirements,
        connectivity=connectivity,
        supply_domains=supply_domains,
        capability_gate=capability_gate,
    )
    measured = _merge_measured(evaluations)
    status = _combined_status(evaluations)
    measured["expected_reach_s"] = artifacts.diagnostics.expected_stop_s or "n/a"
    measured["reached_s"] = artifacts.diagnostics.reached_s or "n/a"

    scenario = SCENARIOS.get(case.scenario_id)
    unknown_reason: str | None = None
    blocked_reason: str | None = None
    detail = _detail_for(status, evaluations)

    if case.expected.kind == "violate_detected":
        status, detail, unknown_reason, blocked_reason = _apply_fault_expectation(
            case, artifacts, evaluations, status, detail
        )

    if status is Status.UNKNOWN:
        unknown_reason = _first_reason(evaluations, Status.UNKNOWN) or fallback_reason(detail)
        if artifacts.blocked_reason and not artifacts.usability.usable:
            unknown_reason = f"{unknown_reason} (run state: {artifacts.blocked_reason})"
    if status is Status.BLOCKED:
        blocked_reason = artifacts.blocked_reason or _first_reason(evaluations, Status.BLOCKED)
        if not blocked_reason:
            blocked_reason = "sim_unusable"
        detail = f"[BLOCKED] {detail} | run: {artifacts.detail}"

    not_exercised = [e.requirement_id for e in evaluations if e.status is Status.NOT_APPLICABLE]
    if not_exercised and status is not Status.NOT_APPLICABLE:
        detail += f" | coverage gap: {', '.join(not_exercised)} not exercised in this scenario"

    return TestResult(
        test_id=case.test_id,
        status=status,
        requirement_ids=list(case.requirement_ids),
        measured=measured,
        expected=case.expected.detail,
        detail=_with_scenario(detail, scenario),
        waveform_refs=[
            ref
            for ref in (artifacts.waveform_ref(signal) for signal in sorted(set(case.measurement)))
            if ref
        ],
        log_ref=artifacts.log_ref(),
        run_id=artifacts.run_id,
        duration_s=round(artifacts.duration_s, 6),
        unknown_reason=unknown_reason,
        blocked_reason=blocked_reason,
    )


def _with_scenario(detail: str, scenario: object) -> str:
    """Append the scenario's identity so a result is readable without lookups."""
    if scenario is None:
        return detail
    return f"{detail} | scenario {scenario.scenario_id} ({scenario.kind}, {scenario.intent})"


def fallback_reason(detail: str) -> str:
    """Fallback unknown reason derived from the detail text."""
    return detail.split(":", 1)[0].strip("[] ") or "unknown"


def _first_reason(evaluations: Sequence[RequirementEvaluation], status: Status) -> str | None:
    for evaluation in evaluations:
        if evaluation.status is status:
            if status is Status.UNKNOWN:
                return evaluation.verdict.unknown_reason
            if status is Status.BLOCKED:
                return evaluation.verdict.blocked_reason
    return None


def _apply_fault_expectation(
    case: TestCase,
    artifacts: RunArtifacts,
    evaluations: Sequence[RequirementEvaluation],
    status: Status,
    detail: str,
) -> tuple[Status, str, str | None, str | None]:
    """Fault-detection semantics for ``violate_detected`` cases.

    * The violation is observed (a FAIL verdict) and the run produced data: PASS —
      the check did its job.
    * The run produced no data at all (no simulator / unreadable output): BLOCKED.
    * The run started but did not complete: FAIL, because an incomplete run cannot
      demonstrate the fault.
    * Everything evaluated cleanly and no violation occurred: FAIL — the fault went
      undetected.
    """
    failing = [e for e in evaluations if e.status is Status.FAIL]
    if failing:
        names = ", ".join(e.requirement_id for e in failing)
        return (
            Status.PASS,
            f"[PASS] violation detected as expected ({case.expected.detail}); "
            f"requirement(s) {names}: {failing[0].verdict.detail}",
            None,
            None,
        )

    if not artifacts.usability.usable:
        reason = artifacts.blocked_reason or "sim_unusable"
        produced_no_data = reason.startswith(("simulator_unavailable", "sim_output"))
        if produced_no_data:
            return (
                Status.BLOCKED,
                f"[BLOCKED] {detail} | the run produced no data ({reason}), so the expected "
                f"violation could not be shown",
                None,
                reason,
            )
        return (
            Status.FAIL,
            f"[FAIL] the run did not complete ({reason}), so the expected violation "
            f"({case.expected.detail}) was not demonstrated: {artifacts.detail}",
            None,
            None,
        )

    if status is Status.UNKNOWN:
        return status, f"[UNKNOWN] {detail}", _first_reason(evaluations, Status.UNKNOWN), None
    if status is Status.NOT_APPLICABLE:
        return (
            Status.UNKNOWN,
            f"[UNKNOWN] the fault scenario {case.scenario_id!r} had nothing to evaluate: {detail}",
            "scenario_not_exercised",
            None,
        )
    if status is Status.PASS:
        return (
            Status.FAIL,
            f"[FAIL] the expected violation was not detected: {detail}",
            None,
            None,
        )
    return status, detail, None, None
