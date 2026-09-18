"""Capability probing (D8, Phase 2 step 4).

A model's behaviour is *probed*, never assumed. Each of the ten
:data:`~boardmodeler.domain.records.BEHAVIOR_KEYS` gets one probe deck, run
through the real simulator, and one verdict:

``supported``
    the probe ran and its numeric criterion was met.
``unsupported``
    the model structurally cannot do it (no switch node to produce switching
    waveforms) or we declare it out of scope (temperature dependence).
``unknown``
    the probe ran but could not establish the behaviour; the observed
    measurement and the reason are recorded.
``not_tested``
    no probe exists for it yet, or a prerequisite behaviour failed. A behavior is
    **never** upgraded to ``supported`` without a probe.

The application circuit is described in *logical roles* (``vin``, ``en``, ``fb``,
``pg``, ``vout``, ``gnd``, ``sw``, ``comp``, ``boot``, ``ss``, ``rt``) so the same
probe criteria apply to a vendor model with 15 pads and to a generated
behavioral template with 8 ports.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

import numpy as np

from boardmodeler.domain.enums import EvidenceLevel, ModelKind, Status
from boardmodeler.domain.hashing import sha256_file
from boardmodeler.domain.records import (
    BEHAVIOR_KEYS,
    ExpectationSpec,
    ModelCapability,
    TestCase,
    TestResult,
)
from boardmodeler.pipeline.runner import RunArtifacts, RunContext, run_case
from boardmodeler.simulation.deck import DeckSpec, Include, Source, TranSpec, write_deck

__all__ = [
    "REGULATOR_ROLES",
    "CapabilityProbeReport",
    "ModelProbeSpec",
    "ProbeOutcome",
    "behavior_gate",
    "probe_model",
    "regulator_probes",
]

ProbeStatus = Literal["supported", "unsupported", "unknown", "not_tested"]

REGULATOR_ROLES: tuple[str, ...] = (
    "vin",
    "en",
    "fb",
    "pg",
    "gnd",
    "sw",
    "comp",
    "boot",
    "ss",
    "rt",
    "ilim_mode",
)
"""Logical roles that connect to a *model pin*.

``vout`` is deliberately not here: in a buck application the output net is made
by the inductor, not by a package pin. It is supplied through
:attr:`ModelProbeSpec.nets` (``vout``), and the power stage is added automatically
whenever the model exposes a switch node.
"""


@dataclass(frozen=True)
class ModelProbeSpec:
    """Everything needed to instantiate one model in a probe deck."""

    model_id: str
    kind: ModelKind
    path: Path
    subckt: str
    #: the model's ports in declaration order (an instance must list nodes in
    #: exactly this order, including duplicated package pads)
    ports: tuple[str, ...]
    #: model port -> logical role; every port must be mapped, so a pad can never
    #: be left silently unconnected
    port_roles: Mapping[str, str]
    #: logical role -> the net name the application circuit uses
    nets: Mapping[str, str] = field(default_factory=dict)
    nominal_vout: float = 3.3
    vref: float = 0.8
    rfbt: float = 10e3
    rfbb: float = 3.24e3
    vin: float = 12.0
    load_ohm: float = 3.3
    ilim: float = 3.0
    extra_lib: tuple[Path, ...] = ()
    extra_cards: tuple[str, ...] = ()
    #: cards added to every probe deck (used to tie duplicate package pads and
    #: to power auxiliary control pins)
    extra_elements: tuple[str, ...] = ()
    #: multiplies every probe's analysis window. Vendor transistor-level models can
    #: be 30 s of wall time per millisecond simulated, so a caller may shorten the
    #: windows — the probe windows themselves are all expressed as fractions of
    #: ``tstop`` so the criteria stay valid.
    probe_time_scale: float = 1.0
    notes: list[str] = field(default_factory=list)

    def net(self, role: str) -> str:
        return self.nets.get(role, f"n_{role}")

    def node(self, role: str) -> str:
        return self.roles.get(role, role.upper())

    def has(self, role: str) -> bool:
        """Whether the model exposes a pin for this role."""
        return role in self.port_roles.values()

    def has_net(self, role: str) -> bool:
        return role in self.nets

    def available(self, role: str) -> bool:
        """Whether a probe can reach this role as a pin or as an application net."""
        return self.has(role) or self.has_net(role)

    def unmapped_ports(self) -> list[str]:
        """Ports without a role — a probe must refuse to guess these."""
        return [port for port in self.ports if port not in self.port_roles]

    def instance_nodes(self) -> list[str]:
        """Node list for an instance, in the model's own port order."""
        if self.unmapped_ports():
            raise ValueError(
                f"{self.model_id}: ports {self.unmapped_ports()} have no role, so the probe "
                "circuit cannot connect them"
            )
        return [self.net(self.port_roles[port]) for port in self.ports]


@dataclass(frozen=True)
class ProbeOutcome:
    """One probe's verdict with the measurement behind it."""

    status: ProbeStatus
    measured: dict[str, float | str]
    detail: str
    running: bool = True


class ProbeFunction(Protocol):
    def __call__(self, spec: ModelProbeSpec, ctx: RunContext, workdir: Path) -> ProbeOutcome: ...


@dataclass(frozen=True)
class BehaviorProbe:
    """A named probe for one behaviour key."""

    behavior: str
    title: str
    run: ProbeFunction
    requires: tuple[str, ...] = ()


@dataclass
class CapabilityProbeReport:
    """The capability record plus the probe results that produced it."""

    capability: ModelCapability
    results: list[TestResult]
    outcomes: dict[str, ProbeOutcome]

    def gate(self, requirement_behaviors: Mapping[str, str]) -> dict[str, str]:
        """Requirement ids this model cannot support (see :func:`behavior_gate`)."""
        return behavior_gate(self.capability, requirement_behaviors)

    @property
    def supported(self) -> list[str]:
        return [key for key, state in self.capability.behaviors.items() if state == "supported"]


def behavior_gate(
    capability: ModelCapability, requirement_behaviors: Mapping[str, str]
) -> dict[str, str]:
    """Requirement ids whose behaviour this model does not support.

    ``not_tested`` and ``unknown`` block just as ``unsupported`` does: an
    unprobed behaviour is not a licence to claim it.
    """
    gate: dict[str, str] = {}
    for requirement_id, behavior in requirement_behaviors.items():
        state = capability.behaviors.get(behavior, "unknown")
        if state != "supported":
            gate[requirement_id] = (
                f"model {capability.model_id} reports {behavior}={state} "
                f"(evidence level {capability.evidence_level.value})"
            )
    return gate


# --------------------------------------------------------------------------- #
# application deck


def _application_deck(
    spec: ModelProbeSpec,
    *,
    title: str,
    tstop: float,
    tmax: float,
    en_pulse: Source,
    load: str,
    extra_elements: Sequence[str] = (),
    extra_sources: Sequence[Source] = (),
    save: Sequence[str] = (),
    extra_options: Mapping[str, str | float] = {},
    tstep: float = 0.0,
) -> DeckSpec:
    """One application circuit used by every regulator probe."""
    elements: list[str] = []

    instance = f"XU1 {' '.join(spec.instance_nodes())} {spec.subckt}"

    elements.append(instance)
    # Power stage: when the model exposes a switch node but no output pin (a
    # controller), the output net is made by the inductor + output capacitor,
    # exactly as the datasheet application circuit does.
    if spec.has("sw") and not spec.has("vout"):
        elements.append(f"L1 {spec.net('sw')} {spec.net('vout')} 4.7u")
        elements.append(f"Cout {spec.net('vout')} 0 47u")
    if load:
        elements.append(load)
    elements.extend(spec.extra_elements)
    elements.extend(extra_elements)

    # Input rail: explicit ramp, never an ideal step.
    sources: list[Source] = [
        Source.ramp("V1", spec.net("vin"), "0", v0=0.0, v1=spec.vin, rise_s=200e-6, hold_s=tstop)
    ]
    sources.append(en_pulse)
    if spec.has("fb"):
        elements.append(f"Rfbt {spec.net('vout')} {spec.net('fb')} {spec.rfbt:g}")
        elements.append(f"Rfbb {spec.net('fb')} 0 {spec.rfbb:g}")
    if spec.has("ss"):
        elements.append(f"Css {spec.net('ss')} 0 1n")
    if spec.has("rt"):
        elements.append(f"Rrt {spec.net('rt')} 0 150k")
    if spec.has("boot") and spec.has("sw"):
        elements.append(f"Cboot {spec.net('boot')} {spec.net('sw')} 100n")
    if spec.has("comp"):
        elements.append(f"Rc {spec.net('comp')} 0 10k")
        elements.append(f"Cc {spec.net('comp')} 0 1n")
    if spec.has("pg"):
        elements.append(
            f"Rpg {spec.net('pg')} {spec.net('vout') if spec.has('vout') else spec.net('vin')} 20k"
        )
    sources.extend(extra_sources)

    return DeckSpec(
        title=title,
        includes=tuple(
            [Include(path=str(spec.path.resolve()))]
            + [Include(path=str(p.resolve())) for p in spec.extra_lib]
        ),
        sources=tuple(sources),
        elements=tuple(elements),
        directives=tuple(spec.extra_cards),
        tran=TranSpec(
            tstep=tstep or tstop / 20000.0,
            tstop=tstop,
            tstart=0.0 if tmax else None,
            tmax=tmax,
        ),
        save=tuple(save),
        options={"method": "gear", "trtol": 10, **dict(extra_options)},
    )


def _run_probe(
    spec: ModelProbeSpec,
    ctx: RunContext,
    workdir: Path,
    deck: DeckSpec,
    behavior: str,
) -> tuple[RunArtifacts, Path]:
    run_dir = workdir / behavior
    run_dir.mkdir(parents=True, exist_ok=True)
    deck_path = write_deck(deck, run_dir / "probe.cir")
    case = TestCase(
        test_id=f"T_capability_{behavior}",
        requirement_ids=[],
        scenario_id="nominal_startup",
        scope="model_qualification",
        deck_template=str(deck_path),
        expected=ExpectationSpec(kind="satisfy", detail=f"probe for the {behavior} behaviour"),
        measurement=[],
    )
    artifacts = run_case(
        ctx,
        case,
        build_deck=lambda _d: deck_path,
        run_identifier=f"probe_{spec.model_id}_{behavior}",
    )
    return artifacts, deck_path


def _supports(spec: ModelProbeSpec, behavior: str, roles: Sequence[str]) -> ProbeOutcome | None:
    """Missing *pin* roles make the behaviour structurally impossible."""
    missing = [role for role in roles if not spec.available(role)]
    if missing:
        return ProbeOutcome(
            status="unsupported",
            measured={"missing_roles": ", ".join(missing)},
            detail=(
                f"the model exposes no {', '.join(missing)} connection, so the {behavior} "
                "behaviour cannot occur at its pins"
            ),
        )
    return None


def _startup(spec: ModelProbeSpec, ctx: RunContext, workdir: Path) -> ProbeOutcome:
    structural = _supports(spec, "startup", ["vin", "vout"])
    if structural:
        return structural
    tstop = 6e-3 * spec.probe_time_scale
    deck = _application_deck(
        spec,
        title=f"{spec.model_id} startup probe",
        tstop=tstop,
        tmax=2e-6,
        en_pulse=Source.pulse(
            "Ven",
            spec.net("en"),
            "0",
            v1=0.0,
            v2=3.3,
            delay_s=200e-6,
            width_s=tstop,
            period_s=2 * tstop,
        ),
        load=f"Rload {spec.net('vout')} 0 {spec.load_ohm:g}",
        save=(f"V({spec.net('vout')})", f"V({spec.net('en')})"),
    )
    artifacts, _ = _run_probe(spec, ctx, workdir, deck, "startup")
    if not artifacts.usability.usable or artifacts.raw is None:
        return ProbeOutcome(
            status="unknown",
            measured={"blocked_reason": artifacts.blocked_reason or "unusable run"},
            detail=f"the startup probe could not be evaluated: {artifacts.detail}",
        )
    time_axis = artifacts.raw.time_column()
    assert time_axis is not None
    vout = artifacts.raw.column(f"V({spec.net('vout')})")
    target = spec.vref * (1 + spec.rfbt / spec.rfbb) if spec.has("fb") else spec.nominal_vout
    final = float(np.mean(vout[time_axis > 0.8 * tstop]))
    during = vout[time_axis < 200e-6]
    measured = {
        "vout_final": final,
        "target": target,
        "vout_before_enable": float(np.max(np.abs(during))) if during.size else float("nan"),
        "reached_s": float(time_axis[-1]),
    }
    if abs(final - target) / target <= 0.05:
        return ProbeOutcome(
            status="supported",
            measured=measured,
            detail=f"the output settled at {final:.4f} V against a {target:.4f} V target",
        )
    return ProbeOutcome(
        status="unknown",
        measured=measured,
        detail=(
            f"the output settled at {final:.4f} V against a {target:.4f} V target, so this probe "
            "does not establish the startup behaviour"
        ),
    )


def _shutdown(spec: ModelProbeSpec, ctx: RunContext, workdir: Path) -> ProbeOutcome:
    structural = _supports(spec, "shutdown", ["vin", "vout"])
    if structural:
        return structural
    tstop = 8e-3 * spec.probe_time_scale
    en = Source.pulse(
        "Ven",
        spec.net("en"),
        "0",
        v1=0.0,
        v2=3.3,
        delay_s=0.05 * tstop,
        width_s=0.4 * tstop,
        period_s=tstop,
    )
    deck = _application_deck(
        spec,
        title=f"{spec.model_id} shutdown probe",
        tstop=tstop,
        tmax=2e-6,
        en_pulse=en,
        load=f"Rload {spec.net('vout')} 0 {spec.load_ohm:g}",
        save=(f"V({spec.net('vout')})", f"V({spec.net('en')})"),
    )
    artifacts, _ = _run_probe(spec, ctx, workdir, deck, "shutdown")
    if not artifacts.usability.usable or artifacts.raw is None:
        return ProbeOutcome(
            status="unknown",
            measured={"blocked_reason": artifacts.blocked_reason or "unusable run"},
            detail=f"the shutdown probe could not be evaluated: {artifacts.detail}",
        )
    time_axis = artifacts.raw.time_column()
    assert time_axis is not None
    vout = artifacts.raw.column(f"V({spec.net('vout')})")
    before = float(np.mean(vout[(time_axis > 0.45 * tstop) & (time_axis < 0.55 * tstop)]))
    after = float(np.mean(vout[time_axis > 0.85 * tstop]))
    measured = {"vout_enabled": before, "vout_disabled": after}
    if before > 0.5 and after < 0.5 * before:
        return ProbeOutcome(
            status="supported",
            measured=measured,
            detail=f"the output fell from {before:.4f} V to {after:.4f} V after EN deasserted",
        )
    return ProbeOutcome(
        status="unknown",
        measured=measured,
        detail=(
            f"the output moved from {before:.4f} V to {after:.4f} V after EN deasserted, which "
            "does not establish the shutdown behaviour"
        ),
    )


def _dc_regulation(spec: ModelProbeSpec, ctx: RunContext, workdir: Path) -> ProbeOutcome:
    structural = _supports(spec, "dc_regulation", ["vout"])
    if structural:
        return structural
    tstop = 6e-3 * spec.probe_time_scale
    deck = _application_deck(
        spec,
        title=f"{spec.model_id} dc regulation probe",
        tstop=tstop,
        tmax=2e-6,
        en_pulse=Source.dc("Ven", spec.net("en"), "0", 3.3)
        if spec.has("en")
        else Source.dc("Vdummy", "n_void", "0", 0.0),
        load=f"Rload {spec.net('vout')} 0 {spec.load_ohm:g}",
        save=(f"V({spec.net('vout')})", f"V({spec.net('fb')})")
        if spec.has("fb")
        else (f"V({spec.net('vout')})",),
    )
    artifacts, _ = _run_probe(spec, ctx, workdir, deck, "dc_regulation")
    if not artifacts.usability.usable or artifacts.raw is None:
        return ProbeOutcome(
            status="unknown",
            measured={"blocked_reason": artifacts.blocked_reason or "unusable run"},
            detail=f"the regulation probe could not be evaluated: {artifacts.detail}",
        )
    time_axis = artifacts.raw.time_column()
    assert time_axis is not None
    vout = artifacts.raw.column(f"V({spec.net('vout')})")
    window = time_axis > 0.8 * tstop
    settled = float(np.mean(vout[window]))
    ripple = float(np.max(vout[window]) - np.min(vout[window]))
    target = spec.vref * (1 + spec.rfbt / spec.rfbb) if spec.has("fb") else spec.nominal_vout
    measured = {"vout_mean": settled, "target": target, "ripple_pp": ripple}
    if target != 0 and abs(settled - target) / target <= 0.05:
        return ProbeOutcome(
            status="supported",
            measured=measured,
            detail=(
                f"mean output {settled:.4f} V against a {target:.4f} V divider target "
                f"(ripple {ripple * 1e3:.2f} mVpp)"
            ),
        )
    return ProbeOutcome(
        status="unknown",
        measured=measured,
        detail=(
            f"mean output {settled:.4f} V against a {target:.4f} V divider target "
            f"(error {100 * abs(settled - target) / max(target, 1e-9):.1f} %)"
        ),
    )


def _load_transients(spec: ModelProbeSpec, ctx: RunContext, workdir: Path) -> ProbeOutcome:
    structural = _supports(spec, "load_transients", ["vout"])
    if structural:
        return structural
    tstop = 6e-3 * spec.probe_time_scale
    i_step = min(1.0, spec.vin / max(spec.load_ohm, 1e-3))
    deck = _application_deck(
        spec,
        title=f"{spec.model_id} load transient probe",
        tstop=tstop,
        tmax=1e-6,
        en_pulse=Source.dc("Ven", spec.net("en"), "0", 3.3)
        if spec.has("en")
        else Source.dc("Vd", "n_void", "0", 0.0),
        load=f"Rload {spec.net('vout')} 0 {spec.load_ohm:g}",
        extra_elements=(f"Iload {spec.net('vout')} 0 PULSE(0 {i_step:g} 4m 1u 1u 1m 4m)",),
        save=(f"V({spec.net('vout')})",),
    )
    artifacts, _ = _run_probe(spec, ctx, workdir, deck, "load_transients")
    if not artifacts.usability.usable or artifacts.raw is None:
        return ProbeOutcome(
            status="unknown",
            measured={"blocked_reason": artifacts.blocked_reason or "unusable run"},
            detail=f"the load-transient probe could not be evaluated: {artifacts.detail}",
        )
    time_axis = artifacts.raw.time_column()
    assert time_axis is not None
    vout = artifacts.raw.column(f"V({spec.net('vout')})")
    pre = float(np.mean(vout[(time_axis > 0.58 * tstop) & (time_axis < 0.65 * tstop)]))
    band = (time_axis > 0.67 * tstop) & (time_axis < 0.84 * tstop)
    dip = float(np.min(vout[band])) if np.any(band) else float("nan")
    after = float(np.mean(vout[time_axis > 0.87 * tstop]))
    measured = {
        "vout_pre": pre,
        "vout_min_after_step": dip,
        "vout_settled": after,
        "load_step_a": i_step,
    }
    deviation = abs(pre - dip) / max(pre, 1e-9)
    if deviation <= 0.05 and abs(after - pre) / max(pre, 1e-9) <= 0.02:
        return ProbeOutcome(
            status="supported",
            measured=measured,
            detail=(
                f"a {i_step:.2f} A step moved the output by {deviation * 100:.1f} % and it "
                f"returned to {after:.4f} V"
            ),
        )
    return ProbeOutcome(
        status="unknown",
        measured=measured,
        detail=(
            f"a {i_step:.2f} A step moved the output by {deviation * 100:.1f} % "
            f"(from {pre:.4f} V to {dip:.4f} V), which this probe cannot judge — loop dynamics "
            "are not established by the model"
        ),
    )


def _input_current(spec: ModelProbeSpec, ctx: RunContext, workdir: Path) -> ProbeOutcome:
    structural = _supports(spec, "input_current", ["vin"])
    if structural:
        return structural
    tstop = 6e-3 * spec.probe_time_scale
    deck = _application_deck(
        spec,
        title=f"{spec.model_id} input current probe",
        tstop=tstop,
        tmax=2e-6,
        en_pulse=Source.dc("Ven", spec.net("en"), "0", 3.3)
        if spec.has("en")
        else Source.dc("Vd", "n_void", "0", 0.0),
        load=f"Rload {spec.net('vout')} 0 {spec.load_ohm:g}" if spec.has("vout") else "",
        save=("I(V1)", f"V({spec.net('vin')})", f"V({spec.net('vout')})")
        if spec.has("vout")
        else ("I(V1)", f"V({spec.net('vin')})"),
    )
    artifacts, _ = _run_probe(spec, ctx, workdir, deck, "input_current")
    if not artifacts.usability.usable or artifacts.raw is None:
        return ProbeOutcome(
            status="unknown",
            measured={"blocked_reason": artifacts.blocked_reason or "unusable run"},
            detail=f"the input-current probe could not be evaluated: {artifacts.detail}",
        )
    if not artifacts.raw.has("I(V1)"):
        return ProbeOutcome(
            status="not_tested",
            measured={},
            detail="the supply current was not saved, so no input-current claim can be made",
        )
    time_axis = artifacts.raw.time_column()
    assert time_axis is not None
    window = time_axis > 0.8 * tstop
    i_in = float(-np.mean(artifacts.raw.column("I(V1)")[window]))
    measured: dict[str, float | str] = {"i_in_mean": i_in}
    vout = None
    if artifacts.raw.has(f"V({spec.net('vout')})"):
        vout = float(np.mean(artifacts.raw.column(f"V({spec.net('vout')})")[window]))
        measured["vout_mean"] = vout
    if i_in <= 0:
        return ProbeOutcome(
            status="unknown",
            measured=measured,
            detail=f"the measured supply current is {i_in:.6g} A, which cannot be physical",
        )
    return ProbeOutcome(
        status="supported",
        measured=measured,
        detail=(
            f"steady-state input current is {i_in * 1e3:.3f} mA"
            + (f" at {vout:.3f} V out" if vout is not None else "")
            + "; the probe only establishes the magnitude, not its efficiency model"
        ),
    )


def _current_limit_recovery(spec: ModelProbeSpec, ctx: RunContext, workdir: Path) -> ProbeOutcome:
    structural = _supports(spec, "current_limit_recovery", ["vout"])
    if structural:
        return structural
    tstop = 12e-3 * spec.probe_time_scale
    overload = spec.load_ohm / 6.0
    deck = _application_deck(
        spec,
        title=f"{spec.model_id} current limit probe",
        tstop=tstop,
        tmax=2e-6,
        en_pulse=Source.dc("Ven", spec.net("en"), "0", 3.3)
        if spec.has("en")
        else Source.dc("Vd", "n_void", "0", 0.0),
        load=f"Rload {spec.net('vout')} 0 {spec.load_ohm:g}",
        extra_elements=(
            f"Rov {spec.net('vout')} n_ov {overload:g}",
            "Sov n_ov 0 n_ctl 0 SW_OV",
            f"Vctl n_ctl 0 PWL(0 0 {0.34 * tstop:g} 0 {0.341 * tstop:g} 1 "
            f"{0.68 * tstop:g} 1 {0.681 * tstop:g} 0 {tstop:g} 0)",
            ".model SW_OV SW(Ron=1m Roff=1G Vt=0.5 Vh=0.1)",
        ),
        save=(f"V({spec.net('vout')})", "I(V1)"),
    )
    artifacts, _ = _run_probe(spec, ctx, workdir, deck, "current_limit_recovery")
    if not artifacts.usability.usable or artifacts.raw is None:
        return ProbeOutcome(
            status="unknown",
            measured={"blocked_reason": artifacts.blocked_reason or "unusable run"},
            detail=f"the current-limit probe could not be evaluated: {artifacts.detail}",
        )
    time_axis = artifacts.raw.time_column()
    assert time_axis is not None
    vout = artifacts.raw.column(f"V({spec.net('vout')})")
    normal = float(np.mean(vout[(time_axis > 0.25 * tstop) & (time_axis < 0.32 * tstop)]))
    loaded = float(np.mean(vout[(time_axis > 0.58 * tstop) & (time_axis < 0.66 * tstop)]))
    recovered = float(np.mean(vout[time_axis > 0.85 * tstop]))
    measured = {"vout_normal": normal, "vout_overload": loaded, "vout_recovered": recovered}
    collapses = loaded < 0.8 * normal
    recovers = recovered > 0.9 * normal
    if collapses and recovers:
        return ProbeOutcome(
            status="supported",
            measured=measured,
            detail=(
                f"the output collapsed to {loaded:.4f} V under overload and recovered to "
                f"{recovered:.4f} V afterwards"
            ),
        )
    return ProbeOutcome(
        status="unknown",
        measured=measured,
        detail=(
            f"overload left the output at {loaded:.4f} V (normal {normal:.4f} V) and it settled at "
            f"{recovered:.4f} V; protection behaviour is not established"
        ),
    )


def _switching_waveforms(spec: ModelProbeSpec, ctx: RunContext, workdir: Path) -> ProbeOutcome:
    structural = _supports(spec, "switching_waveforms", ["sw"])
    if structural:
        return structural
    tstop = 1e-3 * spec.probe_time_scale
    deck = _application_deck(
        spec,
        title=f"{spec.model_id} switching probe",
        tstop=tstop,
        tmax=50e-9,
        tstep=tstop / 100000.0,
        en_pulse=Source.dc("Ven", spec.net("en"), "0", 3.3)
        if spec.has("en")
        else Source.dc("Vd", "n_void", "0", 0.0),
        load=f"Rload {spec.net('vout')} 0 {spec.load_ohm:g}",
        save=(f"V({spec.net('sw')})", f"V({spec.net('vout')})"),
    )
    artifacts, _ = _run_probe(spec, ctx, workdir, deck, "switching_waveforms")
    if not artifacts.usability.usable or artifacts.raw is None:
        return ProbeOutcome(
            status="unknown",
            measured={"blocked_reason": artifacts.blocked_reason or "unusable run"},
            detail=f"the switching probe could not be evaluated: {artifacts.detail}",
        )
    time_axis = artifacts.raw.time_column()
    assert time_axis is not None
    sw = artifacts.raw.column(f"V({spec.net('sw')})")
    window = time_axis > 0.7 * tstop
    span = float(np.max(sw[window]) - np.min(sw[window]))
    # Count transitions of the switch node inside the window.
    mid = float(np.mean(sw[window]))
    above = sw[window] > mid
    edges = int(np.count_nonzero(np.diff(above.astype(np.int8)) != 0))
    measured = {"swing_v": span, "edges": float(edges)}
    if span > 1.0 and edges >= 4:
        return ProbeOutcome(
            status="supported",
            measured=measured,
            detail=(
                f"the switch node swings {span:.3f} V with {edges} transitions in the last 30 % of "
                "the run, so switching waveforms are produced"
            ),
        )
    return ProbeOutcome(
        status="unknown",
        measured=measured,
        detail=(
            f"the switch node swings {span:.3f} V with {edges} transitions; this probe does not "
            "establish switching behaviour"
        ),
    )


def _reverse_current_prebias(spec: ModelProbeSpec, ctx: RunContext, workdir: Path) -> ProbeOutcome:
    structural = _supports(spec, "reverse_current_prebias", ["vout"])
    if structural:
        return structural
    tstop = 6e-3 * spec.probe_time_scale
    prebias = 0.5
    deck = _application_deck(
        spec,
        title=f"{spec.model_id} pre-bias probe",
        tstop=tstop,
        tmax=2e-6,
        en_pulse=Source.pulse(
            "Ven",
            spec.net("en"),
            "0",
            v1=0.0,
            v2=3.3,
            delay_s=1e-3,
            width_s=tstop,
            period_s=2 * tstop,
        ),
        load=f"Rload {spec.net('vout')} 0 1e6",
        extra_elements=(
            f"Vpre n_pre 0 {prebias}",
            f"Rpre n_pre {spec.net('vout')} 0.1",
            f"Vrev {spec.net('vout')} n_rev 0",
        ),
        save=(f"V({spec.net('vout')})", "I(Vrev)"),
    )
    artifacts, _ = _run_probe(spec, ctx, workdir, deck, "reverse_current_prebias")
    if not artifacts.usability.usable or artifacts.raw is None:
        return ProbeOutcome(
            status="unknown",
            measured={"blocked_reason": artifacts.blocked_reason or "unusable run"},
            detail=f"the pre-bias probe could not be evaluated: {artifacts.detail}",
        )
    if not artifacts.raw.has("I(Vrev)"):
        return ProbeOutcome(
            status="not_tested",
            measured={},
            detail="the output branch current was not saved, so no pre-bias claim can be made",
        )
    time_axis = artifacts.raw.time_column()
    assert time_axis is not None
    vout = artifacts.raw.column(f"V({spec.net('vout')})")
    i_rev = artifacts.raw.column("I(Vrev)")
    pre = time_axis < 0.15 * tstop
    sink = float(np.min(i_rev[pre])) if np.any(pre) else 0.0
    start_v = float(np.mean(vout[pre])) if np.any(pre) else float("nan")
    measured = {"vout_prebias": start_v, "min_output_current_prebias_a": sink}
    if start_v > 0.2 and abs(sink) <= 1e-6:
        return ProbeOutcome(
            status="supported",
            measured=measured,
            detail=(
                f"starting from {start_v:.4f} V of pre-bias the output branch carried at most "
                f"{abs(sink) * 1e6:.2f} uA, so the model does not pull the output down"
            ),
        )
    return ProbeOutcome(
        status="unknown",
        measured=measured,
        detail=(
            f"starting from {start_v:.4f} V the model reached {sink * 1e6:.2f} uA of sink current; "
            "the pre-bias requirement is not established"
        ),
    )


def _declared(behavior: str, status: ProbeStatus, detail: str) -> ProbeFunction:
    def _probe(spec: ModelProbeSpec, ctx: RunContext, workdir: Path) -> ProbeOutcome:
        del spec, ctx, workdir
        return ProbeOutcome(status=status, measured={}, detail=detail, running=False)

    return _probe


def regulator_probes() -> list[BehaviorProbe]:
    """The probe set used for any regulator-family model."""
    return [
        BehaviorProbe("startup", "Rail start-up after enable", _startup),
        BehaviorProbe("shutdown", "Rail collapse after disable", _shutdown),
        BehaviorProbe("dc_regulation", "Steady-state regulation", _dc_regulation),
        BehaviorProbe("load_transients", "Load-step response", _load_transients),
        BehaviorProbe("input_current", "Steady-state input current", _input_current),
        BehaviorProbe(
            "current_limit_recovery", "Overload protection and recovery", _current_limit_recovery
        ),
        BehaviorProbe("switching_waveforms", "Switch-node behaviour", _switching_waveforms),
        BehaviorProbe(
            "compensation_loop",
            "Loop gain / phase margin",
            _declared(
                "compensation_loop",
                "not_tested",
                "loop-gain probing needs an AC (complex) analysis, which the native .raw reader "
                "does not support, so no loop claim is made",
            ),
        ),
        BehaviorProbe(
            "thermal_dependence",
            "Temperature dependence",
            _declared(
                "thermal_dependence",
                "unsupported",
                "no temperature dependence is modelled (documented exclusion); a temperature "
                "corner would be meaningless",
            ),
        ),
        BehaviorProbe(
            "reverse_current_prebias", "Pre-bias / reverse current", _reverse_current_prebias
        ),
    ]


def probe_model(
    spec: ModelProbeSpec,
    ctx: RunContext,
    *,
    workdir: Path,
    probes: Sequence[BehaviorProbe] | None = None,
    behaviors: Sequence[str] | None = None,
    evidence_level: EvidenceLevel = EvidenceLevel.VENDOR_MODEL_COMPARED,
    exclusions: Sequence[str] = (),
    cancel: threading.Event | None = None,
) -> CapabilityProbeReport:
    """Run every probe and assemble the capability record.

    A behaviour whose prerequisite failed is ``not_tested`` with the reason, and a
    probe that could not run in this environment (cancelled, simulator missing)
    stays ``not_tested`` — it is never recorded as ``supported``.
    """
    selected = list(probes or regulator_probes())
    if behaviors is not None:
        wanted = set(behaviors)
        selected = [probe for probe in selected if probe.behavior in wanted]

    outcomes: dict[str, ProbeOutcome] = {}
    results: list[TestResult] = []
    states: dict[str, ProbeStatus] = {key: "not_tested" for key in BEHAVIOR_KEYS}

    for probe in selected:
        if cancel is not None and cancel.is_set():
            outcomes[probe.behavior] = ProbeOutcome(
                status="not_tested", measured={}, detail="probing was cancelled", running=False
            )
            continue
        failed_requires = [
            key
            for key in probe.requires
            if outcomes.get(key, ProbeOutcome("not_tested", {}, "")).status != "supported"
        ]
        if failed_requires:
            outcomes[probe.behavior] = ProbeOutcome(
                status="not_tested",
                measured={"prerequisite_failed": ", ".join(failed_requires)},
                detail=(
                    "prerequisites "
                    + ", ".join(failed_requires)
                    + " were not established, so this behaviour was not probed"
                ),
                running=False,
            )
            continue
        try:
            outcomes[probe.behavior] = probe.run(spec, ctx, workdir)
        except Exception as exc:  # a probe that raises is not_tested, never supported
            outcomes[probe.behavior] = ProbeOutcome(
                status="not_tested",
                measured={"error": f"{type(exc).__name__}: {exc}"},
                detail=f"the probe raised {type(exc).__name__}: {exc}",
                running=False,
            )
        outcome = outcomes[probe.behavior]
        states[probe.behavior] = outcome.status
        results.append(
            TestResult(
                test_id=f"T_capability_{probe.behavior}",
                status=_status_for(outcome.status),
                requirement_ids=[],
                measured=dict(outcome.measured) or {"probe": probe.behavior},
                expected=probe.title,
                detail=outcome.detail,
                run_id=f"probe_{spec.model_id}_{probe.behavior}",
                unknown_reason=(
                    None if outcome.status == "supported" else f"capability_{outcome.status}"
                ),
                blocked_reason="simulator_unavailable"
                if "unusable run" in outcome.detail
                else None,
            )
        )

    capability = ModelCapability(
        model_id=spec.model_id,
        kind=spec.kind,
        behaviors=states,
        valid_domain={
            "vin": f"<= {spec.vin:g} V (probe condition)",
            "vout_nominal": f"{spec.nominal_vout:g} V",
            "temperature": "not modelled",
        },
        exclusions=list(exclusions),
        evidence_level=evidence_level,
        probe_results=[result.test_id for result in results],
        source_model_hash=sha256_file(spec.path) if spec.path.is_file() else "0" * 64,
    )
    return CapabilityProbeReport(capability=capability, results=results, outcomes=outcomes)


def _status_for(probe_status: ProbeStatus) -> Status:
    if probe_status == "supported":
        return Status.PASS
    if probe_status == "unsupported":
        return Status.NOT_APPLICABLE
    return Status.UNKNOWN
