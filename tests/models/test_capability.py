"""Capability-probe framework tests (Phase 2 step 4).

These use a tiny synthetic model so the *framework* is verified quickly and
independently of the slow vendor runs: probe wiring, prerequisite handling,
status mapping, the gate, and the "never upgrade to supported" rule.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from boardmodeler.domain.enums import EvidenceLevel, ModelKind
from boardmodeler.domain.records import BEHAVIOR_KEYS, ModelCapability
from boardmodeler.models.capability import (
    BehaviorProbe,
    ModelProbeSpec,
    ProbeOutcome,
    behavior_gate,
    probe_model,
    regulator_probes,
)
from boardmodeler.pipeline.runner import RunContext

pytestmark = pytest.mark.ltspice

#: A model that drives VOUT to the divider target immediately: enough to exercise
#: the startup and regulation probes.
FAKE_MODEL = """* synthetic regulator-shaped model for probe-framework tests
.subckt FAKE_REG VIN EN FB PG VOUT GND SW ILIM_MODE
Rleak VIN GND 1G
Eout VOUT GND VALUE={ 0.8*(1+10k/3.24k) }
Rpg PG GND 1G
Rsw SW GND 1G
.ends FAKE_REG
"""


def make_spec(path: Path, **overrides: object) -> ModelProbeSpec:
    defaults: dict[str, object] = {
        "model_id": "fake_reg",
        "kind": ModelKind.REDUCED_BEHAVIORAL,
        "path": path,
        "subckt": "FAKE_REG",
        "ports": ("VIN", "EN", "FB", "PG", "VOUT", "GND", "SW", "ILIM_MODE"),
        "port_roles": {
            "VIN": "vin",
            "EN": "en",
            "FB": "fb",
            "PG": "pg",
            "VOUT": "vout",
            "GND": "gnd",
            "SW": "sw",
            "ILIM_MODE": "ilim_mode",
        },
        "nets": {"gnd": "0", "vout": "n_vout", "vin": "n_vin", "sw": "n_sw"},
        "probe_time_scale": 0.25,
    }
    defaults.update(overrides)
    return ModelProbeSpec(**defaults)  # type: ignore[arg-type]


def test_instance_nodes_follow_the_model_port_order(tmp_path: Path) -> None:
    path = tmp_path / "fake.lib"
    path.write_text(FAKE_MODEL, encoding="utf-8")
    spec = make_spec(path)
    nodes = spec.instance_nodes()
    assert len(nodes) == len(spec.ports)
    assert nodes[0] == spec.net("vin") and nodes[4] == spec.net("vout")


def test_unmapped_port_is_refused_not_guessed(tmp_path: Path) -> None:
    path = tmp_path / "fake.lib"
    path.write_text(FAKE_MODEL, encoding="utf-8")
    spec = make_spec(path, port_roles={"VIN": "vin", "EN": "en"})
    assert spec.unmapped_ports() == ["FB", "PG", "VOUT", "GND", "SW", "ILIM_MODE"]
    with pytest.raises(ValueError, match="have no role"):
        spec.instance_nodes()


def _probe(spec: ModelProbeSpec, ctx: RunContext, workdir: Path) -> ProbeOutcome:
    del workdir
    # A trivial probe used to test prerequisite handling and status mapping.
    return ProbeOutcome(status="supported", measured={"value": 1.0}, detail="synthetic probe ran")


def _failing_probe(spec: ModelProbeSpec, ctx: RunContext, workdir: Path) -> ProbeOutcome:
    del workdir
    return ProbeOutcome(status="unknown", measured={"observed": 0.0}, detail="synthetic miss")


def _raising_probe(spec: ModelProbeSpec, ctx: RunContext, workdir: Path) -> ProbeOutcome:
    del spec, ctx, workdir
    raise RuntimeError("deck could not be built")


def test_probe_report_covers_every_behavior_key(tmp_path: Path, ltspice_exe: Path) -> None:
    path = tmp_path / "fake.lib"
    path.write_text(FAKE_MODEL, encoding="utf-8")
    spec = make_spec(path)
    ctx = RunContext(project_dir=tmp_path, ltspice=ltspice_exe, timeout_s=60)

    probes = [
        BehaviorProbe("startup", "synthetic startup", _probe),
        BehaviorProbe("shutdown", "synthetic shutdown", _failing_probe),
        BehaviorProbe("dc_regulation", "synthetic regulation", _raising_probe),
        BehaviorProbe("input_current", "needs a supported startup", _probe, requires=("startup",)),
        BehaviorProbe(
            "load_transients", "blocked by a failed prerequisite", _probe, requires=("shutdown",)
        ),
    ]
    report = probe_model(spec, ctx, workdir=tmp_path / "probes", probes=probes)

    # Every key is present, and the states are exactly what the probes reported.
    assert set(report.capability.behaviors) == set(BEHAVIOR_KEYS)
    assert report.capability.behaviors["startup"] == "supported"
    assert report.capability.behaviors["shutdown"] == "unknown"
    assert report.capability.behaviors["dc_regulation"] == "not_tested"  # the probe raised
    assert report.capability.behaviors["input_current"] == "supported"
    assert report.capability.behaviors["load_transients"] == "not_tested"
    # A behaviour with no probe in this run stays not_tested, never supported.
    assert report.capability.behaviors["thermal_dependence"] == "not_tested"
    assert report.capability.evidence_level is EvidenceLevel.VENDOR_MODEL_COMPARED
    assert report.capability.source_model_hash != "0" * 64
    assert "synthetic" in report.outcomes["startup"].detail


def test_probe_result_records_the_observed_measurement(tmp_path: Path, ltspice_exe: Path) -> None:
    path = tmp_path / "fake.lib"
    path.write_text(FAKE_MODEL, encoding="utf-8")
    spec = make_spec(path)
    ctx = RunContext(project_dir=tmp_path, ltspice=ltspice_exe, timeout_s=60)
    report = probe_model(
        spec,
        ctx,
        workdir=tmp_path / "probes",
        probes=[BehaviorProbe("startup", "synthetic miss", _failing_probe)],
    )
    result = report.results[0]
    assert result.status.value == "UNKNOWN"
    assert result.measured["observed"] == 0.0
    assert result.unknown_reason == "capability_unknown"
    assert "synthetic miss" in result.detail


def test_raising_and_cancelled_probes_never_report_supported(
    tmp_path: Path, ltspice_exe: Path
) -> None:
    import threading

    path = tmp_path / "fake.lib"
    path.write_text(FAKE_MODEL, encoding="utf-8")
    spec = make_spec(path)
    ctx = RunContext(project_dir=tmp_path, ltspice=ltspice_exe, timeout_s=60)
    cancel = threading.Event()
    cancel.set()
    report = probe_model(
        spec,
        ctx,
        workdir=tmp_path / "probes",
        probes=[BehaviorProbe("startup", "synthetic", _probe)],
        cancel=cancel,
    )
    assert report.capability.behaviors["startup"] == "not_tested"
    assert "cancelled" in report.outcomes["startup"].detail


def test_gate_blocks_every_non_supported_state() -> None:
    states = {key: "not_tested" for key in BEHAVIOR_KEYS}
    states["startup"] = "supported"
    states["shutdown"] = "unknown"
    states["load_transients"] = "unsupported"
    capability = ModelCapability(
        model_id="m1",
        kind=ModelKind.REDUCED_BEHAVIORAL,
        behaviors=states,
        evidence_level=EvidenceLevel.SYNTHETIC_ANALYTICAL,
        source_model_hash="a" * 64,
    )
    gate = behavior_gate(
        capability,
        {
            "REQ_A": "startup",
            "REQ_B": "shutdown",
            "REQ_C": "load_transients",
            "REQ_D": "thermal_dependence",
        },
    )
    assert "REQ_A" not in gate
    assert set(gate) == {"REQ_B", "REQ_C", "REQ_D"}
    assert "unknown" in gate["REQ_B"] and "m1" in gate["REQ_B"]


def test_regulator_probe_set_covers_the_probeable_behaviors() -> None:
    behaviors = {probe.behavior for probe in regulator_probes()}
    assert behaviors == set(BEHAVIOR_KEYS)
    for probe in regulator_probes():
        assert probe.title.strip()
