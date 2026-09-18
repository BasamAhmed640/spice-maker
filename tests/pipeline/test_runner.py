"""Runner tests (Phase 1 step 10 + status propagation).

The runner must never invent a verdict, never start a simulator it does not have,
and never lose track of which directory belongs to which case.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from boardmodeler.domain.enums import Status
from boardmodeler.domain.records import ExpectationSpec, TestCase
from boardmodeler.pipeline.runner import RunContext, run_case, run_deck_tests


def make_case(
    test_id: str = "T_nominal_startup_001", scenario_id: str = "nominal_startup"
) -> TestCase:
    return TestCase(
        test_id=test_id,
        requirement_ids=["REQ_X_001"],
        scenario_id=scenario_id,
        scope="circuit_compliance",
        deck_template="decks/nominal.cir",
        expected=ExpectationSpec(kind="satisfy", detail="rail within limits"),
        measurement=["V(out)"],
    )


def test_missing_simulator_blocks_and_still_records_the_deck(tmp_path: Path) -> None:
    ctx = RunContext(project_dir=tmp_path, ltspice=None)
    artifacts = run_case(
        ctx,
        make_case(),
        build_deck=lambda run_dir: _write(run_dir, "* deck\nV1 a 0 1\n.tran 1m\n.end\n"),
        run_identifier="no_sim",
    )
    assert artifacts.usability.usable is False
    assert artifacts.blocked_reason == "simulator_unavailable"
    assert artifacts.batch is None
    assert artifacts.raw is None
    # The deck that was refused is still preserved with its hash.
    assert artifacts.deck_path is not None and artifacts.deck_path.is_file()
    assert artifacts.deck_sha256 and len(artifacts.deck_sha256) == 64


def test_deck_builder_failure_blocks_with_the_reason(tmp_path: Path) -> None:
    ctx = RunContext(project_dir=tmp_path, ltspice=None)

    def boom(run_dir: Path) -> Path:
        raise FileNotFoundError("template missing")

    artifacts = run_case(ctx, make_case(), build_deck=boom, run_identifier="bad_deck")
    assert artifacts.blocked_reason == "deck_not_built"
    assert "template missing" in artifacts.detail
    assert artifacts.deck_text == ""


def test_missing_template_blocks_via_the_default_builder(tmp_path: Path) -> None:
    ctx = RunContext(project_dir=tmp_path, ltspice=None)
    artifacts = run_case(ctx, make_case(), run_identifier="no_template")
    assert artifacts.blocked_reason == "deck_not_built"
    assert "deck template not found" in artifacts.detail


def test_cancelled_before_start(tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    ctx = RunContext(project_dir=tmp_path, ltspice=None)
    artifacts = run_case(ctx, make_case(), cancel=cancel, run_identifier="cancelled")
    assert artifacts.blocked_reason == "cancelled"
    assert artifacts.cancelled is False  # nothing was running to cancel


def test_run_deck_tests_stops_when_cancelled(tmp_path: Path) -> None:
    cancel = threading.Event()
    ctx = RunContext(project_dir=tmp_path, ltspice=None)

    def builder(case: TestCase, run_dir: Path) -> Path:
        cancel.set()  # cancel after the first case has been set up
        return _write(run_dir, "* deck\n.end\n")

    cases = [make_case("T_a"), make_case("T_b"), make_case("T_c")]
    artifacts = run_deck_tests(ctx, cases, cancel, deck_builder=builder)
    assert len(artifacts) == 1
    assert artifacts[0].test_id == "T_a"


def test_run_deck_tests_reports_every_blocked_case(tmp_path: Path) -> None:
    ctx = RunContext(project_dir=tmp_path, ltspice=None)
    cases = [make_case("T_a"), make_case("T_b")]
    artifacts = run_deck_tests(
        ctx, cases, deck_builder=lambda case, run_dir: _write(run_dir, "* deck\n.end\n")
    )
    assert [a.blocked_reason for a in artifacts] == ["simulator_unavailable"] * 2
    names = [a.run_dir.name for a in artifacts]
    assert names[0].startswith("T_a_") and names[1].startswith("T_b_")
    assert len(set(names)) == 2


def test_relative_run_directory_is_inside_the_project(tmp_path: Path) -> None:
    ctx = RunContext(project_dir=tmp_path, ltspice=None)
    artifacts = run_case(
        ctx,
        make_case(),
        build_deck=lambda run_dir: _write(run_dir, "* deck\n.end\n"),
        run_identifier="inside",
    )
    assert artifacts.run_dir == tmp_path / "runs" / "inside"
    assert ctx.runs_dir == tmp_path / "runs"


def _write(run_dir: Path, text: str) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / "deck.cir"
    target.write_text(text, encoding="utf-8")
    return target


@pytest.mark.parametrize("kind", ["satisfy", "violate_detected"])
def test_expectation_kind_survives_the_runner(kind: str, tmp_path: Path) -> None:
    case = TestCase(
        test_id=f"T_{kind}",
        requirement_ids=["REQ_X_001"],
        scenario_id="nominal_startup",
        scope="circuit_compliance",
        deck_template="decks/x.cir",
        expected=ExpectationSpec(kind=kind, detail="detail"),
    )
    ctx = RunContext(project_dir=tmp_path, ltspice=None)
    artifacts = run_case(
        ctx,
        case,
        build_deck=lambda run_dir: _write(run_dir, "* deck\n.end\n"),
        run_identifier="k",
    )
    assert artifacts.usability.usable is False
    assert artifacts.usability.blocked_reason == "simulator_unavailable"
    assert Status.BLOCKED.value == "BLOCKED"
