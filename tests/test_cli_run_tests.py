"""`boardmodeler run tests` end-to-end (Phase 1 step 11).

Builds a real project on disk, runs it through the CLI against real LTspice, and
checks both that the honest result appears in the JSON and that the artifacts
(deck/raw/log) it names are actually there.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from boardmodeler.domain.enums import (
    Criticality,
    EvidenceExtraction,
    RequirementClass,
    RequirementKind,
    RequirementOrigin,
)
from boardmodeler.domain.records import (
    EvidenceRef,
    ExpectationSpec,
    PageRef,
    Requirement,
    TestCase,
)
from boardmodeler.pipeline.project import Project, create_project

pytestmark = pytest.mark.ltspice

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_SOURCE = REPO_ROOT / "tests" / "decks" / "divider.cir"

DECK_VALUES = {
    "vin": 12.0,
    "r_upper": 26.364e3,
    "r_lower": 10e3,
    "c_out": 10e-6,
    "tstop": 2e-3,
    "tmax": 2e-6,
}

REQUIREMENT = Requirement(
    req_id="REQ_DIV_ELEC_001",
    applies_to="U1",
    kind=RequirementKind.ELECTRICAL,
    **{
        "class": RequirementClass.DERIVED_VALUE,
        "criticality": Criticality.CRITICAL,
        "origin": RequirementOrigin.TEST_FIXTURE,
        "statement": "The divided rail settles at 3.3 V.",
        "limits": {"min": 3.234, "typ": 3.3, "max": 3.366, "unit": "V"},
        "expression": {
            "op": "between",
            "signal": "V(out)",
            "low": 3.234,
            "high": 3.366,
            "unit": "V",
            "interval": {"start_s": 1e-3, "end_s": 2e-3},
        },
        "evidence": [
            EvidenceRef(
                doc_id="doc_synthetic_divider",
                page=PageRef(pdf_page=0),
                section="Synthetic contract",
                excerpt="V(out) = V(in) * R2 / (R1 + R2).",
                extraction=EvidenceExtraction.SYNTHETIC_FIXTURE,
            )
        ],
        "citation_verified": True,
        "status": "active",
    },
)


def build_project(root: Path, *, rail_off: bool = False) -> Project:
    project = create_project(
        root, project_id="prj_divider", name="Divider project", supply_domains={"out": "3V3"}
    )
    (root / "evidence").mkdir(exist_ok=True)
    r_upper = 30e3 if rail_off else 26.364e3
    template = (REPO_ROOT / "tests" / "decks" / "divider.cir").read_text(encoding="utf-8")
    rendered = template
    values = {**DECK_VALUES, "r_upper": r_upper}
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", f"{value:g}")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "divider.cir").write_text(rendered, encoding="utf-8")

    (root / "evidence" / "requirements.json").write_text(
        json.dumps(
            {"requirements": [json.loads(REQUIREMENT.model_dump_json(by_alias=True))]}, indent=2
        ),
        encoding="utf-8",
    )
    case = TestCase(
        test_id="T_divider_nominal",
        requirement_ids=[REQUIREMENT.req_id],
        scenario_id="nominal_startup",
        scope="circuit_compliance",
        deck_template="tests/divider.cir",
        expected=ExpectationSpec(kind="satisfy", detail="V(out) within 3.234-3.366 V"),
        measurement=["V(out)"],
    )
    (root / "tests" / "tests.json").write_text(
        json.dumps({"tests": [json.loads(case.model_dump_json())]}, indent=2), encoding="utf-8"
    )
    return project


def run_cli(
    args: list[str], *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO_ROOT / "src")
    if env:
        environment.update(env)
    return subprocess.run(
        [sys.executable, "-m", "boardmodeler.cli", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=environment,
        timeout=600,
        check=False,
    )


def test_list_tests_does_not_run_anything(tmp_path: Path) -> None:
    build_project(tmp_path)
    proc = run_cli(["run", "tests", "--project", str(tmp_path), "--list-tests", "--json"])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload[0]["test_id"] == "T_divider_nominal"
    assert payload[0]["scenario_id"] == "nominal_startup"
    assert not (tmp_path / "runs").glob("*/deck.raw") or not list(
        (tmp_path / "runs").glob("*/deck.raw")
    )


def test_run_tests_reports_pass_with_artifacts(tmp_path: Path) -> None:
    build_project(tmp_path)
    out = tmp_path / "results.json"
    proc = run_cli(
        ["run", "tests", "--project", str(tmp_path), "--json", "--out", str(out), "--strict"]
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["summary"] == {"PASS": 1}
    row = payload["results"][0]
    assert row["result"]["status"] == "PASS"
    assert row["result"]["measured"]["min(V(out))"] == pytest.approx(3.3, abs=0.02)
    assert row["result"]["run_id"] == row["run"]["run_id"]

    # The artifacts the result names must exist, with the hashes it claims.
    import hashlib

    raw = tmp_path / "runs" / row["run"]["run_id"] / "deck.raw"
    log = tmp_path / "runs" / row["run"]["run_id"] / "deck.log"
    assert raw.is_file() and log.is_file()
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == row["run"]["raw_sha256"]
    assert hashlib.sha256(log.read_bytes()).hexdigest() == row["run"]["log_sha256"]
    assert json.loads(out.read_text(encoding="utf-8"))["summary"] == {"PASS": 1}


def test_run_tests_reports_failure_for_a_wrong_divider(tmp_path: Path) -> None:
    build_project(tmp_path, rail_off=True)
    proc = run_cli(["run", "tests", "--project", str(tmp_path), "--json"])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["summary"] == {"FAIL": 1}
    assert payload["results"][0]["result"]["measured"]["max(V(out))"] == pytest.approx(
        3.0, abs=0.02
    )
    # --strict turns a non-PASS into a non-zero exit for CI use.
    strict = run_cli(["run", "tests", "--project", str(tmp_path), "--strict"])
    assert strict.returncode == 1


def test_missing_project_is_an_error(tmp_path: Path) -> None:
    proc = run_cli(["run", "tests", "--project", str(tmp_path / "nope")])
    assert proc.returncode == 2
    assert "project.json" in proc.stdout


def test_no_matching_tests_is_an_error(tmp_path: Path) -> None:
    build_project(tmp_path)
    proc = run_cli(["run", "tests", "--project", str(tmp_path), "--scope", "fault_detection"])
    assert proc.returncode == 1
    assert "no test cases matched" in proc.stdout


def test_human_output_lists_results(tmp_path: Path) -> None:
    build_project(tmp_path)
    proc = run_cli(["run", "tests", "--project", str(tmp_path)])
    assert proc.returncode == 0, proc.stderr
    assert "PASS" in proc.stdout
    assert "T_divider_nominal" in proc.stdout
    assert "summary:" in proc.stdout


def test_project_freeze_baseline_is_preferred_over_working_files(tmp_path: Path) -> None:
    """A judgement must be reproducible from the frozen baseline."""
    project = build_project(tmp_path)
    frozen = project.freeze_baseline(notes=["frozen by test"])
    assert frozen.requirement_baseline_hash
    assert project.baseline_path.is_file()

    # Change the working requirement file and make sure evaluation still uses
    # the frozen expression.
    (tmp_path / "evidence" / "requirements.json").write_text(
        json.dumps({"requirements": []}), encoding="utf-8"
    )
    requirements = project.requirements()
    assert REQUIREMENT.req_id in requirements
    assert requirements[REQUIREMENT.req_id].expression is not None

    proc = run_cli(["run", "tests", "--project", str(tmp_path), "--json"])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["summary"] == {"PASS": 1}


def test_copied_project_reruns_identically(tmp_path: Path) -> None:
    """Copying the project to a fresh directory must reproduce the same statuses."""
    build_project(tmp_path / "original")
    first = json.loads(
        run_cli(["run", "tests", "--project", str(tmp_path / "original"), "--json"]).stdout
    )["summary"]
    shutil.copytree(tmp_path / "original", tmp_path / "copy")
    second = json.loads(
        run_cli(["run", "tests", "--project", str(tmp_path / "copy"), "--json"]).stdout
    )["summary"]
    assert first == second == {"PASS": 1}
