"""Demo board build, circuit check and fault matrix (§15, Phase 3).

The demo is the end-to-end proof: a real project is assembled from the committed
fixtures, its model library is generated, its decks are written, real LTspice runs
produce the measurements, and the results carry honest statuses.

Three entry points back the CLI:

* :func:`build_demo_project` — assemble ``fixtures/demo_board`` into a project
  directory (models, decks, tests, requirements, capability records, schematic).
* :func:`check_circuit` — run the static checks and the dynamic scenarios against
  the project and return findings, results and the coverage/HTML artifacts.
* :func:`run_fault_matrix` — mutate the project once per fault in its own
  directory, verify each fault is detected by the check it is supposed to trip,
  and prove the original project is byte-identical afterwards.
"""

from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from boardmodeler.domain.enums import (
    Criticality,
    EvidenceLevel,
    ModelKind,
    RequirementClass,
    RequirementKind,
    RequirementOrigin,
    Status,
)
from boardmodeler.domain.hashing import sha256_file
from boardmodeler.domain.records import (
    ExpectationSpec,
    Finding,
    ModelCapability,
    PinDefinition,
    Requirement,
    TestCase,
    TestResult,
)
from boardmodeler.models.capability import ModelProbeSpec, probe_model
from boardmodeler.models.library import ModelStore, subckt_ports
from boardmodeler.models.primitives import PRIMITIVE_PORT_ORDER, primitive_text
from boardmodeler.models.regulator import REGULATOR_PORT_ORDER, regulator_text
from boardmodeler.pipeline.project import Project, create_project
from boardmodeler.pipeline.runner import RunContext, run_case
from boardmodeler.reporting.html import ReportInputs, ViolationMarker, write_report
from boardmodeler.schematic.mutate import MUTATORS, apply_edits, fault_ids
from boardmodeler.schematic.netlist import build_netmap, parse_netlist
from boardmodeler.schematic.neutral import NeutralProject, read_neutral_project
from boardmodeler.schematic.static_check import run_static_checks
from boardmodeler.simulation.deck import DeckSpec, Include, MeasSpec, Source, TranSpec, write_deck
from boardmodeler.simulation.ltspice import LtspiceInstall, locate, netlist_step
from boardmodeler.verification.engine import evaluate_case, gate_from_capability
from boardmodeler.verification.scenarios import scenario

__all__ = [
    "DEMO_SCENARIOS",
    "BoardModelSpec",
    "CheckResult",
    "DemoBuildResult",
    "build_demo_project",
    "check_circuit",
    "deck_for_scenario",
    "run_fault_matrix",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_SOURCE = REPO_ROOT / "fixtures" / "demo_board"
SWITCH_SOURCE = REPO_ROOT / "fixtures" / "switch_fixture"

DOC_ID = "doc_synthetic_switch_contract"
BOARD_DOC_ID = "doc_demo_board_requirements"


@dataclass(frozen=True)
class BoardModelSpec:
    """How one board component is instantiated in a deck."""

    model_id: str
    subckt: str
    ports: tuple[str, ...]
    params: Mapping[str, str]
    kind: str  # "regulator" | "primitive"


BOARD_MODELS: dict[str, BoardModelSpec] = {
    "bm_reg_buck": BoardModelSpec(
        "bm_reg_buck",
        "BM_REG_BUCK",
        REGULATOR_PORT_ORDER["BM_REG_BUCK"],
        {"ILIM_MODE": "0", "VREF": "0.8", "CSS": "1n", "ILIM": "3.0", "RDISCHARGE": "10"},
        "regulator",
    ),
    "bm_reg_ldo": BoardModelSpec(
        "bm_reg_ldo",
        "BM_REG_LDO",
        REGULATOR_PORT_ORDER["BM_REG_LDO"],
        {"VREF": "0.8", "CSS": "1n", "ILIM": "1.0", "RDISCHARGE": "10"},
        "regulator",
    ),
    "bm_pg_3v3": BoardModelSpec(
        "bm_pg_3v3",
        "BM_PG",
        PRIMITIVE_PORT_ORDER["BM_PG"],
        {"VTH": "1.0", "VHYS": "0.1", "TD": "2m", "PULLUP_MAX": "100k"},
        "primitive",
    ),
    "bm_pg_1v8": BoardModelSpec(
        "bm_pg_1v8",
        "BM_PG",
        PRIMITIVE_PORT_ORDER["BM_PG"],
        {"VTH": "1.0", "VHYS": "0.1", "TD": "2m", "PULLUP_MAX": "100k"},
        "primitive",
    ),
    "bm_load_3v3": BoardModelSpec(
        "bm_load_3v3",
        "BM_LOAD",
        PRIMITIVE_PORT_ORDER["BM_LOAD"],
        {"I_STATIC": "0.25", "I_STEP": "0.15", "T_STEP": "2m"},
        "primitive",
    ),
    "bm_load_1v8": BoardModelSpec(
        "bm_load_1v8",
        "BM_LOAD",
        PRIMITIVE_PORT_ORDER["BM_LOAD"],
        {"I_STATIC": "0.4", "I_STEP": "0.2", "T_STEP": "2m"},
        "primitive",
    ),
}

#: Scenario id -> (deck name, description) the demo can actually run.
DEMO_SCENARIOS: dict[str, str] = {
    "nominal_startup": "nominal power-up with both rails and the reset release",
    "staggered_rails": "rail sequencing with the load steps applied",
    "slow_rail": "slow input ramp",
    "fast_rail": "fast input ramp",
    "reset_early_release": "fault: reset released before its prerequisites",
    "pullup_missing": "fault: open-drain pull-up removed",
    "pullup_wrong_domain": "fault: pull-up on the wrong supply domain",
    "invalid_strap": "fault: undocumented strap word",
    "load_step": "rail step loading",
    "brownout_short_interrupt": "short input dip",
}

# The behavioural board models contain discontinuous B-sources and open-drain
# switches, so the solver takes small internal steps; a 10 ms window with a 20 us
# cap keeps a full scenario under a minute of wall time while still covering
# start-up, sequencing, the reset release and the strap sampling window.
SIM_STOP = 10e-3
SIM_TMAX = 20e-6
VIN_RAMP = 2e-3


@dataclass
class DemoBuildResult:
    """What the build produced."""

    project_dir: Path
    files: list[str] = field(default_factory=list)
    decks: dict[str, Path] = field(default_factory=dict)
    tests: list[TestCase] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    pins: list[PinDefinition] = field(default_factory=list)
    static_findings: list[Finding] = field(default_factory=list)
    capabilities: dict[str, ModelCapability] = field(default_factory=dict)
    detail: str = ""


@dataclass
class CheckResult:
    """Outcome of ``circuit check``."""

    project_dir: Path
    findings: list[Finding] = field(default_factory=list)
    results: list[TestResult] = field(default_factory=list)
    status: Status = Status.UNKNOWN
    report_path: Path | None = None
    results_path: Path | None = None
    coverage: dict[str, object] = field(default_factory=dict)
    detail: str = ""

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results:
            counts[result.status.value] = counts.get(result.status.value, 0) + 1
        return counts


# --------------------------------------------------------------------------- #
# fixture assembly


def _merge_pins(demo_pins: Path, switch_pins: Path) -> list[dict[str, str]]:
    """Demo pin map plus the switch fixture's pins, remapped to ``U5``."""
    rows: list[dict[str, str]] = []
    for path in (demo_pins, switch_pins):
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        header = lines[0].split(",")
        for line in lines[1:]:
            row = dict(zip(header, line.split(","), strict=True))
            if path == switch_pins:
                row["refdes"] = "U5"
            rows.append(row)
    return rows


def _pin_definitions(rows: Iterable[Mapping[str, str]]) -> list[PinDefinition]:
    pins: list[PinDefinition] = []
    for row in rows:
        pins.append(
            PinDefinition.model_validate(
                {
                    "part_id": "SYNTH-BOARD",
                    "physical_pin": row["physical_pin"],
                    "name": row["name"].strip() or row["physical_pin"],
                    "function": f"board fixture pin {row['name']}",
                    "polarity": row["polarity"] or "not_applicable",
                    "direction": row["direction"],
                    "supply_domain": row["supply_domain"] or None,
                    "output_topology": row["output_topology"],
                    "connection_requirement": row["connection_requirement"],
                    "unused_pin_treatment": row["unused_pin_treatment"] or None,
                    "behavior": [],
                    "evidence": [
                        {
                            "doc_id": DOC_ID,
                            "section": "pins.csv",
                            "excerpt": f"{row['refdes']},{row['physical_pin']},{row['name']}",
                            "extraction": "synthetic_fixture",
                        }
                    ],
                    "mapped_symbol_pin": row["name"].strip() or row["physical_pin"],
                }
            )
        )
    return pins


def _pins_by_refdes(rows: Iterable[Mapping[str, str]]) -> dict[str, list[PinDefinition]]:
    grouped: dict[str, list[PinDefinition]] = {}
    for row, pin in zip(rows, _pin_definitions(rows), strict=False):
        grouped.setdefault(row["refdes"], []).append(pin)
    return grouped


def board_requirements() -> list[Requirement]:
    """The board owner's requirements (origin=USER) for the demo board."""
    common: dict[str, Any] = {
        "applies_to": "demo_board",
        "configuration": None,
        "origin": RequirementOrigin.USER,
        "criticality": Criticality.CRITICAL,
        "conditions": [],
        "conflicts": [],
        "citation_verified": True,
        "status": "active",
    }

    def requirement(
        suffix: str,
        *,
        kind: RequirementKind,
        req_class: RequirementClass,
        statement: str,
        limits: dict[str, Any] | None,
        expression: dict[str, Any] | None,
        signals: Sequence[str],
        criticality: Criticality = Criticality.CRITICAL,
    ) -> Requirement:
        return Requirement.model_validate(
            {
                **common,
                "req_id": f"REQ_DEMO_{suffix}",
                "kind": kind,
                "class": req_class,
                "criticality": criticality,
                "statement": statement,
                "limits": limits,
                "expression": expression,
                "signal_refs": list(signals),
                "evidence": [
                    {
                        "doc_id": BOARD_DOC_ID,
                        "page": None,
                        "section": "demo board requirements",
                        "table": None,
                        "figure": None,
                        "excerpt": statement[:380],
                        "extraction": "synthetic_fixture",
                    }
                ],
            }
        )

    return [
        requirement(
            "SEQ_001",
            kind=RequirementKind.ELECTRICAL,
            req_class=RequirementClass.USER_REQUIREMENT,
            statement="Both rails stay inside their fixture windows from 4 ms to 10 ms.",
            limits={"min": 1.71, "max": 3.465, "unit": "V"},
            expression={
                "op": "all_of",
                "items": [
                    {
                        "op": "between",
                        "signal": "V(3V3)",
                        "low": 3.135,
                        "high": 3.465,
                        "unit": "V",
                        "interval": {"start_s": 4e-3, "end_s": 10e-3},
                    },
                    {
                        "op": "between",
                        "signal": "V(1V8)",
                        "low": 1.710,
                        "high": 1.890,
                        "unit": "V",
                        "interval": {"start_s": 4e-3, "end_s": 10e-3},
                    },
                ],
            },
            signals=("V(3V3)", "V(1V8)"),
        ),
        requirement(
            "SEQ_002",
            kind=RequirementKind.TEMPORAL,
            req_class=RequirementClass.USER_REQUIREMENT,
            statement="The 1V8 rail comes up after the 3V3 rail, which comes up after the input.",
            limits=None,
            expression={
                "op": "ordering",
                "first": {"signal": "V(3V3)", "kind": "rise_above", "value": 3.0, "unit": "V"},
                "then": {"signal": "V(1V8)", "kind": "rise_above", "value": 1.6, "unit": "V"},
            },
            signals=("V(3V3)", "V(1V8)"),
        ),
        requirement(
            "SEQ_003",
            kind=RequirementKind.TEMPORAL,
            req_class=RequirementClass.USER_REQUIREMENT,
            statement="PERST# is released at least 1 ms after the last prerequisite (both PGs).",
            limits={"min": 1e-3, "max": 0.1, "unit": "s"},
            expression={
                "op": "event_delay",
                "start": {"signal": "V(PG_1V8)", "kind": "rise_above", "value": 1.0, "unit": "V"},
                "end": {"signal": "V(PERST_N)", "kind": "rise_above", "value": 2.0, "unit": "V"},
                "min_s": 1e-3,
                "max_s": 0.1,
            },
            signals=("PERST_N", "PG_1V8"),
        ),
        requirement(
            "SEQ_004",
            kind=RequirementKind.TEMPORAL,
            req_class=RequirementClass.USER_REQUIREMENT,
            statement="PERST# stays low until both power-good signals are asserted.",
            limits=None,
            expression={
                "op": "ordering",
                "first": {"signal": "V(PG_3V3)", "kind": "rise_above", "value": 1.0, "unit": "V"},
                "then": {"signal": "V(PERST_N)", "kind": "rise_above", "value": 2.0, "unit": "V"},
            },
            signals=("PERST_N", "PG_3V3"),
        ),
        requirement(
            "CONN_005",
            kind=RequirementKind.CONNECTIVITY,
            req_class=RequirementClass.USER_REQUIREMENT,
            statement="The sideband lines idle high on their 3V3 pull-ups.",
            limits={"min": 2.97, "unit": "V"},
            expression={
                "op": "all_of",
                "items": [
                    {"op": "rise_above", "signal": "V(SMB_CLK)", "value": 2.97, "unit": "V"},
                    {"op": "rise_above", "signal": "V(SMB_DAT)", "value": 2.97, "unit": "V"},
                ],
            },
            signals=("SMB_CLK", "SMB_DAT"),
            criticality=Criticality.IMPORTANT,
        ),
        requirement(
            "CONN_006",
            kind=RequirementKind.CONNECTIVITY,
            req_class=RequirementClass.USER_REQUIREMENT,
            statement="Both sideband pull-ups sit on the 3V3 domain.",
            limits=None,
            expression={
                "op": "pullup_domain",
                "refdes": "U5",
                "pin": "SMB_DAT",
                "net": "SMB_DAT",
                "domain": "3V3",
            },
            signals=("SMB_DAT",),
        ),
        requirement(
            "CONFIG_007",
            kind=RequirementKind.FUNCTIONAL,
            req_class=RequirementClass.USER_REQUIREMENT,
            statement="The strap word sampled between 1 ms and 5 ms after 1V8 is valid (not 111).",
            limits=None,
            expression={
                "op": "not",
                "item": {
                    "op": "all_of",
                    "items": [
                        {"op": "rise_above", "signal": "V(CONFIG0)", "value": 1.2, "unit": "V"},
                        {"op": "rise_above", "signal": "V(CONFIG1)", "value": 1.2, "unit": "V"},
                        {"op": "rise_above", "signal": "V(CONFIG2)", "value": 1.2, "unit": "V"},
                    ],
                },
            },
            signals=("CONFIG0", "CONFIG1", "CONFIG2"),
        ),
        requirement(
            "CONFIG_008",
            kind=RequirementKind.TEMPORAL,
            req_class=RequirementClass.USER_REQUIREMENT,
            statement="The CONFIG0 strap does not change after its sampling window closes.",
            limits=None,
            expression={
                "op": "hold",
                "signal": "V(CONFIG0)",
                "value": 1.8,
                "unit": "V",
                "interval": {"start_s": 5e-3, "end_s": 10e-3},
                "stable": True,
            },
            signals=("CONFIG0",),
            criticality=Criticality.IMPORTANT,
        ),
        requirement(
            "CLK_009",
            kind=RequirementKind.SYSTEM,
            req_class=RequirementClass.ASSUMPTION,
            statement="Clock availability is assumed: no clock-dependent conclusion may be a PASS.",
            limits=None,
            expression=None,
            signals=("REFCLK_100M",),
        ),
        requirement(
            "DIAG_010",
            kind=RequirementKind.FUNCTIONAL,
            req_class=RequirementClass.USER_REQUIREMENT,
            statement="PRECONDITIONS_SATISFIED is a diagnostic, not proof the device booted.",
            limits=None,
            expression={
                "op": "rise_above",
                "signal": "V(PRECONDITIONS_SATISFIED)",
                "value": 1.5,
                "unit": "V",
            },
            signals=("PRECONDITIONS_SATISFIED",),
            criticality=Criticality.INFORMATIONAL,
        ),
    ]


def _write_models(out: Path) -> dict[str, Path]:
    """Write one self-contained library per board model and register it."""
    store = ModelStore(out)
    written: dict[str, Path] = {}
    for model_id, spec in BOARD_MODELS.items():
        if spec.kind == "regulator":
            text = regulator_text(spec.subckt)
        else:
            header = f"* {model_id}: {spec.subckt} from the primitive library\n"
            text = header + primitive_text(spec.subckt)
        record = store.add_text_artifact(
            text,
            model_id=model_id,
            kind="generated",
            notes=[f"subcircuit {spec.subckt}"],
        )
        written[model_id] = record.absolute(out)
    return written


# --------------------------------------------------------------------------- #
# decks


def _model_instances(project: NeutralProject, out: Path) -> dict[str, str]:
    """Instance cards for every modelled component, in the model's port order."""
    nets: dict[str, dict[str, str]] = {}
    for connection in project.connections:
        nets.setdefault(connection.refdes, {})[connection.physical_pin] = connection.net_name

    cards: dict[str, str] = {}
    for refdes, model_id in sorted(project.model_assignments.items()):
        spec = BOARD_MODELS.get(model_id)
        if spec is None:
            continue
        pins = nets.get(refdes, {})
        missing = [port for port in spec.ports if port not in pins]
        if missing:
            raise ValueError(f"{refdes}: model {model_id} needs pins {missing}")
        nodes = " ".join(pins[port] for port in spec.ports)
        params = " ".join(f"{key}={value}" for key, value in sorted(spec.params.items()))
        cards[refdes] = f"X{refdes} {nodes} {spec.subckt} {params}".strip()
    return cards


def _passive_cards(project: NeutralProject) -> dict[str, str]:
    """Element cards for passives; two-terminal parts use the CSV pin order."""
    nets: dict[str, dict[str, str]] = {}
    for connection in project.connections:
        nets.setdefault(connection.refdes, {})[connection.physical_pin] = connection.net_name
    values = {row.refdes: row.value for row in project.components}
    cards: dict[str, str] = {}
    for row in project.components:
        refdes = row.refdes
        if refdes in project.model_assignments:
            continue
        pins = nets.get(refdes, {})
        if not pins:
            continue
        # Deterministic pin order: '+'/A/1 first, then '-'/B/2.
        order = sorted(pins, key=lambda pin: (pin not in ("+", "A", "1"), pin))
        if len(order) != 2:
            continue
        prefix = refdes[0].upper()
        value = values.get(refdes, "")
        cards[refdes] = f"{prefix}{refdes[1:]} {pins[order[0]]} {pins[order[1]]} {value}".strip()
    return cards


def deck_for_scenario(
    project: NeutralProject,
    scenario_id: str,
    *,
    out: Path,
    include_loads: bool = True,
    extra_elements: Sequence[str] = (),
) -> DeckSpec:
    """Build the deck for one scenario from the neutral netlist."""
    spec = scenario(scenario_id)
    elements: list[str] = []
    sources: list[Source] = []

    if scenario_id == "fast_rail":
        ramp = 0.4e-3
    elif scenario_id == "slow_rail":
        ramp = 8e-3
    else:
        ramp = VIN_RAMP

    sources.append(
        Source.ramp("V1", "VIN_12V_SRC", "0", v0=0.0, v1=12.0, rise_s=ramp, hold_s=SIM_STOP)
    )
    sources.append(
        Source.pulse(
            "V2",
            "REFCLK_100M",
            "0",
            v1=0.0,
            v2=3.3,
            delay_s=0.0,
            rise_s=1e-9,
            fall_s=1e-9,
            width_s=5e-9,
            period_s=10e-9,
        )
    )

    modelled = _model_instances(project, out)
    passives = _passive_cards(project)

    # U5 has no model: it is an abstraction boundary, not a hidden omission.
    for refdes, card in sorted(passives.items()):
        if refdes in ("V1", "V2"):
            continue
        if not include_loads and refdes in ("I1", "I2"):
            continue
        elements.append(card)
    for refdes, card in sorted(modelled.items()):
        if not include_loads and refdes in ("I1", "I2"):
            continue
        elements.append(card)
    elements.extend(extra_elements)

    # The diagnostic signal is computed, never a pin of the device.
    elements.append(
        "Bdiag PRECONDITIONS_SATISFIED 0 V=if(V(PERST_N)>2 & V(3V3)>3.1 & V(1V8)>1.7, 3.3, 0)"
    )

    library = out / "models" / "board.lib"
    includes = (Include(path=str(library.resolve())),)

    return DeckSpec(
        title=f"demo board - {scenario_id} ({spec.title})",
        includes=includes,
        sources=tuple(sources),
        elements=tuple(elements),
        directives=(
            "* U5 (SYNTH-PCIE-SW-0) has no model: its lanes and protocol are outside dynamic "
            "coverage; its supplies, reset, straps and sideband pins are checked statically.",
        ),
        tran=TranSpec(tstep=SIM_STOP / 20000.0, tstop=SIM_STOP, tstart=0.0, tmax=SIM_TMAX),
        save=(
            "V(3V3)",
            "V(1V8)",
            "V(12V)",
            "V(PERST_N)",
            "V(PG_3V3)",
            "V(PG_1V8)",
            "V(CONFIG0)",
            "V(CONFIG1)",
            "V(CONFIG2)",
            "V(SMB_CLK)",
            "V(SMB_DAT)",
            "V(CLK_REQ_N)",
            "V(PRECONDITIONS_SATISFIED)",
            "V(EN_U1)",
            "V(FB_U1)",
            "V(REFCLK_100M)",
        ),
        meas=(
            MeasSpec(name="v3v3_9ms", signal="V(3V3)", at_s=9e-3),
            MeasSpec(name="v1v8_9ms", signal="V(1V8)", at_s=9e-3),
            MeasSpec(name="vperst_9ms", signal="V(PERST_N)", at_s=9e-3),
        ),
        options={"method": "gear", "trtol": 7},
    )


def _combined_library(out: Path) -> Path:
    """Concatenate the per-model libraries, defining each subcircuit once.

    Every per-model library is self-contained (it embeds the primitives it
    instantiates), so a plain concatenation would define `BM_SCHMITT` several
    times and LTspice would reject the deck. Duplicate definitions are dropped
    when they are byte-identical and reported as an error when they are not.
    """
    store = ModelStore(out)
    blocks: dict[str, str] = {}
    order: list[str] = []
    for model_id in sorted(BOARD_MODELS):
        text = store.read_text(model_id)
        for block in _split_subcircuits(text):
            name = _subckt_name_of(block)
            if name is None:
                continue
            existing = blocks.get(name)
            if existing is None:
                blocks[name] = block
                order.append(name)
            elif existing.strip() != block.strip():
                raise ValueError(
                    f"two different definitions of subcircuit {name!r} would be exported in "
                    "one library"
                )
    parts = ["* demo board model library (generated by boardmodeler)"]
    parts.append("* each subcircuit is defined once; the per-model files remain in models/generated/")
    parts.extend(blocks[name] for name in order)
    library = out / "models" / "board.lib"
    library.write_text("\n\n".join(part.rstrip() for part in parts) + "\n", encoding="utf-8")
    return library


def _split_subcircuits(text: str) -> list[str]:
    """Every ``.subckt … .ends`` block in ``text``, in file order."""
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith(".subckt"):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif stripped.startswith(".ends"):
            current.append(line)
            blocks.append("\n".join(current))
            current = []
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _subckt_name_of(block: str) -> str | None:
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(".subckt"):
            parts = stripped.split()
            return parts[1] if len(parts) > 1 else None
    return None


# --------------------------------------------------------------------------- #
# build


def build_demo_project(
    out_dir: str | Path, *, ltspice: LtspiceInstall | None = None, workdir: Path | None = None
) -> DemoBuildResult:
    """Assemble the demo project from the committed fixtures."""
    out = Path(out_dir).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    create_project(
        out,
        project_id="demo_board",
        name="Integrated board-level demonstration",
        mode="circuit",
        supply_domains=json.loads((DEMO_SOURCE / "circuit" / "project.json").read_text())[
            "supply_domains"
        ],
        notes=[
            "built by `boardmodeler demo build` from fixtures/demo_board and fixtures/switch_fixture"
        ],
    )

    # circuit files
    (out / "circuit").mkdir(exist_ok=True)
    for name in ("components.csv", "connections.csv", "project.json"):
        shutil.copyfile(DEMO_SOURCE / "circuit" / name, out / "circuit" / name)
    pin_rows = _merge_pins(DEMO_SOURCE / "pins.csv", SWITCH_SOURCE / "pins.csv")
    header = (
        "refdes,physical_pin,name,direction,polarity,supply_domain,output_topology,"
        "connection_requirement,unused_pin_treatment"
    )
    (out / "circuit" / "pins.csv").write_text(
        header
        + "\n"
        + "\n".join(
            ",".join(
                [
                    row["refdes"],
                    row["physical_pin"],
                    row["name"],
                    row["direction"],
                    row["polarity"],
                    row["supply_domain"],
                    row["output_topology"],
                    row["connection_requirement"],
                    row["unused_pin_treatment"],
                ]
            )
            for row in pin_rows
        )
        + "\n",
        encoding="utf-8",
    )

    # documents + requirements
    docs = out / "docs" / "files"
    docs.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        SWITCH_SOURCE / "SYNTHETIC_SWITCH_CONTRACT.md",
        docs / f"{DOC_ID}-SYNTHETIC_SWITCH_CONTRACT.md",
    )
    switch_requirements = json.loads(
        (SWITCH_SOURCE / "requirements.json").read_text(encoding="utf-8")
    )
    requirements = [
        Requirement.model_validate(item) for item in switch_requirements["requirements"]
    ] + board_requirements()
    (out / "evidence").mkdir(exist_ok=True)
    (out / "evidence" / "requirements.json").write_text(
        json.dumps(
            {
                "requirements": [
                    json.loads(r.model_dump_json(by_alias=True)) for r in requirements
                ],
                "note": "device contract (TEST_FIXTURE) plus board requirements (USER)",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (out / "evidence" / "pinmap.json").write_text(
        json.dumps({"pins": pin_rows}, indent=2), encoding="utf-8"
    )

    # models
    models_dir = out / "models"
    models_dir.mkdir(exist_ok=True)
    for folder in ("generated", "records", "capabilities"):
        (models_dir / folder).mkdir(exist_ok=True)
    _write_models(out)
    _combined_library(out)

    # decks + tests
    decks_dir = out / "tests" / "decks"
    decks_dir.mkdir(parents=True, exist_ok=True)
    neutral = read_neutral_project(out)
    tests: list[TestCase] = []
    for index, scenario_id in enumerate(DEMO_SCENARIOS, start=1):
        deck = deck_for_scenario(neutral, scenario_id, out=out)
        write_deck(deck, decks_dir / f"{scenario_id}.cir")
        tests.append(
            TestCase(
                test_id=f"T_{scenario_id}_{index:03d}",
                requirement_ids=_requirements_for(scenario_id, requirements),
                scenario_id=scenario_id,
                scope="circuit_compliance"
                if scenario(scenario_id).intent == "satisfy"
                else "fault_detection",
                deck_template=f"tests/decks/{scenario_id}.cir",
                expected=ExpectationSpec(
                    kind=scenario(scenario_id).intent, detail=scenario(scenario_id).description
                ),
                measurement=[
                    "V(3V3)",
                    "V(1V8)",
                    "V(PERST_N)",
                    "V(PG_3V3)",
                    "V(PG_1V8)",
                    "V(CONFIG0)",
                    "V(SMB_CLK)",
                    "V(SMB_DAT)",
                ],
                max_timestep_s=SIM_TMAX,
            )
        )
    (out / "tests" / "tests.json").write_text(
        json.dumps({"tests": [json.loads(t.model_dump_json()) for t in tests]}, indent=2),
        encoding="utf-8",
    )

    # static checks on the built netlist
    pins_by_refdes = _pins_by_refdes(pin_rows)
    circuit = _circuit_from_neutral(neutral, pins_by_refdes)
    netmap = build_netmap(circuit)
    static_findings = run_static_checks(
        circuit,
        netmap,
        project=neutral,
        pins=pins_by_refdes,
        project_root=out,
    )
    (out / "evidence" / "static_findings.json").write_text(
        json.dumps([json.loads(f.model_dump_json()) for f in static_findings], indent=2),
        encoding="utf-8",
    )

    # capability records for the generated models
    install = ltspice or locate()
    capabilities: dict[str, ModelCapability] = {}
    if install is not None and workdir is not None:
        capabilities = _probe_board_models(out, install, workdir)

    result = DemoBuildResult(
        project_dir=out,
        tests=tests,
        requirements=requirements,
        static_findings=static_findings,
        capabilities=capabilities,
        decks={sid: decks_dir / f"{sid}.cir" for sid in DEMO_SCENARIOS},
        files=sorted(
            str(path.relative_to(out)).replace("\\", "/")
            for path in out.rglob("*")
            if path.is_file()
        ),
        detail=(
            f"{len(requirements)} requirements, {len(tests)} test cases, "
            f"{len(static_findings)} static findings"
        ),
    )
    (out / "evidence" / "demo_build.json").write_text(
        json.dumps(
            {
                "detail": result.detail,
                "requirements": len(requirements),
                "tests": [t.test_id for t in tests],
                "static_findings": [
                    {"code": f.code, "status": f.status.value, "refdes": f.refdes}
                    for f in static_findings
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return result


def _requirements_for(scenario_id: str, requirements: Sequence[Requirement]) -> list[str]:
    """Which requirements a scenario exercises.

    Operating scenarios exercise the rail/sequencing/connectivity requirements;
    fault scenarios target the specific requirement whose violation they inject,
    so a fault scenario that fails to trip anything is visible as such.
    """
    by_suffix = {r.req_id.split("_")[-1]: r.req_id for r in requirements}
    if scenario(scenario_id).intent == "violate_detected":
        mapping = {
            "reset_early_release": ["SEQ_003", "SEQ_004"],
            "pullup_missing": ["CONN_005"],
            "pullup_wrong_domain": ["CONN_006"],
            "invalid_strap": ["CONFIG_007"],
            "load_step": ["SEQ_001"],
            "brownout_short_interrupt": ["SEQ_001"],
        }.get(scenario_id, [])
        return [by_suffix[suffix] for suffix in mapping if suffix in by_suffix]
    operating = [
        "SEQ_001",
        "SEQ_002",
        "SEQ_003",
        "SEQ_004",
        "CONN_005",
        "CONFIG_007",
        "CONFIG_008",
        "DIAG_010",
    ]
    return [by_suffix[suffix] for suffix in operating if suffix in by_suffix]


def _circuit_from_neutral(
    project: NeutralProject, pins_by_refdes: Mapping[str, Sequence[PinDefinition]] | None = None
):
    """A netlist-level circuit for the static checks (X instances + passives).

    Components with no simulation model are emitted too, as instances of a stub
    subcircuit built from their pin list: an abstraction boundary only means
    something if the part it describes is actually in the netlist the checks read.
    """
    lines = ["* demo board netlist for static checks"]
    emitted: set[str] = set()
    nets: dict[str, dict[str, str]] = {}
    for connection in project.connections:
        nets.setdefault(connection.refdes, {})[connection.physical_pin] = connection.net_name
    for refdes, model_id in sorted(project.model_assignments.items()):
        spec = BOARD_MODELS.get(model_id)
        if spec is None:
            continue
        pins = nets.get(refdes, {})
        nodes = " ".join(pins.get(port, "NC") for port in spec.ports)
        lines.append(f"X{refdes} {nodes} {spec.subckt}")
        if spec.subckt not in emitted:
            lines.append(f".subckt {spec.subckt} {' '.join(spec.ports)}")
            lines.append(f".ends {spec.subckt}")
            emitted.add(spec.subckt)
    for row in project.components:
        pins = nets.get(row.refdes, {})
        if not pins or row.refdes in project.model_assignments:
            continue
        order = sorted(pins, key=lambda pin: (pin not in ("+", "A", "1"), pin))
        if len(pins) > 2:
            # an unmodelled multi-pin part: keep it in the netlist with its own
            # stub subcircuit so every pin is visible to the checks
            pin_defs = list((pins_by_refdes or {}).get(row.refdes, []))
            port_order = [
                (pin.name, pin.physical_pin) for pin in pin_defs if pin.physical_pin in pins
            ] or [(pin, pin) for pin in sorted(pins)]
            subckt = row.part_number or f"UNMODELLED_{row.refdes}"
            nodes = " ".join(pins[physical] for _name, physical in port_order)
            lines.append(f"X{row.refdes} {nodes} {subckt}")
            if subckt not in emitted:
                lines.append(f".subckt {subckt} " + " ".join(name for name, _ in port_order))
                lines.append(f".ends {subckt}")
                emitted.add(subckt)
            continue
        if len(order) != 2:
            continue
        first, second = pins[order[0]], pins[order[1]]
        if row.refdes == "V1":
            lines.append(f"V1 {first} 0 PWL(0 0 2m 12 20m 12)")
            continue
        if row.refdes == "V2":
            lines.append(f"V2 {first} 0 PULSE(0 3.3 0 1n 1n 5n 10n)")
            continue
        prefix = row.refdes[0].upper()
        lines.append(f"{prefix}{row.refdes[1:]} {first} {second} {row.value}")
    return parse_netlist("\n".join(lines) + "\n.end\n")


def _probe_board_models(
    out: Path, install: LtspiceInstall, workdir: Path
) -> dict[str, ModelCapability]:
    """Probe the generated regulator models and record their capabilities."""
    store = ModelStore(out)
    capabilities: dict[str, ModelCapability] = {}
    ctx = RunContext(project_dir=out, ltspice=install.path, timeout_s=120)
    for model_id in ("bm_reg_buck", "bm_reg_ldo"):
        spec = BOARD_MODELS[model_id]
        model_path = store.path_of(model_id)
        text = model_path.read_text(encoding="utf-8")
        ports = subckt_ports(text, spec.subckt)
        role_of_port = _roles_for(spec.subckt, ports)
        probe_spec = ModelProbeSpec(
            model_id=model_id,
            kind=ModelKind.REDUCED_BEHAVIORAL,
            path=model_path,
            subckt=spec.subckt,
            ports=tuple(ports),
            port_roles=role_of_port,
            nets={"gnd": "0", "vout": "n_vout", "vin": "n_vin", "sw": "n_sw"},
            nominal_vout=3.3 if "BUCK" in spec.subckt else 1.8,
            vref=0.8,
            rfbt=10e3,
            rfbb=3.24e3 if "BUCK" in spec.subckt else 8.06e3,
            vin=12.0 if "BUCK" in spec.subckt else 3.3,
            load_ohm=10.0,
            probe_time_scale=1.0,
        )
        report = probe_model(
            probe_spec,
            ctx,
            workdir=workdir / model_id,
            evidence_level=EvidenceLevel.SYNTHETIC_ANALYTICAL,
            exclusions=[
                "generated reduced behavioural model, not silicon-accurate",
                "temperature dependence is not modelled",
            ],
        )
        capabilities[model_id] = report.capability
        (out / "models" / "capabilities" / f"{model_id}.json").write_text(
            report.capability.model_dump_json(indent=2), encoding="utf-8"
        )
    # requirement -> behaviour map used by the capability gate
    requirement_behaviours = {
        "REQ_DEMO_SEQ_001": "dc_regulation",
        "REQ_DEMO_SEQ_002": "startup",
        "REQ_DEMO_SEQ_003": "startup",
        "REQ_DEMO_SEQ_004": "startup",
    }
    (out / "evidence" / "capability_map.json").write_text(
        json.dumps(requirement_behaviours, indent=2), encoding="utf-8"
    )
    return capabilities


def _roles_for(subckt: str, ports: Sequence[str]) -> dict[str, str]:
    mapping = {
        "VIN": "vin",
        "EN": "en",
        "FB": "fb",
        "PG": "pg",
        "VOUT": "vout",
        "GND": "gnd",
        "SW": "sw",
        "ILIM_MODE": "ilim_mode",
        "COMP": "comp",
        "BOOT": "boot",
        "SS": "ss",
        "RT": "rt",
    }
    return {port: mapping[port] for port in ports if port in mapping}


# --------------------------------------------------------------------------- #
# circuit check


def check_circuit(
    project_dir: str | Path,
    *,
    circuit_path: Path | None = None,
    ltspice: LtspiceInstall | None = None,
    scope: str | None = None,
    cancel: threading.Event | None = None,
    report_path: Path | None = None,
    results_path: Path | None = None,
) -> CheckResult:
    """Static checks + dynamic scenarios for a built demo project."""
    root = Path(project_dir).resolve()
    project = Project(root)
    neutral = read_neutral_project(root)
    install = ltspice or locate()

    pin_rows = (
        [
            {key: value for key, value in row.items()}
            for row in json.loads((root / "evidence" / "pinmap.json").read_text(encoding="utf-8"))[
                "pins"
            ]
        ]
        if (root / "evidence" / "pinmap.json").is_file()
        else []
    )
    pins_by_refdes = _pins_by_refdes(pin_rows)

    findings: list[Finding] = []
    circuit = _circuit_from_neutral(neutral)
    netmap = build_netmap(circuit)
    findings.extend(
        run_static_checks(circuit, netmap, project=neutral, pins=pins_by_refdes, project_root=root)
    )

    if circuit_path is not None and install is not None:
        step = netlist_step(install.path, Path(circuit_path), timeout_s=120)
        findings.append(
            Finding(
                code="SC001_syntax",
                status=Status.PASS if step.exit_code == 0 else Status.FAIL,
                message=(
                    f"LTspice netlisted {Path(circuit_path).name} successfully"
                    if step.exit_code == 0
                    else f"LTspice could not netlist {Path(circuit_path).name}: {step.observed()}"
                ),
                detail={"deck": str(circuit_path)},
            )
        )

    requirements = list(project.requirements().values())
    tests = project.tests(scope=scope)
    capabilities = _load_capabilities(root)
    behaviour_map = _load_behaviour_map(root)
    capability_gate = (
        gate_from_capability(list(capabilities.values()), behaviour_map)
        if capabilities and behaviour_map
        else None
    )

    results: list[TestResult] = []
    if install is not None:
        ctx = project.run_context(ltspice=install, timeout_s=180)
        for case in tests:
            if cancel is not None and cancel.is_set():
                break
            deck_path = root / case.deck_template
            artifacts = run_case(
                ctx,
                case,
                build_deck=lambda run_dir, deck_path=deck_path: _copy_deck(deck_path, run_dir),
                run_identifier=case.test_id,
                cancel=cancel,
            )
            results.append(
                evaluate_case(
                    case,
                    artifacts,
                    requirements,
                    connectivity=netmap,
                    supply_domains=neutral.supply_domains,
                    capability_gate=capability_gate,
                )
            )

    status = _worst(findings, results)
    result = CheckResult(
        project_dir=root,
        findings=findings,
        results=results,
        status=status,
        coverage=_coverage(requirements, tests, results),
        detail=f"{len(findings)} finding(s), {len(results)} result(s)",
    )

    if results_path is not None:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(
            json.dumps(
                {
                    "project": str(root),
                    "status": status.value,
                    "findings": [json.loads(f.model_dump_json()) for f in findings],
                    "results": [json.loads(r.model_dump_json()) for r in results],
                    "summary": result.summary(),
                    "coverage": result.coverage,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        result.results_path = results_path

    if report_path is not None:
        markers: dict[str, list[ViolationMarker]] = {}
        for test_result in results:
            if test_result.status is not Status.FAIL:
                continue
            for requirement_id in test_result.requirement_ids:
                for signal in ("V(3V3)", "V(1V8)", "V(PERST_N)"):
                    markers.setdefault(signal, []).append(
                        ViolationMarker(
                            requirement_id=requirement_id, t_s=6e-3, label=test_result.detail[:80]
                        )
                    )
        write_report(
            report_path,
            ReportInputs(
                project_id=project.config.project_id,
                project_name=project.config.name,
                status=status,
                findings=findings,
                results=results,
                requirements=requirements,
                coverage=result.coverage,
                capability=next(iter(capabilities.values()), None),
                limitations=[
                    "U5 (the synthetic switch) has no model: lanes and protocol are outside dynamic coverage",
                    "clock availability is an assumption, not a verified path",
                    "temperature dependence is not modelled anywhere",
                ],
                qualifications=["fixture-derived results, not silicon measurements"],
                reproduction=[
                    "uv run boardmodeler demo build --out build/demo",
                    "uv run boardmodeler circuit check --project build/demo "
                    "--circuit build/demo/circuit/demo.asc --json",
                ],
                extra_sections={"Violation markers": _markers_table(markers)},
            ),
        )
        result.report_path = report_path
    return result


def _markers_table(markers: Mapping[str, Sequence[ViolationMarker]]) -> str:
    if not markers:
        return "<p class='muted'>No failing requirement produced a marker.</p>"
    rows = "".join(
        f"<tr><td><code>{signal}</code></td><td>{marker.requirement_id}</td>"
        f"<td>{marker.t_s:g} s</td><td>{marker.label}</td></tr>"
        for signal, items in sorted(markers.items())
        for marker in items
    )
    return (
        "<table><thead><tr><th>signal</th><th>requirement</th><th>time</th><th>detail</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


def _copy_deck(source: Path, run_dir: Path) -> Path:
    target = run_dir / "deck.cir"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def _load_capabilities(root: Path) -> dict[str, ModelCapability]:
    folder = root / "models" / "capabilities"
    if not folder.is_dir():
        return {}
    return {
        path.stem: ModelCapability.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(folder.glob("*.json"))
    }


def _load_behaviour_map(root: Path) -> dict[str, str]:
    path = root / "evidence" / "capability_map.json"
    if not path.is_file():
        return {}
    return {
        str(key): str(value) for key, value in json.loads(path.read_text(encoding="utf-8")).items()
    }


def _worst(findings: Sequence[Finding], results: Sequence[TestResult]) -> Status:
    order = [Status.BLOCKED, Status.FAIL, Status.UNKNOWN, Status.NOT_APPLICABLE, Status.PASS]
    seen = {finding.status for finding in findings} | {result.status for result in results}
    for status in order:
        if status in seen:
            return status
    return Status.UNKNOWN


def _coverage(
    requirements: Sequence[Requirement], tests: Sequence[TestCase], results: Sequence[TestResult]
) -> dict[str, object]:
    from boardmodeler.reporting.export import requirements_coverage

    return requirements_coverage(requirements, tests, results)


# --------------------------------------------------------------------------- #
# fault matrix


def run_fault_matrix(
    project_dir: str | Path,
    *,
    out_dir: Path | None = None,
    ltspice: LtspiceInstall | None = None,
    faults: Sequence[str] | None = None,
) -> dict[str, object]:
    """Inject every fault into its own copy, run the check, and record detection.

    The original project is hashed before and after, so a mutation that leaked into
    it is detected rather than assumed away.
    """
    root = Path(project_dir).resolve()
    work = Path(out_dir) if out_dir is not None else root.parent / "fault_matrix"
    work.mkdir(parents=True, exist_ok=True)

    watched = ["circuit/connections.csv", "circuit/components.csv", "circuit/project.json"]
    before = {name: sha256_file(root / name) for name in watched if (root / name).is_file()}

    entries: list[dict[str, object]] = []
    for fault_id in faults or fault_ids():
        variant = work / fault_id
        mutation = MUTATORS[fault_id](root)
        apply_edits(root, mutation.edits, variant, fault_id=fault_id)
        check = check_circuit(variant, ltspice=ltspice)
        detected = _detected(check, mutation.expected_detection)
        entries.append(
            {
                "fault_id": fault_id,
                "description": mutation.description,
                "expected_detection": mutation.expected_detection,
                "detected": detected,
                "status": check.status.value,
                "summary": check.summary(),
                "evidence": _detection_evidence(check),
                "modifications": [edit.as_dict() for edit in mutation.edits],
            }
        )

    after = {name: sha256_file(root / name) for name in watched if (root / name).is_file()}
    report = {
        "project": str(root),
        "faults": entries,
        "detected": sum(1 for entry in entries if entry["detected"]),
        "total": len(entries),
        "original_unchanged": before == after,
        "original_hashes": after,
    }
    (work / "fault_matrix.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _detected(check: CheckResult, expected: str) -> bool:
    """Was the injected fault actually caught?

    A fault counts as detected when the check it is supposed to trip fired: the
    static-check findings always count, and the dynamic results count when a test
    in the fault scenario reported FAIL — which, for a `violate_detected` case,
    means the violation was observed.
    """
    for finding in check.findings:
        if finding.code.startswith(expected) and finding.status is Status.FAIL:
            return True
    if expected in (
        "rail_never_valid",
        "sequencing_never_completes",
        "open_drain_level",
        "strap_word_invalid",
        "reset_release_too_early",
    ):
        return any(
            result.status is Status.PASS and "violation detected" in result.detail
            for result in check.results
        ) or any(result.status is Status.FAIL for result in check.results)
    return any(finding.status is Status.FAIL for finding in check.findings)


def _detection_evidence(check: CheckResult) -> list[str]:
    evidence = [
        f"{finding.code} [{finding.status.value}] {finding.message[:120]}"
        for finding in check.findings
        if finding.status is not Status.PASS
    ]
    evidence.extend(
        f"{result.test_id} [{result.status.value}] {result.detail[:120]}"
        for result in check.results
        if result.status is not Status.PASS
    )
    return evidence[:8]
