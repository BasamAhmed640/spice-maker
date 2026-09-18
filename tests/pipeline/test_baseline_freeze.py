"""Baseline freeze (D10, ``docs/INTERFACES.md`` §1).

The frozen baseline is the file every verdict is judged against, so these tests
pin the properties that make it meaningful: it is written before any repair
runs, a requirement or scope change is a *visible revision* (version bump plus a
review question), an unchanged run is idempotent, and no repair can touch it.

The project fixtures are shared with ``test_controller`` (same directory), which
keeps one definition of the extraction fixtures and the deck templates.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from boardmodeler.documents.store import DocumentStore
from boardmodeler.domain.enums import Status
from boardmodeler.domain.records import EvidenceRef, PageRef, Requirement, TestCase
from boardmodeler.pipeline.controller import (
    STAGE_ORDER,
    PipelineController,
    PipelineRequest,
    RepairEdit,
    RepairProposal,
    RepairViolation,
    Stage,
    check_repair,
)
from boardmodeler.pipeline.project import Project


def _load_sibling(name: str):
    """Load a sibling test module by path.

    pytest's import layout decides whether this directory lands on ``sys.path``
    (it depends on which ``__init__.py`` files exist above us), so the shared
    project fixtures are loaded explicitly instead of relying on that.
    """
    import importlib.util
    import sys

    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_pipeline_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_fixtures = _load_sibling("test_controller")
author_extraction_for = _fixtures.author_extraction_for
by_stage = _fixtures.by_stage
full_project = _fixtures.full_project
make_case = _fixtures.make_case
make_config = _fixtures.make_config
make_project = _fixtures.make_project
make_requirement = _fixtures.make_requirement
patch_simulator_boundary = _fixtures.patch_simulator_boundary
write_tests_file = _fixtures.write_tests_file


def run_once(project: Project, config, **kwargs):
    return PipelineController(config).run(PipelineRequest(project_dir=project.root, **kwargs))


def document_requirement(req_id: str, excerpt: str, doc_id: str) -> Requirement:
    return Requirement(
        req_id=req_id,
        applies_to="device",
        kind="ELECTRICAL",
        req_class="DOCUMENTED_LIMIT",
        criticality="CRITICAL",
        origin="DOCUMENT",
        statement="the input voltage range is 4.5 V to 60 V",
        evidence=[
            EvidenceRef(
                doc_id=doc_id,
                page=PageRef(pdf_page=0),
                excerpt=excerpt,
                extraction="embedded_text",
            )
        ],
    )


def test_the_baseline_is_frozen_before_any_repair_runs(tmp_path: Path) -> None:
    project, config = full_project(tmp_path)
    run = run_once(project, config)

    assert STAGE_ORDER.index(Stage.FREEZE_BASELINE) < STAGE_ORDER.index(Stage.REPAIR)

    stages = by_stage(run)
    assert stages[Stage.FREEZE_BASELINE].status is Status.PASS
    assert stages[Stage.REPAIR].status is Status.NOT_APPLICABLE

    baseline = project.baseline()
    assert baseline is not None
    assert baseline.baseline_version == 1
    assert baseline.requirement_baseline_hash == baseline.compute_hash()
    assert baseline.test_hash
    assert baseline.frozen_utc.endswith("Z")
    assert "frozen before repair" in baseline.notes[0]
    assert project.requirements_path.is_file()
    # The frozen set — not a stale working file — is what the project reports.
    assert project.requirements()["REQ_VOUT"].req_id == "REQ_VOUT"


def test_an_unchanged_rerun_does_not_bump_the_version(tmp_path: Path) -> None:
    project, config = full_project(tmp_path)
    run_once(project, config)
    first = project.baseline()
    assert first is not None

    second_run = run_once(project, config)
    again = project.baseline()
    assert again is not None
    assert again.baseline_version == first.baseline_version
    assert again.requirement_baseline_hash == first.requirement_baseline_hash
    assert second_run.review_items == []


def test_a_changed_requirement_set_is_a_visible_revision(tmp_path: Path) -> None:
    project, config = full_project(tmp_path)
    run_once(project, config)
    first = project.baseline()
    assert first is not None

    # A corrected contract arrives: a second document changes the snippets, so the
    # extraction request (and its fixtures) change with it.
    DocumentStore(project.root).add_synthetic(
        "contract-v2.txt", "Synthetic contract v2. The output voltage is 3.6 V nominal.\n"
    )
    records = sorted(project.documents(), key=lambda record: record.doc_id)
    author_extraction_for(
        project, tmp_path / "proj-fixtures", records, [make_requirement(value=3.6)]
    )
    run = run_once(project, config)

    second = project.baseline()
    assert second is not None
    assert second.baseline_version == 2
    assert second.requirement_baseline_hash != first.requirement_baseline_hash

    review = {item.id: item for item in project.review_items()}
    assert "RV_baseline_v2" in review
    item = review["RV_baseline_v2"]
    assert item.kind == "ambiguity"
    assert item.blocking is False
    assert item.affected_requirement_ids == []
    assert "changed since baseline v1" in item.question
    assert [entry.id for entry in run.review_items] == ["RV_baseline_v2"]
    assert "baseline_version bumped" in by_stage(run)[Stage.FREEZE_BASELINE].detail


def test_a_scope_change_is_a_visible_revision(tmp_path: Path) -> None:
    project, config = full_project(tmp_path, cases=[make_case("T_A"), make_case("T_B")])
    run_once(project, config)
    first = project.baseline()
    assert first is not None
    assert [case.test_id for case in first.tests] == ["T_A", "T_B"]

    run = run_once(project, config, test_ids=["T_A"])
    second = project.baseline()
    assert second is not None
    assert second.baseline_version == 2
    assert [case.test_id for case in second.tests] == ["T_A"]
    assert [item.id for item in run.review_items] == ["RV_baseline_v2"]


def test_a_repair_cannot_touch_the_frozen_baseline(tmp_path: Path, monkeypatch) -> None:
    project, config = full_project(tmp_path)
    run_once(project, config)
    frozen = (project.root / "baseline.json").read_bytes()

    # A repair that names the baseline is refused before anything is written...
    with pytest.raises(RepairViolation, match="models/candidates"):
        check_repair(
            project,
            RepairProposal(
                description="rewrite the baseline",
                edits=(RepairEdit("baseline.json", "requirements", "a", "b"),),
            ),
        )

    # ...and a repair that succeeds cannot change what the frozen baseline says.
    config.ltspice.path = sys.executable
    set_applied = patch_simulator_boundary(
        monkeypatch, failures=lambda applied: 0 if applied else 4
    )

    def repair(iteration: int, results, project_arg) -> RepairProposal:
        set_applied(1)
        return RepairProposal(
            description="tune the candidate model",
            edits=(RepairEdit("models/candidates/c1/model.lib", "RDS", "", ".param RDS=0.2\n"),),
        )

    PipelineController(config, repair=repair).run(
        PipelineRequest(project_dir=project.root, max_repair_iterations=2)
    )
    assert (project.root / "baseline.json").read_bytes() == frozen
    baseline = project.baseline()
    assert baseline is not None and baseline.baseline_version == 1
    assert (project.root / "models/candidates/c1/model.lib").is_file()


def test_only_the_reviewed_requirements_are_frozen(tmp_path: Path) -> None:
    """A citation that cannot be verified is frozen as unverified, and asked about."""
    project = make_project(tmp_path)
    document = DocumentStore(project.root).add_synthetic(
        "contract.txt", "Input voltage range: 4.5 V to 60 V.\n"
    )
    good = document_requirement("REQ_GOOD", "Input voltage range: 4.5 V to 60 V.", document.doc_id)
    bad = document_requirement(
        "REQ_BAD", "an invented excerpt that is not on the page", document.doc_id
    )
    cases: list[TestCase] = [make_case("T_GOOD", ("REQ_GOOD",))]
    write_tests_file(project, cases)

    author_extraction_for(project, tmp_path / "fixtures", [document], [good, bad])
    config = make_config(tmp_path / "fixtures")

    run = run_once(project, config)

    baseline = project.baseline()
    assert baseline is not None
    frozen = {requirement.req_id: requirement for requirement in baseline.requirements}
    assert frozen["REQ_GOOD"].citation_verified is True
    assert frozen["REQ_BAD"].citation_verified is False

    stages = by_stage(run)
    assert stages[Stage.REVIEW_SOURCES].status is Status.UNKNOWN
    assert "blocking" in stages[Stage.REVIEW_SOURCES].detail
    assert any(item.kind == "missing_evidence" for item in run.review_items)
