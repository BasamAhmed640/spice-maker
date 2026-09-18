"""Baseline regression and the honest qualification gate (Phase 6 steps 1-2).

Two responsibilities:

* :func:`compare_baseline` — compare a produced ``dict[str, float]`` (waveform
  statistics measured from a real run) against a committed baseline JSON.  A
  value missing on *either* side is UNKNOWN, never PASS; a value outside its
  declared tolerance is FAIL with the observed deviation in a
  :class:`~boardmodeler.domain.records.Finding`.  The tolerance default is 1 %
  relative, overridden per value by the baseline's ``tolerances`` entry.
* :func:`request_device_qualification` — the explicit blocked path for a real
  PCIe switch.  This entry point lives here rather than in
  ``models/templates.py`` because it is the same honesty boundary as the
  baseline comparison: a request is answered with a status and a reason, never
  with a fabricated identity, requirement list or model.  A synthetic fixture
  cannot qualify a real device, so it is refused exactly like missing
  documentation: ``Status.BLOCKED`` with reason
  ``device_documentation_unavailable``.

Baselines are *produced*, never hand-written: :func:`run_family_deck` writes the
contract's application deck, runs it under real LTspice, checks the log, reads
the ``.raw`` and measures it with :func:`measure_raw`.  :func:`describe_baseline`
then records the values together with the deck conditions in ``note`` and the
producing call in ``produced_by``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from boardmodeler.domain.enums import Status
from boardmodeler.domain.records import (
    DocumentRecord,
    Finding,
    PartIdentity,
    Requirement,
)
from boardmodeler.models.templates import (
    TemplateContract,
    build_deck_spec,
    write_application_deck,
)
from boardmodeler.simulation.deck import MeasSpec
from boardmodeler.simulation.log import parse_log
from boardmodeler.simulation.ltspice import LtspiceLockTimeout, run_batch
from boardmodeler.simulation.ltspice import version as ltspice_version
from boardmodeler.simulation.raw import RawFile, read_raw
from boardmodeler.verification.corners import worst_status

__all__ = [
    "DEFAULT_TOLERANCE_REL",
    "QUALIFICATION_BLOCKED_REASON",
    "QUALIFICATION_UNKNOWN_REASON",
    "BaselineComparison",
    "BaselineDocument",
    "BaselineTolerance",
    "FamilyRun",
    "QualificationOutcome",
    "QualificationRequest",
    "compare_baseline",
    "describe_baseline",
    "load_baseline",
    "measure_raw",
    "request_device_qualification",
    "run_family_deck",
    "write_baseline",
]

DEFAULT_TOLERANCE_REL = 0.01
"""A baseline value with no declared tolerance is compared within 1 % relative."""

DEFAULT_TIMEOUT_S = 180.0
"""Wall-clock limit for one family regression run (LTspice serialises runs)."""

QUALIFICATION_BLOCKED_REASON = "device_documentation_unavailable"
"""Exact reason a real-device qualification request is blocked with."""

QUALIFICATION_UNKNOWN_REASON = "qualification_requires_extraction"
"""Reason for a request whose documentation exists but has not been processed here."""

_DEVICE_DOC_TYPES = frozenset({"datasheet", "errata", "design_guide", "app_note", "user_guide"})
_SYNTHETIC_DOC_TYPES = frozenset({"synthetic_contract"})


def _g(value: float) -> str:
    return f"{value:.6g}"


# --------------------------------------------------------------------------- #
# baseline documents


class BaselineTolerance(BaseModel):
    """Relative and absolute tolerance for one baseline value."""

    model_config = ConfigDict(extra="forbid")

    rel: float = DEFAULT_TOLERANCE_REL
    abs: float = 0.0

    @model_validator(mode="after")
    def _check_non_negative(self) -> BaselineTolerance:
        if self.rel < 0 or self.abs < 0:
            raise ValueError(f"tolerances must be >= 0, got rel={self.rel} abs={self.abs}")
        return self


class BaselineDocument(BaseModel):
    """A committed baseline: observed values, tolerances and how they were produced."""

    model_config = ConfigDict(extra="forbid")

    family: str
    values: dict[str, float]
    tolerances: dict[str, BaselineTolerance] = Field(default_factory=dict)
    produced_by: str
    note: str

    @model_validator(mode="after")
    def _check_tolerance_names(self) -> BaselineDocument:
        unknown = [name for name in self.tolerances if name not in self.values]
        if unknown:
            raise ValueError(f"tolerance(s) declared for values that do not exist: {unknown}")
        return self


def load_baseline(path: str | Path) -> BaselineDocument:
    """Read a committed baseline JSON (strict: an unknown field is an error)."""
    return BaselineDocument.model_validate_json(Path(path).read_text(encoding="utf-8"))


def write_baseline(path: str | Path, document: BaselineDocument) -> Path:
    """Write a baseline JSON deterministically and return the path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    return target


@dataclass(frozen=True)
class BaselineComparison:
    """Outcome of comparing produced values against a baseline."""

    status: Status
    findings: list[Finding] = field(default_factory=list)
    deviations: dict[str, float] = field(default_factory=dict)
    """Per value: relative deviation when the baseline value is non-zero, else absolute."""

    def summary(self) -> str:
        if self.status is Status.PASS:
            return f"all {len(self.deviations)} baseline value(s) within tolerance"
        return "; ".join(f"[{finding.code}] {finding.message}" for finding in self.findings)


def compare_baseline(
    produced: Mapping[str, float],
    baseline: BaselineDocument,
    *,
    family: str | None = None,
) -> BaselineComparison:
    """Compare measured values against a baseline, value by value.

    Missing on either side → UNKNOWN; outside tolerance → FAIL.  The overall
    status is the most severe finding status (FAIL beats UNKNOWN, BLOCKED beats
    FAIL — the shared ``verification.corners.worst_status`` ordering), and PASS
    only when there are no findings at all.
    """
    if family is not None and baseline.family != family:
        raise ValueError(
            f"baseline family {baseline.family!r} does not match the requested {family!r}"
        )
    findings: list[Finding] = []
    deviations: dict[str, float] = {}
    for name, expected in baseline.values.items():
        if name not in produced:
            findings.append(
                Finding(
                    code="baseline_value_missing",
                    status=Status.UNKNOWN,
                    message=(
                        f"baseline value {name!r} has no produced counterpart, so nothing "
                        "was measured for it"
                    ),
                    detail={"baseline": _g(expected), "produced": "<missing>"},
                )
            )
            continue
        observed = float(produced[name])
        tolerance = baseline.tolerances.get(name, BaselineTolerance())
        allowed = max(tolerance.abs, tolerance.rel * abs(expected))
        difference = observed - expected
        deviations[name] = difference / abs(expected) if expected else difference
        if abs(difference) > allowed:
            findings.append(
                Finding(
                    code="baseline_deviation",
                    status=Status.FAIL,
                    message=(
                        f"{name!r} measured {_g(observed)} against a baseline of {_g(expected)} "
                        f"(deviation {_g(difference)}, allowed {_g(allowed)})"
                    ),
                    detail={
                        "baseline": _g(expected),
                        "produced": _g(observed),
                        "difference": _g(difference),
                        "allowed": _g(allowed),
                        "tolerance_rel": _g(tolerance.rel),
                        "tolerance_abs": _g(tolerance.abs),
                    },
                )
            )
    for name in produced:
        if name not in baseline.values:
            findings.append(
                Finding(
                    code="produced_value_unbaselined",
                    status=Status.UNKNOWN,
                    message=(
                        f"produced value {name!r} is not in the baseline, so it cannot be "
                        "compared to anything"
                    ),
                    detail={"produced": _g(float(produced[name]))},
                )
            )
    status = worst_status([finding.status for finding in findings]) if findings else Status.PASS
    return BaselineComparison(status=status, findings=findings, deviations=deviations)


# --------------------------------------------------------------------------- #
# running a contract's application deck


@dataclass(frozen=True)
class FamilyRun:
    """One real run of a contract's application deck, with its measured values."""

    family: str
    deck: Path
    library: Path
    raw_path: Path
    log_path: Path
    values: dict[str, float]
    wall_s: float
    ltspice_version: str | None


def measure_raw(raw: RawFile, measures: Sequence[MeasSpec]) -> dict[str, float]:
    """Measure a waveform exactly as the deck's ``.meas`` cards describe it.

    Values are computed from the saved ``.raw`` (never from a synthesised grid);
    a missing signal or an empty window raises instead of returning a value.
    """
    time = raw.time_column()
    if time is None:
        raise ValueError(f"{raw.path}: no time variable, so no transient measurement is possible")
    values: dict[str, float] = {}
    for measure in measures:
        if not raw.has(measure.signal):
            raise KeyError(
                f"{raw.path}: signal {measure.signal!r} was not saved, so {measure.name!r} "
                "cannot be measured"
            )
        column = raw.column(measure.signal)
        if measure.kind == "find_at":
            if measure.at_s is None:
                raise ValueError(f"{measure.name}: find_at needs at_s")
            if not float(time[0]) <= measure.at_s <= float(time[-1]):
                raise ValueError(
                    f"{measure.name}: at_s={measure.at_s!r} is outside the saved range "
                    f"[{float(time[0]):.6g}, {float(time[-1]):.6g}] s"
                )
            values[measure.name] = float(np.interp(measure.at_s, time, column))
            continue
        if measure.from_s is None or measure.to_s is None:
            raise ValueError(f"{measure.name}: {measure.kind} needs from_s and to_s")
        lo = int(np.searchsorted(time, measure.from_s, side="left"))
        hi = int(np.searchsorted(time, measure.to_s, side="right"))
        if hi <= lo:
            raise ValueError(
                f"{measure.name}: no saved points in [{measure.from_s}, {measure.to_s}] s"
            )
        window = column[lo:hi]
        if measure.kind == "avg":
            values[measure.name] = float(np.mean(window))
        elif measure.kind == "max":
            values[measure.name] = float(np.max(window))
        elif measure.kind == "min":
            values[measure.name] = float(np.min(window))
        else:  # pp
            values[measure.name] = float(np.max(window) - np.min(window))
    return values


def _measure_specs(contract: TemplateContract) -> list[MeasSpec]:
    return [
        MeasSpec(
            name=measure.name,
            signal=measure.signal,
            kind=measure.kind,
            at_s=measure.at_s,
            from_s=measure.from_s,
            to_s=measure.to_s,
        )
        for measure in contract.deck.measures
    ]


def run_family_deck(
    contract: TemplateContract,
    *,
    exe: str | Path,
    workdir: str | Path,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> FamilyRun:
    """Run the contract's application deck under LTspice and measure the result.

    Any incomplete run (non-zero exit, timeout, missing artifact, log without a
    completion marker, simulator error) raises with the observed detail; no value
    is ever produced without an observed artifact.
    """
    target_dir = Path(workdir)
    target_dir.mkdir(parents=True, exist_ok=True)
    application = write_application_deck(contract, target_dir / f"{contract.family}.cir")
    try:
        result = run_batch(Path(exe), application.deck, target_dir, timeout_s=timeout_s)
    except LtspiceLockTimeout as exc:
        raise RuntimeError(f"{contract.family}: simulator unavailable: {exc}") from exc
    detail = (
        f"exit_code={result.exit_code} timed_out={result.timed_out} "
        f"cancelled={result.cancelled} wall_s={result.wall_s:.2f}"
    )
    if result.timed_out or result.cancelled or result.exit_code != 0:
        raise RuntimeError(f"{contract.family}: LTspice run failed ({detail})")
    if result.raw_path is None or not result.raw_path.is_file():
        raise RuntimeError(f"{contract.family}: no .raw produced ({detail})")
    if result.log_path is None or not result.log_path.is_file():
        raise RuntimeError(f"{contract.family}: no .log produced ({detail})")
    summary = parse_log(result.log_path)
    if not summary.completed:
        raise RuntimeError(
            f"{contract.family}: the run did not complete ({detail}); log: {summary.text[-300:]!r}"
        )
    if summary.errors or summary.convergence_issues:
        raise RuntimeError(
            f"{contract.family}: simulator reported errors {summary.errors} / "
            f"convergence {summary.convergence_issues}"
        )
    raw = read_raw(result.raw_path)
    values = measure_raw(raw, _measure_specs(contract))
    return FamilyRun(
        family=contract.family,
        deck=application.deck,
        library=application.library,
        raw_path=result.raw_path,
        log_path=result.log_path,
        values=values,
        wall_s=result.wall_s,
        ltspice_version=ltspice_version(Path(exe)),
    )


def describe_baseline(contract: TemplateContract, run: FamilyRun) -> BaselineDocument:
    """Build the baseline document for a completed run, tolerances included."""
    tolerances: dict[str, BaselineTolerance] = {}
    for measure in contract.deck.measures:
        if measure.rel is None and measure.abs is None:
            continue
        tolerances[measure.name] = BaselineTolerance(
            rel=DEFAULT_TOLERANCE_REL if measure.rel is None else measure.rel,
            abs=0.0 if measure.abs is None else measure.abs,
        )
    spec = build_deck_spec(contract, library=run.library)
    lines = spec.render(run.deck.parent).splitlines()
    conditions = [
        line
        for line in lines
        if line and not line.startswith((".include", ".backanno", ".end", "*"))
    ]
    note = (
        f"{contract.part} ({contract.subckt}) template instantiation, deck "
        f"'{contract.deck.title}' ({run.deck.name}); conditions: {'; '.join(conditions)}; "
        f"values measured from {run.raw_path.name} by "
        f"boardmodeler.models.regression.measure_raw; simulator "
        f"LTspice {run.ltspice_version or 'unknown version'}"
    )
    produced_by = (
        f"boardmodeler.models.regression.run_family_deck with LTspice "
        f"{run.ltspice_version or 'unknown version'}, invoked from "
        "tests/regression/test_templates.py --generate"
    )
    return BaselineDocument(
        family=contract.family,
        values=dict(run.values),
        tolerances=tolerances,
        produced_by=produced_by,
        note=note,
    )


# --------------------------------------------------------------------------- #
# real-device qualification gate


@dataclass(frozen=True)
class QualificationRequest:
    """A request to qualify a real device.

    ``identity`` is what the caller knows; it is the *request*, never an outcome
    — the gate echoes no identity and derives none.
    """

    part: str
    identity: PartIdentity | None = None
    documents: tuple[DocumentRecord, ...] = ()


@dataclass(frozen=True)
class QualificationOutcome:
    """The gate's answer: a status and a reason, never a fabricated artifact.

    ``identity``, ``requirements`` and ``model_text`` stay empty in every
    outcome this gate produces: requests are answered with documentation facts,
    and nothing is inferred about the device itself.
    """

    request: QualificationRequest
    status: Status
    reason: str
    detail: str
    usable_documents: tuple[str, ...] = ()
    identity: PartIdentity | None = None
    requirements: tuple[Requirement, ...] = ()
    model_text: str | None = None


def _is_device_document(document: DocumentRecord) -> bool:
    """Whether a record can serve as device documentation for qualification."""
    return document.doc_type in _DEVICE_DOC_TYPES and document.provenance != "synthetic_fixture"


def _is_synthetic_document(document: DocumentRecord) -> bool:
    return document.doc_type in _SYNTHETIC_DOC_TYPES or document.provenance == "synthetic_fixture"


def request_device_qualification(request: QualificationRequest) -> QualificationOutcome:
    """Answer a real-device qualification request honestly.

    * no usable documentation → ``BLOCKED`` with reason
      ``device_documentation_unavailable``;
    * only synthetic fixture documentation → ``BLOCKED`` with the same reason,
      because a synthetic contract states no device data and cannot qualify a
      real device (its requirements carry ``origin=TEST_FIXTURE``);
    * usable documentation present → ``UNKNOWN``: documentation exists, but this
      gate performs no extraction, model build or simulation, so no PASS is
      possible here.
    """
    part = request.part.strip()
    if not part:
        raise ValueError("QualificationRequest.part must not be empty")
    usable = [document for document in request.documents if _is_device_document(document)]
    synthetic = [document for document in request.documents if _is_synthetic_document(document)]
    if usable:
        return QualificationOutcome(
            request=request,
            status=Status.UNKNOWN,
            reason=QUALIFICATION_UNKNOWN_REASON,
            detail=(
                f"{part!r}: documentation is available "
                f"({', '.join(document.doc_id for document in usable)}), but this gate performs "
                "no extraction, model build or simulation, so qualification is not established"
            ),
            usable_documents=tuple(document.doc_id for document in usable),
        )
    if synthetic:
        return QualificationOutcome(
            request=request,
            status=Status.BLOCKED,
            reason=QUALIFICATION_BLOCKED_REASON,
            detail=(
                f"{part!r}: only synthetic fixture documentation was supplied "
                f"({', '.join(document.doc_id for document in synthetic)}); a synthetic contract "
                "states no device data and cannot qualify a real device — its requirements carry "
                "origin=TEST_FIXTURE, so no identity, requirement or model may be derived from it"
            ),
        )
    return QualificationOutcome(
        request=request,
        status=Status.BLOCKED,
        reason=QUALIFICATION_BLOCKED_REASON,
        detail=(
            f"{part!r}: no device documentation was supplied; a real-device qualification "
            "requires a datasheet or equivalent document, and none may be fabricated"
        ),
    )
