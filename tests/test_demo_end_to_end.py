"""Phase 3 gate: the integrated board demonstration, end to end (plan step 10).

Everything here runs real LTspice through the same code path the CLI uses, and the
assertions are about *observable outcomes* — statuses, measured values, detection
evidence — never about the shape of the source that produced them.

The module builds the demo project once, checks it once, and re-checks it once more
from an exported copy in a fresh directory, because those are the three facts the
plan's exit gate names:

* the nominal scenario passes with real measurements;
* at least five injected faults are each detected, and the original project is
  byte-identical afterwards;
* a scenario that cannot be concluded is present as UNKNOWN/BLOCKED with a reason,
  and an exported copy reproduces the same statuses.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from boardmodeler.domain.enums import Status
from boardmodeler.pipeline.demo import build_demo_project, check_circuit, run_fault_matrix
from boardmodeler.reporting.export import export_project
from boardmodeler.simulation.ltspice import locate

pytestmark = pytest.mark.ltspice

#: The faults the report must catch; each is a real circuit mistake, not a
#: tolerance change. Kept to five so the suite stays inside a sane wall time —
#: every fault re-runs the whole check.
FAULTS = (
    "swap_straps",
    "pullup_missing",
    "pullup_wrong_domain",
    "release_reset_early",
    "missing_pg",
)


@pytest.fixture(scope="module")
def install():
    found = locate()
    if found is None:
        pytest.skip("LTspice is not installed")
    return found


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory, install):
    out = tmp_path_factory.mktemp("demo_board") / "project"
    result = build_demo_project(out, ltspice=install)
    return result


@pytest.fixture(scope="module")
def checked(demo, install, tmp_path_factory: pytest.TempPathFactory):
    """One real check, with the CLI's own result and report files written."""
    out = tmp_path_factory.mktemp("check")
    return check_circuit(
        demo.project_dir,
        ltspice=install,
        results_path=out / "results.json",
        report_path=out / "report.html",
    )


def test_the_build_produced_a_checkable_project(demo) -> None:
    assert demo.requirements, "the demo carries no requirements"
    assert demo.tests, "the demo carries no test cases"
    assert demo.decks, "the build produced no decks"
    for case in demo.tests:
        assert (demo.project_dir / case.deck_template).is_file(), (
            f"{case.test_id} references deck {case.deck_template}, which was not built"
        )


def test_static_checks_pass_except_the_declared_boundary(demo) -> None:
    failures = [f for f in demo.static_findings if f.status is Status.FAIL]
    assert not failures, "; ".join(f"{f.code}: {f.message}" for f in failures)
    unknown = [f for f in demo.static_findings if f.status is Status.UNKNOWN]
    # The reduced behavioural buck has no switching node: exactly that boundary may
    # be UNKNOWN, and it must say why.
    assert all(f.detail.get("reason") == "connectivity_not_preserved" for f in unknown), [
        f.message for f in unknown
    ]


def test_every_result_carries_an_observation_or_a_reason(checked) -> None:
    """No silent rows: a result either measured something or says why it did not."""
    for result in checked.results:
        if result.status in (Status.PASS, Status.FAIL):
            assert result.measured, (
                f"{result.test_id} reported {result.status.value} with no measurement"
            )
            assert result.run_id, f"{result.test_id} has no run id, so no artifact backs it"
        elif result.status is Status.UNKNOWN:
            assert result.unknown_reason, f"{result.test_id} is UNKNOWN with no reason"
        elif result.status is Status.BLOCKED:
            assert result.blocked_reason, f"{result.test_id} is BLOCKED with no reason"


def test_the_nominal_scenario_passes_with_real_measurements(checked) -> None:
    nominal = [r for r in checked.results if "nominal_startup" in r.test_id]
    assert nominal, f"no nominal_startup result among {[r.test_id for r in checked.results]}"
    passed = [r for r in nominal if r.status is Status.PASS]
    assert passed, "; ".join(f"{r.test_id}: {r.status.value} {r.detail}" for r in nominal)
    rails = [
        value
        for result in passed
        for name, value in result.measured.items()
        if name.startswith("max(V(") and isinstance(value, (int, float))
    ]
    assert rails, "no rail measurement was recorded for the nominal scenario"
    assert max(rails) > 2.0, f"the rails never came up: {passed[0].measured}"


def test_unresolved_requirements_are_visible_as_unknown_or_blocked(checked) -> None:
    """The fixture's clock is an assumption, so nothing depending on it may pass."""
    statuses = {result.status for result in checked.results}
    unresolved = [
        result for result in checked.results if result.status in (Status.UNKNOWN, Status.BLOCKED)
    ]
    assert unresolved, (
        "no UNKNOWN/BLOCKED result at all — the clock-availability assumption and the "
        f"unmodelled switch boundary must produce one (statuses seen: {statuses})"
    )
    for result in unresolved:
        reason = result.unknown_reason or result.blocked_reason
        assert reason and reason != "unknown", f"{result.test_id} is unresolved without a reason"
    coverage = checked.coverage or {}
    assert coverage, "the check produced no coverage summary"


def test_injected_faults_are_detected_and_the_original_is_untouched(demo, install) -> None:
    report = run_fault_matrix(demo.project_dir, ltspice=install, faults=FAULTS)
    assert report["original_unchanged"] is True, (
        f"a mutation leaked into the original project: {report['original_hashes']}"
    )
    assert report["total"] == len(FAULTS)
    missed = [entry["fault_id"] for entry in report["faults"] if not entry["detected"]]
    assert not missed, "these injected circuit mistakes were not detected: " + "; ".join(
        f"{entry['fault_id']} ({entry['description']}: {entry['status']} {entry['summary']})"
        for entry in report["faults"]
        if not entry["detected"]
    )
    for entry in report["faults"]:
        assert entry["evidence"], f"{entry['fault_id']} was 'detected' with no evidence"
        assert entry["modifications"], f"{entry['fault_id']} recorded no edit"
        # A detected fault means the check produced the *expected* outcome for it,
        # which the mutator declares; the evidence must name where it showed up.
        assert entry["expected_detection"]
        assert any(entry["evidence"]), entry["evidence"]


def test_an_export_reproduces_the_same_statuses_in_a_fresh_directory(
    demo, checked, tmp_path: Path, install
) -> None:
    project = demo.project_dir
    export_dir = tmp_path / "export"
    result = export_project(
        _as_project(project),
        export_dir,
        requirements=demo.requirements,
        tests=demo.tests,
        results=list(checked.results),
    )
    assert result.ok, "; ".join(f"{f.code}: {f.message}" for f in result.findings)
    for relative in result.relative_files():
        assert not Path(relative).is_absolute(), f"{relative} is an absolute path"

    rerun_root = tmp_path / "rerun"
    shutil.copytree(export_dir, rerun_root)
    rerun = check_circuit(rerun_root, ltspice=install)
    before = {r.test_id: r.status.value for r in checked.results}
    after = {r.test_id: r.status.value for r in rerun.results}
    assert after == before, f"the exported copy disagreed with the original: {before} vs {after}"


def _as_project(root: Path):
    from boardmodeler.pipeline.project import Project

    return Project(root)


def test_the_published_results_match_the_in_memory_check(checked) -> None:
    """The JSON and HTML the CLI writes must agree with the result it returned."""
    assert checked.results_path is not None and checked.results_path.is_file()
    payload = json.loads(checked.results_path.read_text(encoding="utf-8"))
    written = {item["test_id"]: item["status"] for item in payload["results"]}
    assert written == {r.test_id: r.status.value for r in checked.results}
    assert payload["summary"] == checked.summary()
    assert checked.report_path is not None
    assert checked.report_path.is_file() and checked.report_path.stat().st_size > 0
    assert checked.report_path.read_text(encoding="utf-8").lstrip().lower().startswith("<!doctype")
