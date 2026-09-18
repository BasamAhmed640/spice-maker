"""HTML report tests (Phase 3 step 9 contract).

The report is the artifact a hardware engineer reads under time pressure, so the
tests pin the things that matter: findings come first, critical failures outrank
passes, violation markers carry their requirement id, measured values are shown,
and coverage lists what is *not* covered.
"""

from __future__ import annotations

import numpy as np

from boardmodeler.domain.enums import Criticality, Status
from boardmodeler.domain.records import Finding, TestResult
from boardmodeler.reporting.html import (
    ReportInputs,
    ViolationMarker,
    render_report,
    traces_from_raw,
    write_report,
)
from boardmodeler.simulation.raw import RawFile


def make_raw() -> RawFile:
    t = np.linspace(0.0, 1e-3, 500)
    out = np.where(t > 4e-4, 3.3, 0.0) + 0.05 * np.sin(t * 2e6)
    return RawFile(
        path=None,
        plotname="Transient Analysis",
        flags=["real"],
        variables=["time", "V(3V3)"],
        variable_types=["time", "voltage"],
        data=np.column_stack([t, out]),
        points_per_step=[t.size],
    )


def test_report_puts_findings_first_and_orders_by_severity() -> None:
    inputs = ReportInputs(
        project_id="prj_demo",
        project_name="Demo board",
        status=Status.FAIL,
        findings=[
            Finding(code="SC009_supply_domain_assignment", status=Status.PASS, message="ok"),
            Finding(
                code="SC009_supply_domain_assignment",
                status=Status.FAIL,
                refdes="U3",
                nets=["1V8_PG"],
                message="PG pull-up on the wrong domain",
                detail={"pin": "PG", "domain": "1V8", "net": "3V3"},
            ),
        ],
        results=[
            TestResult(
                test_id="T_nominal_startup_001",
                status=Status.PASS,
                requirement_ids=["REQ_A"],
                measured={"min(V(3V3))": 3.3021},
                expected="rail within limits",
            ),
            TestResult(
                test_id="T_reset_early_release_001",
                status=Status.FAIL,
                requirement_ids=["REQ_B"],
                measured={"reset_release_s": 1.1e-3},
                expected="reset releases after both rails",
            ),
        ],
        coverage={"requirements_total": 2, "not_dynamically_covered": [{"req_id": "REQ_C"}]},
        limitations=["thermal dependence is not modelled"],
        reproduction=["uv run boardmodeler circuit check --project . --circuit circuit/demo.asc"],
        generated_utc="2026-09-18T00:00:00Z",
        simulator="LTspice 26.0.0.3",
        tool_version="0.1.0",
    )
    html = render_report(inputs)

    assert html.index("<h2>Findings</h2>") < html.index("<h2>Results</h2>")
    assert html.index("<h2>Results</h2>") < html.index("<h2>Coverage</h2>")
    assert html.index("<h2>Coverage</h2>") < html.index("<h2>Reproduction</h2>")
    # The failing finding appears before the passing one.
    assert html.index("PG pull-up on the wrong domain") < html.index(">ok<")
    assert "U3" in html and "1V8_PG" in html
    # Measured values are shown, not just statuses.
    assert "3.3021" in html and "reset_release_s" in html
    # Coverage names the requirement with no test.
    assert "REQ_C" in html
    assert "thermal dependence is not modelled" in html
    assert "boardmodeler circuit check" in html
    assert "overall status: FAIL" in html


def test_report_marks_violations_on_the_waveform() -> None:
    raw = make_raw()
    markers = {
        "V(3V3)": [
            ViolationMarker(requirement_id="REQ_RESET_001", t_s=2e-4, label="reset released early"),
            ViolationMarker(requirement_id="REQ_FAR", t_s=9.0, label="outside the window"),
        ]
    }
    traces = traces_from_raw(raw, ["V(3V3)", "V(missing)"], markers)
    assert [trace.name for trace in traces] == ["V(3V3)"]
    html = render_report(
        ReportInputs(
            project_id="prj_demo",
            status=Status.FAIL,
            traces=traces,
            results=[],
        )
    )
    assert "REQ_RESET_001" in html
    assert html.count("<line ") == 1  # the out-of-window marker is not drawn
    assert "waveform of V(3V3)" in html
    assert "<polyline" in html


def test_waveform_svg_keeps_extremes_when_downsampling() -> None:
    t = np.linspace(0.0, 1e-3, 20000)
    values = np.zeros_like(t)
    values[12345] = 12.0  # a single-sample spike
    raw = RawFile(
        path=None,
        plotname="Transient Analysis",
        flags=["real"],
        variables=["time", "V(spike)"],
        variable_types=["time", "voltage"],
        data=np.column_stack([t, values]),
        points_per_step=[t.size],
    )
    html = render_report(
        ReportInputs(project_id="prj", traces=traces_from_raw(raw, ["V(spike)"]), results=[])
    )
    assert "12" in html
    assert "waveform of V(spike)" in html


def test_write_report_creates_the_file(tmp_path) -> None:
    path = write_report(tmp_path / "report.html", ReportInputs(project_id="prj", results=[]))
    assert path.is_file()
    assert "BoardModeler report" in path.read_text(encoding="utf-8")


def test_report_with_no_data_says_so_instead_of_looking_green() -> None:
    html = render_report(ReportInputs(project_id="prj", status=Status.UNKNOWN))
    assert "No findings were recorded." in html
    assert "No tests were executed." in html
    assert "No coverage data was recorded." in html
    assert "overall status: UNKNOWN" in html
    assert Criticality is not None
