"""Pipeline controller (``docs/INTERFACES.md`` §1, D10).

Simulator-free runs are the honest default here: the LTspice path in the test
config points at a file that does not exist, so ``COMPILE_SIMULATE`` must report
``BLOCKED`` while still preserving the deck and its hash. The repair loop is
exercised by replacing the two boundaries a simulator would provide
(``run_deck_tests`` and ``evaluate_case``) with in-process doubles, which is the
only way to test the loop's bounds without a working simulator; everything else
(baseline freezing, repair enforcement, export wiring, extraction) runs for real.
"""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from boardmodeler.config import AppConfig, ProviderConfig
from boardmodeler.documents.chunk import chunk_document
from boardmodeler.documents.store import DocumentStore
from boardmodeler.domain.enums import ProviderKind, Status
from boardmodeler.domain.expressions import parse_expr
from boardmodeler.domain.hashing import sha256_text
from boardmodeler.domain.records import (
    DocumentRecord,
    ExpectationSpec,
    Limit,
    Requirement,
    TestCase,
    TestResult,
)
from boardmodeler.pipeline import controller as controller_module
from boardmodeler.pipeline.controller import (
    STAGE_ORDER,
    PipelineController,
    PipelineRequest,
    RepairEdit,
    RepairProposal,
    RepairViolation,
    Stage,
    apply_repair,
    check_repair,
)
from boardmodeler.pipeline.project import Project, create_project
from boardmodeler.pipeline.runner import RunArtifacts
from boardmodeler.providers.base import ExtractionTask
from boardmodeler.providers.fixture import FixtureProvider
from boardmodeler.requirements.extract import tasks_for
from boardmodeler.simulation.limits import RunUsability
from boardmodeler.simulation.log import LogSummary
from boardmodeler.simulation.measures import RunDiagnostics

CONTRACT_TEXT = "Synthetic contract. The output voltage is 3.3 V nominal.\n"
DECK_TEXT = "* test deck\nV1 in 0 1\nR1 in out 1k\n.tran 1m\n.end\n"
CASE_IDS = ("T_A", "T_B", "T_C", "T_D")


# --------------------------------------------------------------------------- #
# project fixtures


def make_project(tmp_path: Path, name: str = "proj") -> Project:
    return create_project(tmp_path / name, project_id="P_CTRL", name="Controller test")


def make_requirement(req_id: str = "REQ_VOUT", value: float = 3.3) -> Requirement:
    return Requirement(
        req_id=req_id,
        applies_to="U1",
        kind="ELECTRICAL",
        req_class="USER_REQUIREMENT",
        criticality="CRITICAL",
        origin="USER",
        statement=f"the output voltage is {value} V nominal",
        limits=Limit(typ=value, unit="V"),
        expression=parse_expr({"op": "lt", "signal": "V(out)", "value": value + 0.3, "unit": "V"}),
        signal_refs=["V(out)"],
    )


def make_case(
    test_id: str = "T_VOUT", requirement_ids: tuple[str, ...] = ("REQ_VOUT",)
) -> TestCase:
    return TestCase(
        test_id=test_id,
        requirement_ids=list(requirement_ids),
        scenario_id="nominal_startup",
        scope="circuit_compliance",
        deck_template="decks/simple.cir",
        expected=ExpectationSpec(kind="satisfy", detail="the rail settles at 3.3 V"),
        measurement=["V(out)"],
    )


def write_tests_file(project: Project, cases: list[TestCase]) -> None:
    (project.root / "decks").mkdir(parents=True, exist_ok=True)
    for case in cases:
        (project.root / case.deck_template).write_text(DECK_TEXT, encoding="utf-8")
    (project.root / "tests").mkdir(parents=True, exist_ok=True)
    (project.root / "tests" / "tests.json").write_text(
        json.dumps({"tests": [json.loads(case.model_dump_json()) for case in cases]}, indent=2),
        encoding="utf-8",
    )


def write_requirements_file(project: Project, requirements: list[Requirement]) -> None:
    (project.root / "evidence").mkdir(parents=True, exist_ok=True)
    project.requirements_path.write_text(
        json.dumps(
            {
                "requirements": [
                    json.loads(requirement.model_dump_json(by_alias=True))
                    for requirement in requirements
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def extraction_payloads(requirements: list[Requirement]) -> dict[ExtractionTask, dict]:
    return {
        ExtractionTask.IDENTITY: {
            "part": {"base_part": "SYNTH", "confidence": "partial", "ambiguities": []}
        },
        ExtractionTask.PINMAP: {
            "part_id": "SYNTH",
            "pins": [
                {
                    "physical_pin": "1",
                    "name": "VOUT",
                    "function": "output",
                    "polarity": "not_applicable",
                    "direction": "output",
                    "output_topology": "push_pull",
                    "connection_requirement": "required",
                }
            ],
        },
        ExtractionTask.REQUIREMENTS: {
            "requirements": [
                json.loads(requirement.model_dump_json(by_alias=True))
                for requirement in requirements
            ]
        },
        ExtractionTask.CAPABILITY_SUMMARY: {"behaviors": {"dc_regulation": "unknown"}},
    }


def author_extraction_for(
    project: Project,
    fixture_dir: Path,
    records: list[DocumentRecord],
    requirements: list[Requirement],
) -> FixtureProvider:
    """Author the four fixtures against the exact snippets extraction will build."""
    snippets = [
        snippet
        for record in sorted(records, key=lambda item: item.doc_id)
        for snippet in chunk_document(record, base_dir=project.root)
    ]
    provider = FixtureProvider(fixture_dir)
    for task, payload in extraction_payloads(requirements).items():
        provider.write_fixture(tasks_for(task, snippets), payload)
    return provider


def author_extraction(
    project: Project, fixture_dir: Path, requirements: list[Requirement]
) -> FixtureProvider:
    record = DocumentStore(project.root).add_synthetic("contract.txt", CONTRACT_TEXT)
    return author_extraction_for(project, fixture_dir, [record], requirements)


def make_config(fixture_dir: Path, *, simulator: Path | None = None) -> AppConfig:
    """A config whose LTspice path does not exist unless one is supplied."""
    config = AppConfig()
    config.ltspice.path = str(simulator or fixture_dir.parent / "no-ltspice.exe")
    config.providers = {
        "fixture": ProviderConfig(kind=ProviderKind.FIXTURE, fixture_dir=str(fixture_dir)),
    }
    config.provider_order = [ProviderKind.FIXTURE]
    return config


def full_project(
    tmp_path: Path,
    *,
    cases: list[TestCase] | None = None,
    requirements: list[Requirement] | None = None,
    name: str = "proj",
) -> tuple[Project, AppConfig]:
    project = make_project(tmp_path, name)
    requirements = requirements if requirements is not None else [make_requirement()]
    cases = cases if cases is not None else [make_case()]
    write_tests_file(project, cases)
    author_extraction(project, tmp_path / f"{name}-fixtures", requirements)
    return project, make_config(tmp_path / f"{name}-fixtures")


def by_stage(result) -> dict[Stage, object]:
    return {progress.stage: progress for progress in result.stages}


# --------------------------------------------------------------------------- #
# the chain


def test_the_chain_reports_every_stage_in_order_and_blocks_without_a_simulator(
    tmp_path: Path,
) -> None:
    project, config = full_project(tmp_path)
    export_dir = tmp_path / "out"

    run = PipelineController(config).run(
        PipelineRequest(project_dir=project.root, export_dir=export_dir)
    )

    assert [progress.stage for progress in run.stages] == list(STAGE_ORDER)
    stages = by_stage(run)
    assert stages[Stage.IDENTIFY].status is Status.PASS
    assert stages[Stage.COLLECT_EVIDENCE].status is Status.PASS
    assert stages[Stage.BUILD_REQUIREMENTS].status is Status.PASS
    assert stages[Stage.REVIEW_SOURCES].status is Status.PASS
    assert stages[Stage.FREEZE_BASELINE].status is Status.PASS
    assert stages[Stage.SELECT_MODEL].status is Status.UNKNOWN
    assert stages[Stage.COMPILE_SIMULATE].status is Status.BLOCKED
    assert "simulator_unavailable" in stages[Stage.COMPILE_SIMULATE].detail
    assert stages[Stage.EVALUATE].status is Status.BLOCKED
    assert "repair cannot proceed without a simulation" in stages[Stage.REPAIR].detail
    assert stages[Stage.EXPORT].status is Status.PASS
    assert run.status is Status.BLOCKED

    # The refused deck is still preserved, with its hash, as evidence.
    assert any(path.endswith("deck.cir") for path in run.artifacts)
    assert "baseline.json" in run.artifacts
    assert run.manifest_path is not None and run.manifest_path.is_file()
    assert run.export_dir == export_dir

    baseline = project.baseline()
    assert baseline is not None
    assert [requirement.req_id for requirement in baseline.requirements] == ["REQ_VOUT"]
    assert [case.test_id for case in baseline.tests] == ["T_VOUT"]
    assert baseline.baseline_version == 1
    assert baseline.requirement_baseline_hash
    assert baseline.test_hash

    assert {result.status for result in run.results} == {Status.BLOCKED}
    assert all(result.blocked_reason for result in run.results)
    assert (project.root / "results.json").is_file()
    assert run.diagnostics["provider_identity"] == "fixture"
    assert run.diagnostics["baseline_version"] == "1"

    # Extraction left its evidence (and its open questions) on disk.
    evidence = project.root / "evidence"
    for name in ("requirements.extracted.json", "pinmap.json", "extraction.json"):
        assert (evidence / name).is_file(), name
    extraction_metadata = json.loads((evidence / "extraction.json").read_text(encoding="utf-8"))
    assert extraction_metadata["provider"]["provider"] == "fixture"
    assert extraction_metadata["cache_hits"] == 0
    assert extraction_metadata["disclosures"] == []
    assert extraction_metadata["findings"] == []
    pinmap = json.loads((evidence / "pinmap.json").read_text(encoding="utf-8"))
    assert [pin["physical_pin"] for pin in pinmap["pins"]] == ["1"]
    assert len(sorted((evidence / "cache").glob("*.json"))) == 4, "one entry per extraction task"


def test_a_project_that_does_not_exist_is_blocked_without_creating_anything(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "not-a-project"
    config = make_config(tmp_path / "fixtures")
    run = PipelineController(config).run(PipelineRequest(project_dir=missing))

    stages = by_stage(run)
    assert stages[Stage.IDENTIFY].status is Status.BLOCKED
    assert "project_not_found" in stages[Stage.IDENTIFY].detail
    assert all(
        progress.status is Status.NOT_APPLICABLE
        for progress in run.stages
        if progress.stage is not Stage.IDENTIFY
    )
    assert run.status is Status.BLOCKED
    assert not missing.exists()


def test_cancellation_before_the_run_stops_the_chain(tmp_path: Path) -> None:
    project, config = full_project(tmp_path)
    cancel = threading.Event()
    cancel.set()
    run = PipelineController(config).run(PipelineRequest(project_dir=project.root), cancel=cancel)
    stages = by_stage(run)
    assert stages[Stage.IDENTIFY].status is Status.BLOCKED
    assert "cancelled" in stages[Stage.IDENTIFY].detail
    assert run.status is Status.BLOCKED
    assert not project.requirements_path.exists(), "a cancelled run must not write evidence"


def test_a_deadline_is_reported_not_ignored(tmp_path: Path) -> None:
    project, config = full_project(tmp_path)
    run = PipelineController(config).run(PipelineRequest(project_dir=project.root, deadline_s=0.0))
    stages = by_stage(run)
    assert stages[Stage.IDENTIFY].status is Status.BLOCKED
    assert "deadline_exceeded" in stages[Stage.IDENTIFY].detail


def test_progress_callbacks_see_every_stage(tmp_path: Path) -> None:
    project, config = full_project(tmp_path)
    seen: list[Stage] = []
    PipelineController(config).run(
        PipelineRequest(project_dir=project.root), progress=lambda item: seen.append(item.stage)
    )
    assert seen == list(STAGE_ORDER)


def test_an_undeclared_requirement_set_without_fixtures_blocks_the_run(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    write_tests_file(project, [make_case()])
    # A document exists, but no fixture answers the extraction request.
    DocumentStore(project.root).add_synthetic("contract.txt", CONTRACT_TEXT)
    config = make_config(tmp_path / "empty-fixtures")

    run = PipelineController(config).run(PipelineRequest(project_dir=project.root))
    stages = by_stage(run)
    assert stages[Stage.COLLECT_EVIDENCE].status is Status.BLOCKED
    assert "fixture_missing" in stages[Stage.COLLECT_EVIDENCE].detail
    assert stages[Stage.BUILD_REQUIREMENTS].status is Status.NOT_APPLICABLE
    assert not (project.root / "baseline.json").exists()


def test_a_declared_requirement_set_survives_a_missing_extraction(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    write_requirements_file(project, [make_requirement()])
    write_tests_file(project, [make_case()])
    DocumentStore(project.root).add_synthetic("contract.txt", CONTRACT_TEXT)
    config = make_config(tmp_path / "empty-fixtures")

    run = PipelineController(config).run(PipelineRequest(project_dir=project.root))
    stages = by_stage(run)
    assert stages[Stage.COLLECT_EVIDENCE].status is Status.UNKNOWN
    assert "fixture_missing" in stages[Stage.COLLECT_EVIDENCE].detail
    assert "declared in the project" in stages[Stage.COLLECT_EVIDENCE].detail
    assert stages[Stage.FREEZE_BASELINE].status is Status.PASS
    baseline = project.baseline()
    assert baseline is not None
    assert [requirement.req_id for requirement in baseline.requirements] == ["REQ_VOUT"]
    assert [req_id for result in run.results for req_id in result.requirement_ids] == ["REQ_VOUT"]


def test_export_is_skipped_when_no_directory_is_requested(tmp_path: Path) -> None:
    project, config = full_project(tmp_path)
    run = PipelineController(config).run(PipelineRequest(project_dir=project.root))
    stages = by_stage(run)
    assert stages[Stage.EXPORT].status is Status.NOT_APPLICABLE
    assert run.export_dir is None
    assert run.manifest_path is None


def test_the_export_stage_delegates_to_export_project(tmp_path: Path, monkeypatch) -> None:
    project, config = full_project(tmp_path)
    calls: list[tuple[Path, Path]] = []
    real = controller_module.export_project

    def spy(project_arg, out_dir, **kwargs):
        calls.append((Path(project_arg.root), Path(out_dir)))
        return real(project_arg, out_dir, **kwargs)

    monkeypatch.setattr(controller_module, "export_project", spy)
    out = tmp_path / "exported"
    run = PipelineController(config).run(PipelineRequest(project_dir=project.root, export_dir=out))

    assert calls == [(project.root, out)]
    exported = sorted(path.name for path in out.iterdir())
    assert "manifest.json" in exported
    assert "results.json" in exported
    assert "requirements.json" in exported
    stages = by_stage(run)
    assert stages[Stage.EXPORT].status is Status.PASS
    assert stages[Stage.EXPORT].artifacts


def test_a_document_path_is_ingested_before_extraction(tmp_path: Path) -> None:
    from reportlab.pdfgen import canvas

    project = make_project(tmp_path)
    write_tests_file(project, [make_case()])
    source = tmp_path / "contract.pdf"
    sheet = canvas.Canvas(str(source))
    sheet.drawString(72, 720, CONTRACT_TEXT.strip())
    sheet.showPage()
    sheet.save()
    # Ingest first, with exactly the parameters the controller uses: the request
    # re-stores the same bytes, so the stored record — and therefore the request
    # hash — is identical to the one the fixtures were authored against.
    stored = DocumentStore(project.root).add_file(
        source, doc_type="other", provenance="user_supplied", classification="unknown"
    )
    author_extraction_for(project, tmp_path / "fixtures", [stored], [make_requirement()])
    config = make_config(tmp_path / "fixtures")

    run = PipelineController(config).run(
        PipelineRequest(project_dir=project.root, document_paths=[source])
    )

    assert [record.doc_id for record in project.documents()] == [stored.doc_id]
    stages = by_stage(run)
    assert stages[Stage.COLLECT_EVIDENCE].status is Status.PASS
    assert stages[Stage.BUILD_REQUIREMENTS].status is Status.PASS
    assert stages[Stage.IDENTIFY].artifacts == []


# --------------------------------------------------------------------------- #
# repair enforcement


def test_check_repair_refuses_every_kind_that_would_weaken_the_baseline(
    tmp_path: Path,
) -> None:
    project, _config = full_project(tmp_path)
    (project.root / "models" / "candidates" / "c1").mkdir(parents=True, exist_ok=True)
    forbidden = {
        "tolerance": RepairEdit("models/candidates/c1/model.lib", "tol", "1", "2", "tolerance"),
        "test_deletion": RepairEdit(
            "models/candidates/c1/model.lib", "tests", "a", "b", "test_deletion"
        ),
        "evidence": RepairEdit("models/candidates/c1/model.lib", "ev", "a", "b", "evidence"),
        "coverage": RepairEdit("models/candidates/c1/model.lib", "cov", "a", "b", "coverage"),
        "circuit": RepairEdit("models/candidates/c1/model.lib", "cir", "a", "b", "circuit"),
    }
    for kind, edit in forbidden.items():
        with pytest.raises(RepairViolation) as excinfo:
            check_repair(project, RepairProposal(description=kind, edits=(edit,)))
        assert kind in str(excinfo.value)

    outside = RepairProposal(
        description="escape",
        edits=(RepairEdit("circuit/components.csv", "U1.value", "1", "2"),),
    )
    with pytest.raises(RepairViolation, match="models/candidates"):
        check_repair(project, outside)

    escaping = RepairProposal(
        description="escape",
        edits=(RepairEdit("models/candidates/../model.lib", "x", "1", "2"),),
    )
    with pytest.raises(RepairViolation):
        check_repair(project, escaping)


def test_apply_repair_writes_candidate_files_and_refuses_ambiguous_edits(
    tmp_path: Path,
) -> None:
    project, _config = full_project(tmp_path)
    created = RepairProposal(
        description="add a candidate",
        edits=(
            RepairEdit(
                "models/candidates/c1/model.lib", "subckt", old="", new=".subckt CAND a b\n.ends\n"
            ),
        ),
    )
    assert apply_repair(project, created) == ["models/candidates/c1/model.lib"]
    assert (
        (project.root / "models/candidates/c1/model.lib")
        .read_text(encoding="utf-8")
        .startswith(".subckt CAND")
    )

    ambiguous = RepairProposal(
        description="ambiguous",
        edits=(RepairEdit("models/candidates/c1/model.lib", "x", "NOT-PRESENT", "b"),),
    )
    with pytest.raises(RepairViolation, match="occurs 0 time"):
        apply_repair(project, ambiguous)


# --------------------------------------------------------------------------- #
# the repair loop (simulator boundary doubled)


def fake_artifact(case: TestCase, ctx) -> RunArtifacts:
    run_dir = Path(ctx.runs_dir) / f"fake_{case.test_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    deck = run_dir / "deck.cir"
    deck.write_text(DECK_TEXT, encoding="utf-8")
    return RunArtifacts(
        run_id=run_dir.name,
        test_id=case.test_id,
        scenario_id=case.scenario_id,
        run_dir=run_dir,
        deck_path=deck,
        deck_text=DECK_TEXT,
        deck_sha256=sha256_text(DECK_TEXT),
        batch=None,
        log=LogSummary(path=None),
        raw=None,
        raw_error=None,
        diagnostics=RunDiagnostics(),
        usability=RunUsability(usable=True, detail="in-process double"),
        detail="in-process double",
    )


def patch_simulator_boundary(
    monkeypatch: pytest.MonkeyPatch,
    *,
    failures: Callable[[int], int],
    status: Status = Status.FAIL,
) -> Callable[[int], None]:
    """Double the simulator boundary; return a setter for the applied-repair count."""
    applied = {"n": 0}

    def set_applied(count: int) -> None:
        applied["n"] = count

    def fake_run_deck_tests(ctx, cases, cancel=None, **kwargs):
        return [fake_artifact(case, ctx) for case in cases]

    def fake_evaluate_case(case, artifacts, requirements, **kwargs):
        position = CASE_IDS.index(case.test_id) if case.test_id in CASE_IDS else len(CASE_IDS) - 1
        failing = position < failures(applied["n"])
        verdict = status if failing else Status.PASS
        return TestResult(
            test_id=case.test_id,
            status=verdict,
            requirement_ids=list(case.requirement_ids),
            measured={"V(out)": 0.0},
            expected=case.expected.detail,
            detail="in-process double",
            run_id=artifacts.run_id,
            unknown_reason="in-process double" if verdict is Status.UNKNOWN else None,
        )

    monkeypatch.setattr(controller_module, "run_deck_tests", fake_run_deck_tests)
    monkeypatch.setattr(controller_module, "evaluate_case", fake_evaluate_case)
    return set_applied


def four_case_project(tmp_path: Path) -> tuple[Project, AppConfig]:
    cases = [make_case(test_id) for test_id in CASE_IDS]
    return full_project(tmp_path, cases=cases)


def test_repair_loop_is_capped_and_stops_with_unknown(tmp_path: Path, monkeypatch) -> None:
    project, config = four_case_project(tmp_path)
    # A real file stands in for the simulator path so the controller believes one
    # exists; run_deck_tests is doubled, so nothing is ever executed.
    config.ltspice.path = sys.executable
    set_applied = patch_simulator_boundary(
        monkeypatch, failures=lambda applied: max(4 - applied, 0)
    )
    iterations: list[int] = []

    def repair(iteration: int, results, project_arg) -> RepairProposal:
        iterations.append(iteration)
        set_applied(len(iterations))
        return RepairProposal(
            description=f"candidate {iteration}",
            edits=(
                RepairEdit(
                    file=f"models/candidates/c{iteration}/model.lib",
                    path="RDS",
                    old="",
                    new=f".param RDS={iteration}\n",
                ),
            ),
        )

    run = PipelineController(config, repair=repair).run(
        PipelineRequest(project_dir=project.root, max_repair_iterations=3)
    )

    assert iterations == [1, 2, 3], "the loop must stop at max_repair_iterations"
    stages = by_stage(run)
    assert stages[Stage.REPAIR].status is Status.UNKNOWN
    assert "repair_cap_reached" in stages[Stage.REPAIR].detail
    assert "3 iteration(s)" in stages[Stage.REPAIR].detail
    for iteration in (1, 2, 3):
        assert (project.root / f"models/candidates/c{iteration}/model.lib").is_file()
        log = json.loads(
            (project.root / f"models/candidates/c{iteration}/repair_log.json").read_text(
                encoding="utf-8"
            )
        )
        assert [entry["iteration"] for entry in log["iterations"]] == [iteration]
        assert log["iterations"][0]["files"] == [f"models/candidates/c{iteration}/model.lib"]
        assert log["iterations"][0]["applied_utc"]


def test_repair_that_fixes_every_result_reports_pass(tmp_path: Path, monkeypatch) -> None:
    project, config = four_case_project(tmp_path)
    config.ltspice.path = sys.executable
    set_applied = patch_simulator_boundary(
        monkeypatch, failures=lambda applied: 0 if applied else 4
    )

    def repair(iteration: int, results, project_arg) -> RepairProposal:
        set_applied(1)
        return RepairProposal(
            description="one candidate fixes every result",
            edits=(
                RepairEdit(
                    "models/candidates/good/model.lib", "RDS", old="", new=".param RDS=0.1\n"
                ),
            ),
        )

    run = PipelineController(config, repair=repair).run(
        PipelineRequest(project_dir=project.root, max_repair_iterations=3)
    )
    stages = by_stage(run)
    assert stages[Stage.REPAIR].status is Status.PASS
    assert "no evaluated result needs repair any more" in stages[Stage.REPAIR].detail
    assert {result.status for result in run.results} == {Status.PASS}
    # The chain still records that EVALUATE failed before the repair ran (the
    # interface defines the overall status as the worst status of the chain).
    assert stages[Stage.EVALUATE].status is Status.FAIL
    assert run.status is Status.FAIL


def test_a_repair_violation_stops_the_loop_with_unknown(tmp_path: Path, monkeypatch) -> None:
    project, config = four_case_project(tmp_path)
    config.ltspice.path = sys.executable
    patch_simulator_boundary(
        monkeypatch,
        failures=lambda applied: max(4 - applied, 0),
        status=Status.UNKNOWN,
    )

    def repair(iteration: int, results, project_arg) -> RepairProposal:
        return RepairProposal(
            description="relax the tolerance",
            edits=(
                RepairEdit(
                    "models/candidates/c1/model.lib", "tolerance", "1e-3", "1e-1", "tolerance"
                ),
            ),
        )

    # Freeze a baseline first, so the violation has something frozen to preserve.
    PipelineController(config).run(PipelineRequest(project_dir=project.root))
    baseline_before = (project.root / "baseline.json").read_bytes()
    run = PipelineController(config, repair=repair).run(
        PipelineRequest(project_dir=project.root, max_repair_iterations=3)
    )

    stages = by_stage(run)
    assert stages[Stage.REPAIR].status is Status.UNKNOWN
    assert "repair_violation" in stages[Stage.REPAIR].detail
    assert run.status is Status.UNKNOWN
    assert [finding.code for finding in run.findings] == ["repair_violation"]
    assert (project.root / "baseline.json").read_bytes() == baseline_before
    assert not (project.root / "models/candidates/c1/model.lib").exists()


def test_repair_needs_a_step_to_be_configured(tmp_path: Path, monkeypatch) -> None:
    project, config = four_case_project(tmp_path)
    config.ltspice.path = sys.executable
    patch_simulator_boundary(monkeypatch, failures=lambda applied: max(4 - applied, 0))

    run = PipelineController(config).run(
        PipelineRequest(project_dir=project.root, max_repair_iterations=3)
    )
    stages = by_stage(run)
    assert stages[Stage.REPAIR].status is Status.UNKNOWN
    assert "repair_not_configured" in stages[Stage.REPAIR].detail


def test_repair_disabled_by_the_request(tmp_path: Path) -> None:
    project, config = full_project(tmp_path)
    run = PipelineController(config).run(
        PipelineRequest(project_dir=project.root, max_repair_iterations=0)
    )
    stages = by_stage(run)
    assert stages[Stage.REPAIR].status is Status.NOT_APPLICABLE
    assert "disabled" in stages[Stage.REPAIR].detail
