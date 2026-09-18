"""Scenario catalogue (Phase 1 step 9).

A scenario is a named bring-up situation with a declared intent. The ids below
are the vocabulary the whole product speaks: tests, the fault matrix, the report
and the coverage artifact all reference scenario ids, and the CLI can list them.

Intent matters for honesty: a *fault* scenario is expected to show a violation
(``violate_detected``); an *operating* scenario is expected to satisfy its
requirements; a *boundary* scenario exercises a limit and may legitimately end
NOT_APPLICABLE when its precondition is absent from the device under study.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from boardmodeler.domain.enums import RequirementKind

__all__ = [
    "REQUIRED_SCENARIO_IDS",
    "SCENARIOS",
    "ScenarioSpec",
    "all_scenario_ids",
    "scenario",
    "scenario_ids_for_kind",
]

ScenarioKind = Literal["operating", "fault", "boundary"]
Intent = Literal["satisfy", "violate_detected"]


@dataclass(frozen=True)
class ScenarioSpec:
    """One scenario: what it is, when it applies, and what it must show."""

    scenario_id: str
    title: str
    kind: ScenarioKind
    intent: Intent
    description: str
    requirement_kinds: tuple[RequirementKind, ...] = ()
    stimulus: tuple[str, ...] = ()
    checks: tuple[str, ...] = field(default_factory=tuple)
    coverage_note: str = ""


def _s(
    scenario_id: str,
    title: str,
    kind: ScenarioKind,
    intent: Intent,
    description: str,
    *,
    requirement_kinds: tuple[RequirementKind, ...] = (),
    stimulus: tuple[str, ...] = (),
    checks: tuple[str, ...] = (),
    coverage_note: str = "",
) -> ScenarioSpec:
    return ScenarioSpec(
        scenario_id=scenario_id,
        title=title,
        kind=kind,
        intent=intent,
        description=description,
        requirement_kinds=requirement_kinds,
        stimulus=stimulus,
        checks=checks,
        coverage_note=coverage_note,
    )


_ELECTRICAL = RequirementKind.ELECTRICAL
_TEMPORAL = RequirementKind.TEMPORAL
_CONNECTIVITY = RequirementKind.CONNECTIVITY
_FUNCTIONAL = RequirementKind.FUNCTIONAL
_SYSTEM = RequirementKind.SYSTEM

_SCENARIO_LIST: tuple[ScenarioSpec, ...] = (
    _s(
        "nominal_startup",
        "Nominal power-up",
        "operating",
        "satisfy",
        "All rails ramp with their declared rise times, reset releases after the rails "
        "and power-good are valid, and the device reaches its declared operating state.",
        requirement_kinds=(_ELECTRICAL, _TEMPORAL, _FUNCTIONAL),
        stimulus=("VIN ramp", "EN release", "loads at nominal"),
        checks=("rail limits", "reset release delay", "PG assertion"),
    ),
    _s(
        "nominal_shutdown",
        "Nominal power-down",
        "operating",
        "satisfy",
        "VIN falls with its declared fall time; converters stop switching, outputs "
        "discharge through the declared discharge path, and PG deasserts.",
        requirement_kinds=(_ELECTRICAL, _TEMPORAL),
        stimulus=("VIN falling ramp",),
        checks=("PG deassertion", "output discharge", "no reverse current"),
    ),
    _s(
        "slow_rail",
        "Slow rail rise",
        "boundary",
        "satisfy",
        "A rail rises much more slowly than nominal; dependent logic must not release "
        "reset early and converters must not mis-regulate.",
        requirement_kinds=(_TEMPORAL, _FUNCTIONAL),
        stimulus=("slow VIN/rail ramp",),
        checks=("reset release", "regulation during ramp"),
    ),
    _s(
        "fast_rail",
        "Fast rail rise",
        "boundary",
        "satisfy",
        "A rail rises faster than nominal (inrush-limited source); no overshoot beyond "
        "the declared limit and no premature reset release.",
        requirement_kinds=(_ELECTRICAL, _TEMPORAL),
        stimulus=("fast VIN/rail ramp",),
        checks=("overshoot", "reset release", "inrush where modeled"),
    ),
    _s(
        "staggered_rails",
        "Staggered rail sequencing",
        "operating",
        "satisfy",
        "Rails come up in the declared order with the declared separation; each "
        "dependent rail starts only after its prerequisite is valid.",
        requirement_kinds=(_TEMPORAL, _SYSTEM),
        stimulus=("sequenced rail ramps",),
        checks=("ordering", "rail separation"),
    ),
    _s(
        "en_before_vin",
        "Enable asserted before the input rail",
        "fault",
        "violate_detected",
        "Enable present while VIN is below its UVLO threshold is a connection/sequencing "
        "error and must be reported rather than assumed harmless.",
        requirement_kinds=(_CONNECTIVITY, _ELECTRICAL),
        stimulus=("EN high from t=0", "VIN slow ramp"),
        checks=("EN/UVLO ordering",),
    ),
    _s(
        "pg_delayed",
        "Power-good delayed beyond its window",
        "boundary",
        "satisfy",
        "PG must assert within the declared delay after the monitored rail is valid.",
        requirement_kinds=(_TEMPORAL,),
        stimulus=("nominal ramp with a delayed PG path",),
        checks=("PG assertion delay",),
    ),
    _s(
        "pg_missing",
        "Power-good never asserts",
        "fault",
        "violate_detected",
        "A PG pin left unconnected or pulled to the wrong rail never asserts, so the "
        "dependent reset must be seen staying asserted.",
        requirement_kinds=(_CONNECTIVITY, _TEMPORAL),
        stimulus=("PG net removed",),
        checks=("reset release blocked",),
    ),
    _s(
        "pullup_missing",
        "Missing pull-up on an open-drain signal",
        "fault",
        "violate_detected",
        "An open-drain signal with no pull-up cannot reach its inactive level; the "
        "dependent logic must not see it as deasserted.",
        requirement_kinds=(_CONNECTIVITY, _FUNCTIONAL),
        stimulus=("pull-up removed",),
        checks=("signal level", "static check SC009"),
    ),
    _s(
        "pullup_wrong_domain",
        "Pull-up on the wrong supply domain",
        "fault",
        "violate_detected",
        "Pulling an open-drain signal up to a rail that is not its own domain is a "
        "connection error even when the netlist looks plausible.",
        requirement_kinds=(_CONNECTIVITY,),
        stimulus=("pull-up moved to another rail",),
        checks=("domain assignment", "static check SC009"),
    ),
    _s(
        "reset_early_release",
        "Reset released before its prerequisites",
        "fault",
        "violate_detected",
        "Reset must stay asserted until every prerequisite rail and PG are valid; "
        "releasing early must be detected with the offending time.",
        requirement_kinds=(_TEMPORAL, _SYSTEM),
        stimulus=("reset hold time shortened",),
        checks=("reset release ordering",),
    ),
    _s(
        "brownout_short_interrupt",
        "Short input interruption",
        "boundary",
        "satisfy",
        "A brief input dip that stays within the hold-up capability must not disturb the "
        "rails or reset; longer dips follow the shutdown path.",
        requirement_kinds=(_ELECTRICAL, _TEMPORAL),
        stimulus=("input dip",),
        checks=("rail hold-up", "reset stability"),
    ),
    _s(
        "prebiased_output",
        "Start-up into a pre-biased output",
        "boundary",
        "satisfy",
        "With the output already held above 0 V, the converter must not sink current "
        "from the pre-bias source during start-up.",
        requirement_kinds=(_ELECTRICAL,),
        stimulus=("output pre-charged", "EN release"),
        checks=("sink current",),
    ),
    _s(
        "load_step",
        "Load transient",
        "operating",
        "satisfy",
        "A declared load step must stay within the output limits (or be reported "
        "UNKNOWN when the model does not reproduce loop dynamics).",
        requirement_kinds=(_ELECTRICAL,),
        stimulus=("load step",),
        checks=("output limits", "recovery"),
        coverage_note="Requires a model whose capability probe says load_transients is supported.",
    ),
    _s(
        "overload",
        "Overload and recovery",
        "boundary",
        "satisfy",
        "A load beyond the current limit must enter the declared protection mode and "
        "recover as declared.",
        requirement_kinds=(_ELECTRICAL, _FUNCTIONAL),
        stimulus=("overload",),
        checks=("current limit", "recovery mode"),
    ),
    _s(
        "partial_power",
        "Partial power (one domain unpowered)",
        "fault",
        "violate_detected",
        "With one supply domain absent, the device must not be driven into an undefined "
        "state, and any externally driven pin on the dead domain must be detected.",
        requirement_kinds=(_CONNECTIVITY, _ELECTRICAL),
        stimulus=("one rail held at 0 V",),
        checks=("dead-domain drive", "static check SC009"),
    ),
    _s(
        "externally_driven_unpowered_pin",
        "Externally driven pin on an unpowered device",
        "fault",
        "violate_detected",
        "A signal driven into a pin whose supply domain is unpowered is the classic "
        "back-powering connection error.",
        requirement_kinds=(_CONNECTIVITY,),
        stimulus=("external drive on a dead domain",),
        checks=("back-power path",),
    ),
    _s(
        "invalid_strap",
        "Invalid strap combination",
        "fault",
        "violate_detected",
        "A strap setting outside the documented set must be reported, not silently "
        "interpreted as a default.",
        requirement_kinds=(_CONNECTIVITY, _SYSTEM),
        stimulus=("strap combination outside the documented set",),
        checks=("strap validity",),
    ),
    _s(
        "late_strap",
        "Strap sampled after its window",
        "fault",
        "violate_detected",
        "Straps are sampled in a documented window; a value that changes after the "
        "window closes must be detected as a connection/timing error.",
        requirement_kinds=(_TEMPORAL, _CONNECTIVITY),
        stimulus=("strap change after the sampling window",),
        checks=("sampling window",),
    ),
    _s(
        "clock_missing",
        "Reference clock missing",
        "fault",
        "violate_detected",
        "With no reference clock, dependent activity must be reported as blocked rather "
        "than assumed to work.",
        requirement_kinds=(_TEMPORAL, _SYSTEM),
        stimulus=("no clock",),
        checks=("clock presence",),
        coverage_note="A clock-availability assumption is a coverage gap, not a verified "
        "clock path.",
    ),
    _s(
        "clock_late",
        "Reference clock late",
        "boundary",
        "satisfy",
        "A clock arriving after its declared deadline must be flagged against the "
        "declared timing budget.",
        requirement_kinds=(_TEMPORAL,),
        stimulus=("delayed clock",),
        checks=("clock arrival window",),
    ),
    _s(
        "repeat_power_cycles",
        "Repeated power cycling",
        "boundary",
        "satisfy",
        "Several power cycles in quick succession must not leave latch-up, incomplete "
        "discharge, or a stuck reset.",
        requirement_kinds=(_TEMPORAL, _FUNCTIONAL),
        stimulus=("repeated ramps",),
        checks=("state after each cycle",),
    ),
    _s(
        "incomplete_discharge",
        "Incomplete output discharge before re-enable",
        "fault",
        "violate_detected",
        "Re-enabling before the output has discharged beyond the declared margin is a "
        "start-up condition violation",
        requirement_kinds=(_ELECTRICAL, _TEMPORAL),
        stimulus=("re-enable during discharge",),
        checks=("discharge margin",),
    ),
)

SCENARIOS: dict[str, ScenarioSpec] = {spec.scenario_id: spec for spec in _SCENARIO_LIST}

REQUIRED_SCENARIO_IDS: tuple[str, ...] = tuple(spec.scenario_id for spec in _SCENARIO_LIST)
"""Exactly the scenario ids the product promises to know about."""


def scenario(scenario_id: str) -> ScenarioSpec:
    """Look up a scenario; an unknown id is an error, never a default."""
    try:
        return SCENARIOS[scenario_id]
    except KeyError as exc:
        raise KeyError(f"unknown scenario {scenario_id!r}; known ids: {sorted(SCENARIOS)}") from exc


def all_scenario_ids() -> tuple[str, ...]:
    return REQUIRED_SCENARIO_IDS


def scenario_ids_for_kind(kind: str) -> tuple[str, ...]:
    return tuple(s.scenario_id for s in SCENARIOS.values() if s.kind == kind)
