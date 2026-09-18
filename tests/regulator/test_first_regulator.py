"""First regulator against the frozen requirement set (Phase 2 step 8).

Two model families are involved, and the tests say which is which:

* the **generated type-B template** (`BM_REG_BUCK`) carries the verified
  startup / enable / power-good / current-limit behaviour. It is deterministic and
  fast, so it is what these tests exercise; its capability record is probed and
  exported like any other model.
* the **ported TI vendor model** (type A) is exercised by
  ``tests/regulator/test_vendor_*.py``, which is opt-in because that
  transistor-level model needs tens of seconds of wall time per simulated
  millisecond and can exceed any sane timeout on a longer window (observed: a
  2.1 ms application run hit the 600 s cap). Its capability record says exactly
  that — 4 behaviours probed as supported, the rest unknown/not_tested.

The decisive requirement in both cases is the same: **regulation must follow the
external feedback divider**, so a broken divider is detected instead of being
masked by a fixed internal output.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from boardmodeler.domain.enums import EvidenceLevel, ModelKind, Status
from boardmodeler.domain.records import (
    BEHAVIOR_KEYS,
    ExpectationSpec,
    ModelCapability,
    Requirement,
    TestCase,
)
from boardmodeler.models.regulator import write_regulator_library
from boardmodeler.pipeline.runner import RunContext, run_case
from boardmodeler.simulation.deck import DeckSpec, Include, Source, TranSpec, write_deck
from boardmodeler.verification.engine import evaluate_case, gate_from_capability

pytestmark = pytest.mark.ltspice

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "regulator" / "tps54320"
REQUIREMENTS_FILE = FIXTURE / "requirements.json"
CAPABILITY_FILE = FIXTURE / "capability_vendor.json"

VREF = 0.8
RFBB = 3.24e3
RFBT_NOMINAL = 10e3
TSTOP = 6e-3
TMAX = 2e-6
LOAD = 10.0


def load_requirements() -> dict[str, Requirement]:
    raw = json.loads(REQUIREMENTS_FILE.read_text(encoding="utf-8"))
    return {item["req_id"]: Requirement.model_validate(item) for item in raw["requirements"]}


def capability() -> ModelCapability | None:
    if not CAPABILITY_FILE.is_file():
        return None
    raw = json.loads(CAPABILITY_FILE.read_text(encoding="utf-8"))
    return ModelCapability.model_validate(raw["model"])


def buck_deck(run_dir: Path, *, rfbt: float, rfbb: float = RFBB, params: dict | None = None) -> Path:
    """Application deck around the generated buck template."""
    library = run_dir.parent / "board.lib"
    if not library.is_file():
        write_regulator_library(library, ["BM_REG_BUCK"])
    overrides = {"VREF": VREF, "CSS": "1n", "ILIM": "3.0", "ILIM_MODE": "0"}
    overrides.update(params or {})
    params_text = " ".join(f"{key}={value}" for key, value in sorted(overrides.items()))
    deck = DeckSpec(
        title=f"generated buck, Rfbt={rfbt:g}",
        includes=(Include(path=str(library.resolve())),),
        sources=(
            Source.ramp("V1", "VIN", "0", v0=0.0, v1=12.0, rise_s=200e-6, hold_s=TSTOP),
            Source.pulse(
                "Ven",
                "EN",
                "0",
                v1=0.0,
                v2=3.3,
                delay_s=0.3e-3,
                width_s=TSTOP,
                period_s=2 * TSTOP,
            ),
        ),
        elements=(
            f"XU1 VIN EN FB PG VOUT 0 SW 0 BM_REG_BUCK {params_text}",
            f"Rload VOUT 0 {LOAD:g}",
            f"Rfbt VOUT FB {rfbt:g}",
            f"Rfbb FB 0 {rfbb:g}",
        ),
        tran=TranSpec(tstep=TSTOP / 20000.0, tstop=TSTOP, tstart=0.0, tmax=TMAX),
        save=("V(VOUT)", "V(FB)", "V(PG)", "V(EN)", "V(SW)"),
        options={"method": "gear", "trtol": 7},
    )
    return write_deck(deck, run_dir / "app.cir")


def case(name: str, requirement_ids: list[str], *, kind: str = "satisfy") -> TestCase:
    return TestCase(
        test_id=f"T_{name}",
        requirement_ids=requirement_ids,
        scenario_id="nominal_startup",
        scope="circuit_compliance",
        deck_template="app.cir",
        expected=ExpectationSpec(kind=kind, detail=f"{name} against the frozen requirement"),
        measurement=["V(VOUT)", "V(FB)", "V(PG)"],
    )


def regulation_requirement(*, low: float | None = None, high: float | None = None) -> Requirement:
    base = load_requirements()["REQ_TPS54320_ELEC_020"]
    target = VREF * (1 + RFBT_NOMINAL / RFBB)
    return base.model_copy(
        update={
            "statement": "V(VOUT) regulates to Vref * (1 + Rfbt/Rfbb) through the external divider.",
            "expression": {
                "op": "between",
                "signal": "V(VOUT)",
                "low": low if low is not None else target * 0.97,
                "high": high if high is not None else target * 1.03,
                "unit": "V",
                "interval": {"start_s": 0.8 * TSTOP, "end_s": TSTOP},
            },
            "signal_refs": ["V(VOUT)", "V(FB)"],
        }
    )


def run(tmp_path: Path, *, rfbt: float, name: str, params: dict | None = None):
    from boardmodeler.simulation.ltspice import locate

    install = locate()
    if install is None:
        pytest.skip("LTspice is not installed")
    ctx = RunContext(project_dir=tmp_path, ltspice=install.path, timeout_s=240)
    test_case = case(name, ["REQ_TPS54320_ELEC_020"])
    artifacts = run_case(
        ctx,
        test_case,
        build_deck=lambda run_dir: buck_deck(run_dir, rfbt=rfbt, params=params),
        run_identifier=name,
    )
    return artifacts, test_case


@pytest.fixture(scope="module")
def nominal(tmp_path_factory: pytest.TempPathFactory):
    return run(tmp_path_factory.mktemp("nominal"), rfbt=RFBT_NOMINAL, name="nominal")


@pytest.fixture(scope="module")
def broken(tmp_path_factory: pytest.TempPathFactory):
    return run(tmp_path_factory.mktemp("broken"), rfbt=30e3, name="broken")


def test_nominal_divider_regulates_at_the_target(nominal) -> None:
    artifacts, test_case = nominal
    assert artifacts.usability.usable, artifacts.detail
    result = evaluate_case(test_case, artifacts, {regulation_requirement().req_id: regulation_requirement()})
    assert result.status is Status.PASS, result.detail
    assert result.measured["min(V(VOUT))"] == pytest.approx(VREF * (1 + RFBT_NOMINAL / RFBB), rel=0.03)


def test_broken_feedback_divider_is_detected(broken, nominal) -> None:
    """The output must follow the external divider, not an internal fixed value."""
    artifacts, test_case = broken
    assert artifacts.usability.usable, artifacts.detail
    assert artifacts.raw is not None
    requirement = regulation_requirement()
    result = evaluate_case(test_case, artifacts, {requirement.req_id: requirement})
    assert result.status is Status.FAIL, result.detail

    time_axis = artifacts.raw.time_column()
    assert time_axis is not None
    settled = float(np.mean(artifacts.raw.column("V(VOUT)")[time_axis > 0.85 * TSTOP]))
    expected_broken = VREF * (1 + 30e3 / RFBB)
    assert settled == pytest.approx(expected_broken, rel=0.10), (
        f"with Rfbt=30k the output settled at {settled:.3f} V; expected about "
        f"{expected_broken:.3f} V — a fixed internal output would hide this fault"
    )
    # The feedback node still sits at the reference: the divider sets the output.
    vfb = float(np.mean(artifacts.raw.column("V(FB)")[time_axis > 0.85 * TSTOP]))
    assert vfb == pytest.approx(VREF, rel=0.05)


def test_enable_threshold_and_rail_startup(nominal) -> None:
    artifacts, test_case = nominal
    assert artifacts.raw is not None
    time_axis = artifacts.raw.time_column()
    assert time_axis is not None
    vout = artifacts.raw.column("V(VOUT)")
    assert float(np.max(np.abs(vout[time_axis < 0.25e-3]))) < 0.1, "the rail moved before enable"

    enable = load_requirements()["REQ_TPS54320_ELEC_010"]
    assertion = enable.model_copy(
        update={
            "expression": {
                "op": "state_dependent",
                "when": {"signal": "V(EN)", "kind": "rise_above", "value": 1.21, "unit": "V"},
                "then": {"op": "rise_above", "signal": "V(VOUT)", "value": 3.0, "unit": "V"},
            }
        }
    )
    result = evaluate_case(test_case, artifacts, {assertion.req_id: assertion})
    assert result.status is Status.PASS, result.detail


def test_power_good_follows_the_rail(nominal) -> None:
    artifacts, test_case = nominal
    assert artifacts.raw is not None
    time_axis = artifacts.raw.time_column()
    assert time_axis is not None
    pg = artifacts.raw.column("V(PG)")
    low = float(np.mean(pg[time_axis < 0.3e-3]))
    high = float(np.mean(pg[time_axis > 0.9 * TSTOP]))
    assert low < 0.4, f"power-good asserted before the rail was valid ({low:.3f} V)"
    assert high > 2.0, f"power-good never released ({high:.3f} V)"

    pg_requirement = load_requirements()["REQ_TPS54320_PG_050"].model_copy(
        update={"expression": {"op": "rise_above", "signal": "V(PG)", "value": 2.0, "unit": "V"}}
    )
    result = evaluate_case(test_case, artifacts, {pg_requirement.req_id: pg_requirement})
    assert result.status is Status.PASS, result.detail


def test_current_limit_engages_and_recovers(tmp_path: Path) -> None:
    """With ILIM=1.0 A and a 3 A load the template must limit and then recover."""
    from boardmodeler.simulation.ltspice import locate

    install = locate()
    if install is None:
        pytest.skip("LTspice is not installed")
    overrides = {"ILIM": "1.0", "ILIM_MODE": "0"}
    ctx = RunContext(project_dir=tmp_path, ltspice=install.path, timeout_s=240)
    test_case = case("current_limit", ["REQ_TPS54320_ELEC_030"], kind="violate_detected")

    def build(run_dir: Path) -> Path:
        deck = buck_deck(run_dir, rfbt=RFBT_NOMINAL, params=overrides)
        text = deck.read_text(encoding="utf-8")
        # Overload from 2 ms to 4 ms by shrinking the load resistor.
        text += f"\nRov VOUT 0 {LOAD / 6:g}\nSov VOUT nov Vctl 0 SW_OV\n.ic V(nov)=0\n"
        text = text.replace(
            "Rload VOUT 0 10",
            "Rload VOUT 0 10\nVctl Vctl 0 PWL(0 0 1.999m 0 2m 1 4m 1 4.001m 0 6m 0)\n"
            ".model SW_OV SW(Ron=1m Roff=1G Vt=0.5 Vh=0.1)\nRovmid nov VOUT 1m",
        )
        deck.write_text(text, encoding="utf-8")
        return deck

    artifacts = run_case(ctx, test_case, build_deck=build, run_identifier="ol")
    assert artifacts.usability.usable, artifacts.detail
    assert artifacts.raw is not None
    time_axis = artifacts.raw.time_column()
    assert time_axis is not None
    vout = artifacts.raw.column("V(VOUT)")
    normal = float(np.mean(vout[(time_axis > 1.5e-3) & (time_axis < 1.9e-3)]))
    loaded = float(np.mean(vout[(time_axis > 2.5e-3) & (time_axis < 4e-3)]))
    recovered = float(np.mean(vout[time_axis > 5.2e-3]))
    assert normal > 2.0, f"the rail never came up ({normal:.3f} V)"
    assert loaded < 0.8 * normal, (
        f"the output did not collapse under overload (normal {normal:.3f} V, loaded {loaded:.3f} V) — "
        "protection is not engaging"
    )
    assert recovered > 0.9 * normal, f"the rail did not recover ({recovered:.3f} V)"


def test_capability_gate_blocks_unprobed_behaviours(nominal) -> None:
    """A requirement needs a probed *supported* behaviour; unknown/not_tested is not a licence."""
    artifacts, test_case = nominal
    requirement = regulation_requirement()
    states = {key: "supported" for key in BEHAVIOR_KEYS}
    model_capability = ModelCapability(
        model_id="bm_reg_buck",
        kind=ModelKind.REDUCED_BEHAVIORAL,
        behaviors=states,
        evidence_level=EvidenceLevel.SYNTHETIC_ANALYTICAL,
        source_model_hash="c" * 64,
    )
    open_gate = gate_from_capability([model_capability], {requirement.req_id: "startup"})
    assert open_gate == {}
    result = evaluate_case(
        test_case, artifacts, {requirement.req_id: requirement}, capability_gate=open_gate
    )
    assert result.status is Status.PASS, result.detail

    states["load_transients"] = "not_tested"
    gated_capability = model_capability.model_copy(update={"behaviors": states})
    closed_gate = gate_from_capability([gated_capability], {requirement.req_id: "load_transients"})
    assert requirement.req_id in closed_gate
    blocked = evaluate_case(
        test_case, artifacts, {requirement.req_id: requirement}, capability_gate=closed_gate
    )
    assert blocked.status is Status.UNKNOWN
    assert blocked.unknown_reason == "model_capability_unsupported"
    assert "load_transients" in blocked.detail


def test_vendor_capability_record_is_honest_about_what_it_could_not_probe() -> None:
    """The committed vendor capability record must not claim unprobed behaviour."""
    record = capability()
    if record is None:
        pytest.skip("no vendor capability record yet")
    assert record.evidence_level.value == "VENDOR_MODEL_COMPARED"
    assert record.behaviors["thermal_dependence"] == "unsupported"
    assert record.behaviors["compensation_loop"] == "not_tested"
    # Every non-supported state must be blocking for dependent requirements.
    gate = gate_from_capability(
        [record],
        {"REQ_X": "compensation_loop", "REQ_Y": "thermal_dependence"},
    )
    assert set(gate) == {"REQ_X", "REQ_Y"}
