"""BoardModeler command line interface.

The CLI is the automation/test surface: it must be usable in scripts and CI, so
every command that reports a result also supports ``--json`` and exits non-zero
only when the request itself failed (a FAIL status is data, not a crash).

Commands are registered as the phases land; ``doctor`` is the environment
surface and reports *only* observed facts (nothing is assumed about the machine).
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import sys
import tempfile
from pathlib import Path
from typing import Any

from boardmodeler import __version__
from boardmodeler.config import config_path, load_config
from boardmodeler.simulation.backend import probe_backend
from boardmodeler.simulation.ltspice import (
    BATCH_RESOLUTION_NOTES,
    default_lib_dir,
    locate_outcome,
    smoke_test,
)
from boardmodeler.simulation.ltspice import (
    version as ltspice_version,
)

__all__ = ["build_parser", "doctor_payload", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="boardmodeler",
        description=(
            "Datasheet-grounded LTspice model acquisition plus schematic-level "
            "bring-up verification."
        ),
    )
    parser.add_argument("--version", action="version", version=f"boardmodeler {__version__}")
    sub = parser.add_subparsers(dest="command", required=False)

    doctor = sub.add_parser(
        "doctor",
        help="report the environment: LTspice, reader backend, OCR, credentials",
    )
    doctor.add_argument("--json", action="store_true", help="machine-readable output")
    doctor.add_argument(
        "--no-smoke",
        action="store_true",
        help="skip the LTspice smoke test (reports smoke_test=null, never 'pass')",
    )
    doctor.add_argument(
        "--smoke-workdir",
        type=Path,
        default=None,
        help="directory for the smoke artifacts (default: a fresh temp directory)",
    )

    version_cmd = sub.add_parser("version", help="print the version")
    version_cmd.add_argument("--json", action="store_true")

    setup_cmd = sub.add_parser(
        "setup", help="run the retro setup wizard (LTspice, provider, data policy)"
    )
    setup_cmd.add_argument(
        "--json",
        action="store_true",
        help="walk every step with the default answers and print the observed outcome",
    )
    setup_cmd.add_argument("--project", type=Path, default=None, help="project directory to record")

    ui_cmd = sub.add_parser("ui", help="launch the desktop application")
    ui_cmd.add_argument("--project", type=Path, default=None, help="project directory to open")
    ui_cmd.add_argument(
        "--installer", action="store_true", help="launch the setup wizard instead of the app"
    )

    run = sub.add_parser("run", help="execute project work")
    run_sub = run.add_subparsers(dest="run_command", required=True)
    run_tests = run_sub.add_parser(
        "tests", help="run the project's test cases against the simulator"
    )
    run_tests.add_argument("--project", type=Path, required=True, help="project directory")
    run_tests.add_argument("--scope", default=None, help="only cases in this scope")
    run_tests.add_argument(
        "--test",
        action="append",
        dest="test_ids",
        default=None,
        help="only these test ids (repeatable)",
    )
    run_tests.add_argument("--list-tests", action="store_true", help="list cases and exit")
    run_tests.add_argument("--json", action="store_true", help="machine-readable output")
    run_tests.add_argument("--out", type=Path, default=None, help="write results JSON here")
    run_tests.add_argument(
        "--timeout", type=float, default=None, help="per-case simulator timeout in seconds"
    )
    run_tests.add_argument("--ltspice", type=Path, default=None, help="explicit LTspice executable")
    run_tests.add_argument(
        "--ascii-raw",
        action="store_true",
        help="write .raw in ASCII (more precise float rendering, larger files)",
    )
    run_tests.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 when any result is not PASS (statuses are data otherwise)",
    )

    run_mutations = run_sub.add_parser(
        "mutations", help="inject every fault into its own copy and record detection"
    )
    run_mutations.add_argument("--project", type=Path, required=True, help="project directory")
    run_mutations.add_argument(
        "--report", type=Path, required=True, help="where to write the report"
    )
    run_mutations.add_argument(
        "--fault", action="append", default=None, help="only these faults (repeatable)"
    )
    run_mutations.add_argument("--json", action="store_true")

    demo = sub.add_parser("demo", help="the integrated board-level demonstration")
    demo_sub = demo.add_subparsers(dest="demo_command", required=True)
    demo_build = demo_sub.add_parser("build", help="assemble the demo project from the fixtures")
    demo_build.add_argument("--out", type=Path, required=True, help="project directory to create")
    demo_build.add_argument("--json", action="store_true")
    demo_build.add_argument(
        "--no-probe", action="store_true", help="skip capability probing (faster, less evidence)"
    )

    circuit = sub.add_parser("circuit", help="circuit-level checks")
    circuit_sub = circuit.add_subparsers(dest="circuit_command", required=True)
    circuit_check = circuit_sub.add_parser(
        "check", help="static checks plus the dynamic scenarios against a built project"
    )
    circuit_check.add_argument("--project", type=Path, required=True)
    circuit_check.add_argument(
        "--circuit", type=Path, default=None, help="schematic to netlist-check"
    )
    circuit_check.add_argument("--scope", default=None, help="only cases in this scope")
    circuit_check.add_argument(
        "--fault-matrix", action="store_true", help="also run the fault matrix"
    )
    circuit_check.add_argument("--json", action="store_true")
    circuit_check.add_argument("--out", type=Path, default=None, help="results JSON path")
    circuit_check.add_argument("--report", type=Path, default=None, help="HTML report path")
    circuit_check.add_argument(
        "--strict", action="store_true", help="exit 1 when the overall status is not PASS"
    )

    export = sub.add_parser("export", help="write the portable model/test export")
    export.add_argument("--project", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--json", action="store_true")
    export.add_argument(
        "--model", default=None, help="model id to export (default: first generated)"
    )

    extract = sub.add_parser(
        "extract", help="extract requirements from a document through a provider"
    )
    extract.add_argument("--project", type=Path, required=True)
    extract.add_argument("--doc", type=Path, default=None, help="document to ingest first")
    extract.add_argument(
        "--provider", default=None, help="provider name (fixture, http_inference, bob_direct)"
    )
    extract.add_argument("--allow-remote", action="store_true", help="permit remote inference")
    extract.add_argument("--json", action="store_true")
    return parser


def _ltspice_section(*, run_smoke: bool, smoke_workdir: Path | None) -> dict[str, Any]:
    config = load_config()
    explicit = config.ltspice.path
    outcome = locate_outcome(explicit)
    install = outcome.install

    section: dict[str, Any] = {
        "found": install is not None,
        "path": str(install.path) if install else None,
        "source": install.source if install else None,
        "reason": outcome.reason,
        "env_override": os.environ.get("LTSPICE_EXE"),
        "config_path_setting": explicit,
        "probed": outcome.probed_paths,
        "batch_resolution": BATCH_RESOLUTION_NOTES,
        "lib_dir": str(default_lib_dir()) if default_lib_dir() else config.ltspice.lib_dir,
        "timeout_s": config.ltspice.timeout_s,
    }

    if install is None:
        override = os.environ.get("LTSPICE_EXE")
        if outcome.reason == "configured_missing":
            detail = (
                f"configured LTspice path does not exist: {explicit} "
                "(discovery does not fall back to another installation)"
            )
        elif outcome.reason == "env_missing":
            detail = (
                f"LTSPICE_EXE points at a file that does not exist: {override} "
                "(discovery does not fall back to another installation)"
            )
        else:
            detail = "LTspice executable not found; probed: " + ", ".join(outcome.probed_paths)
        section.update(
            {
                "version": None,
                "smoke_test": "fail" if run_smoke else None,
                "smoke_detail": detail,
            }
        )
        return section

    section["version"] = ltspice_version(install.path)
    if not run_smoke:
        section["smoke_test"] = None
        section["smoke_detail"] = "smoke test skipped (--no-smoke)"
        return section

    workdir = smoke_workdir
    if workdir is None:
        workdir = Path(tempfile.mkdtemp(prefix="boardmodeler-smoke-"))
    result = smoke_test(install.path, workdir, timeout_s=config.ltspice.timeout_s)
    section.update(result.as_dict())
    section["smoke_workdir"] = str(workdir)
    return section


def _reader_section(smoke_raw: Path | None) -> dict[str, Any]:
    return probe_backend(smoke_raw).as_dict()


def _ocr_section() -> dict[str, Any]:
    try:
        ocr = importlib.import_module("boardmodeler.documents.ocr")
    except ImportError as exc:  # pragma: no cover - only before the module lands
        return {"available": None, "reason": "documents_module_unavailable", "detail": str(exc)}
    unavailable = ocr.probe_ocr()
    if unavailable is None:
        return {"available": True, "reason": None, "detail": "OCR engine detected"}
    return {
        "available": False,
        "reason": unavailable.reason,
        "detail": unavailable.detail,
        "engine": unavailable.engine,
    }


def _credentials_section(
    names: tuple[str, ...] = ("fixture", "http_inference", "bob_direct"),
) -> dict:
    try:
        creds = importlib.import_module("boardmodeler.security.credentials")
    except ImportError as exc:  # pragma: no cover - only before the module lands
        return {"available": None, "reason": "credentials_module_unavailable", "detail": str(exc)}
    return {name: creds.describe_credential(name) for name in names}


def doctor_payload(*, run_smoke: bool = True, smoke_workdir: Path | None = None) -> dict[str, Any]:
    """Collect the environment report. Every field is observed, never assumed."""
    cfg_path = config_path()
    ltspice = _ltspice_section(run_smoke=run_smoke, smoke_workdir=smoke_workdir)
    smoke_raw = None
    if ltspice.get("smoke_workdir"):
        candidate = Path(str(ltspice["smoke_workdir"])) / "smoke_rc.raw"
        if candidate.is_file():
            smoke_raw = candidate

    payload: dict[str, Any] = {
        "tool": "boardmodeler",
        "version": __version__,
        "python": platform.python_version(),
        "platform": sys.platform,
        "executable": sys.executable,
        "config": {"path": str(cfg_path), "exists": cfg_path.is_file()},
        "ltspice": ltspice,
        "reader_backend": _reader_section(smoke_raw),
        "ocr": _ocr_section(),
        "credentials": _credentials_section(),
        "telemetry": "none",
    }
    payload["ok"] = bool(ltspice.get("found")) and (ltspice.get("smoke_test") in ("pass", None))
    return payload


def _render_doctor_human(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(
        f"boardmodeler {payload['version']} on python {payload['python']} ({payload['platform']})"
    )
    config = payload["config"]
    lines.append(f"config: {config['path']} ({'found' if config['exists'] else 'not present'})")

    ltspice = payload["ltspice"]
    if ltspice["found"]:
        lines.append(
            f"ltspice: {ltspice['path']} (version {ltspice['version']}, via {ltspice['source']})"
        )
        lines.append(f"  lib dir: {ltspice['lib_dir']}")
        if ltspice.get("smoke_test") is None:
            lines.append(f"  smoke test: skipped ({ltspice.get('smoke_detail')})")
        else:
            lines.append(f"  smoke test: {ltspice['smoke_test']}")
            lines.append(f"  {ltspice.get('smoke_detail')}")
    else:
        lines.append("ltspice: NOT FOUND")
        lines.append(f"  probed: {ltspice.get('smoke_detail')}")

    backend = payload["reader_backend"]
    lines.append(
        f"reader backend: {backend['reader_backend']} (spicelib={backend['spicelib_version']}, "
        f"max deviation={backend['max_deviation']})"
    )
    lines.append(f"  {backend['detail']}")

    ocr = payload["ocr"]
    lines.append(
        f"ocr: {'available' if ocr.get('available') else 'unavailable'}"
        f"{' - ' + str(ocr.get('reason')) if ocr.get('reason') else ''}"
    )
    lines.append(f"  {ocr.get('detail')}")

    creds = payload["credentials"]
    lines.append("credentials: " + ", ".join(f"{k}={v}" for k, v in creds.items()))
    lines.append("telemetry: none")
    return "\n".join(lines)


def _cmd_run_tests(args: argparse.Namespace) -> int:
    from boardmodeler.domain.enums import Status
    from boardmodeler.pipeline.project import Project, ProjectError
    from boardmodeler.pipeline.runner import run_deck_tests
    from boardmodeler.simulation.ltspice import locate_outcome
    from boardmodeler.verification.engine import evaluate_case

    try:
        project = Project(args.project)
    except ProjectError as exc:
        print(f"error: {exc}")
        return 2

    cases = project.tests(scope=args.scope, test_ids=args.test_ids)
    if args.list_tests:
        payload = [
            {
                "test_id": case.test_id,
                "scenario_id": case.scenario_id,
                "scope": case.scope,
                "expected": case.expected.kind,
                "requirement_ids": case.requirement_ids,
                "deck_template": case.deck_template,
            }
            for case in cases
        ]
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            for row in payload:
                print(
                    f"{row['test_id']:32} {row['scenario_id']:28} {row['scope']:20} "
                    f"{row['expected']:16} {len(row['requirement_ids'])} requirement(s)"
                )
            print(f"{len(cases)} case(s)")
        return 0

    if not cases:
        print(
            "error: no test cases matched"
            + (f" scope={args.scope!r}" if args.scope else "")
            + (f" test_ids={args.test_ids}" if args.test_ids else "")
            + f"; the project has {len(project.read_tests_file())} case(s)"
        )
        return 1

    requirements = project.requirements()
    if not requirements:
        print(
            "warning: the project has no requirements, so every case will be UNKNOWN "
            f"({project.requirements_path} is missing or empty)"
        )

    outcome = locate_outcome(args.ltspice)
    install = outcome.install
    if install is None:
        print(
            "error: LTspice was not found, so no case can run "
            f"(reason={outcome.reason}); probed: {', '.join(outcome.probed_paths)}"
        )
        return 2

    ctx = project.run_context(
        ltspice=install, timeout_s=args.timeout or 120.0, ascii_raw=args.ascii_raw
    )
    artifacts = run_deck_tests(ctx, cases)

    results = []
    summary: dict[str, int] = {}
    for case, artifact in zip(cases, artifacts, strict=True):
        cap_gate = _capability_gate(project, case)
        result = evaluate_case(
            case,
            artifact,
            requirements,
            supply_domains=project.config.supply_domains,
            capability_gate=cap_gate,
        )
        summary[result.status.value] = summary.get(result.status.value, 0) + 1
        results.append(
            {
                "result": json.loads(result.model_dump_json()),
                "run": {
                    "run_id": artifact.run_id,
                    "run_dir": str(artifact.run_dir),
                    "deck_sha256": artifact.deck_sha256,
                    "raw_sha256": artifact.raw_sha256,
                    "log_sha256": artifact.log_sha256,
                    "observed": artifact.detail,
                    "usable": artifact.usability.usable,
                    "blocked_reason": artifact.blocked_reason,
                },
            }
        )

    payload = {
        "tool": "boardmodeler",
        "command": "run tests",
        "project": str(project.root),
        "project_id": project.config.project_id,
        "ltspice": {"path": str(install.path), "version": ltspice_version(install.path)},
        "requirements": len(requirements),
        "summary": summary,
        "results": results,
    }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for row in results:
            result = row["result"]
            print(f"{result['status']:16} {result['test_id']:32} {result['detail'][:110]}")
        print(f"summary: {summary}  ({len(results)} case(s))")
        if args.out:
            print(f"results written to {args.out}")

    if args.strict and any(r["result"]["status"] != Status.PASS.value for r in results):
        return 1
    return 0


def _capability_gate(project: object, case: object) -> dict[str, str] | None:
    """Capability gate for a project, built from its declared capabilities.

    Reads ``models/capabilities/*.json`` (``ModelCapability`` records) and
    ``evidence/capability_map.json`` (``{requirement_id: behavior}``). A project
    without either file has no capability declaration, so nothing is gated.
    """
    from boardmodeler.domain.records import ModelCapability
    from boardmodeler.verification.engine import gate_from_capability

    root = Path(str(getattr(project, "root", ".")))
    caps_dir = root / "models" / "capabilities"
    behavior_map_file = root / "evidence" / "capability_map.json"
    if not caps_dir.is_dir() or not behavior_map_file.is_file():
        return None

    capabilities = [
        ModelCapability.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(caps_dir.glob("*.json"))
    ]
    raw_map = json.loads(behavior_map_file.read_text(encoding="utf-8"))
    requirement_behaviors = {str(k): str(v) for k, v in raw_map.items()}
    gate = gate_from_capability(capabilities, requirement_behaviors)
    return gate or None


def _cmd_demo_build(args: argparse.Namespace) -> int:
    from boardmodeler.pipeline.demo import build_demo_project
    from boardmodeler.simulation.ltspice import locate_outcome

    install = locate_outcome().install
    if install is None:
        print("error: LTspice was not found, so the demo cannot be built and probed")
        return 2
    result = build_demo_project(args.out, ltspice=install, workdir=Path(args.out) / "probe_project")
    payload = {
        "tool": "boardmodeler",
        "command": "demo build",
        "project": str(result.project_dir),
        "detail": result.detail,
        "files": result.files,
        "decks": sorted(result.decks),
        "tests": [case.test_id for case in result.tests],
        "static_findings": [
            {"code": f.code, "status": f.status.value, "refdes": f.refdes, "message": f.message}
            for f in result.static_findings
        ],
        "capabilities": {mid: cap.behaviors for mid, cap in result.capabilities.items()},
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"built {result.project_dir}")
        print(f"  {result.detail}")
        print(f"  files: {len(result.files)}")
        print(f"  decks: {', '.join(sorted(result.decks))}")
        failing = [f for f in result.static_findings if f.status.value == "FAIL"]
        print(f"  static checks: {len(result.static_findings)} findings, {len(failing)} failing")
        for finding in failing:
            print(f"    {finding.code} {finding.refdes or ''} {finding.message[:90]}")
    return 0


def _cmd_circuit_check(args: argparse.Namespace) -> int:
    from boardmodeler.domain.enums import Status
    from boardmodeler.pipeline.demo import check_circuit, run_fault_matrix
    from boardmodeler.simulation.ltspice import locate_outcome

    install = locate_outcome().install
    if install is None:
        print("error: LTspice was not found, so no dynamic check can run")
        return 2
    out = args.out or Path(args.project) / "results.json"
    report = args.report or Path(args.project) / "report.html"
    result = check_circuit(
        args.project,
        circuit_path=args.circuit,
        ltspice=install,
        scope=args.scope,
        report_path=report,
        results_path=out,
    )
    fault_matrix = None
    if args.fault_matrix:
        fault_matrix = run_fault_matrix(args.project, ltspice=install)
    payload = {
        "tool": "boardmodeler",
        "command": "circuit check",
        "project": str(result.project_dir),
        "status": result.status.value,
        "summary": result.summary(),
        "coverage": result.coverage,
        "findings": [json.loads(f.model_dump_json()) for f in result.findings],
        "results": [json.loads(r.model_dump_json()) for r in result.results],
        "results_path": str(out),
        "report_path": str(report),
        "fault_matrix": fault_matrix,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"status: {result.status.value}   {result.summary()}")
        for finding in result.findings:
            if finding.status is not Status.PASS:
                print(f"  {finding.status.value:12} {finding.code:32} {finding.message[:90]}")
        for test_result in result.results:
            print(
                f"  {test_result.status.value:12} {test_result.test_id:28} {test_result.detail[:80]}"
            )
        print(f"results -> {out}")
        print(f"report  -> {report}")
        if fault_matrix:
            print(
                f"fault matrix: {fault_matrix['detected']}/{fault_matrix['total']} detected, "
                f"original unchanged: {fault_matrix['original_unchanged']}"
            )
    if args.strict and result.status is not Status.PASS:
        return 1
    return 0


def _cmd_run_mutations(args: argparse.Namespace) -> int:
    from boardmodeler.pipeline.demo import run_fault_matrix
    from boardmodeler.simulation.ltspice import locate_outcome

    install = locate_outcome().install
    if install is None:
        print("error: LTspice was not found, so faults cannot be exercised")
        return 2
    report = run_fault_matrix(
        args.project,
        out_dir=args.report.parent / "fault_matrix",
        ltspice=install,
        faults=args.fault,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for entry in report["faults"]:
            mark = "detected" if entry["detected"] else "NOT DETECTED"
            print(f"  {entry['fault_id']:22} {mark:14} {entry['expected_detection']}")
        print(f"{report['detected']}/{report['total']} faults detected")
        print(f"original project unchanged: {report['original_unchanged']}")
        print(f"report -> {args.report}")
    return 0 if report["detected"] == report["total"] else 1


def _cmd_export(args: argparse.Namespace) -> int:
    from boardmodeler.pipeline.project import Project, ProjectError
    from boardmodeler.reporting.export import export_project

    try:
        project = Project(args.project)
    except ProjectError as exc:
        print(f"error: {exc}")
        return 2
    result = export_project(project, args.out, model_id=args.model)
    payload = {
        "tool": "boardmodeler",
        "command": "export",
        "out": str(result.out_dir),
        "files": result.relative_files(),
        "findings": [json.loads(f.model_dump_json()) for f in result.findings],
        "ok": result.ok,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"exported {len(result.files)} file(s) to {result.out_dir}")
        for name in result.relative_files():
            print(f"  {name}")
        for finding in result.findings:
            print(f"  {finding.status.value:10} {finding.code}: {finding.message[:90]}")
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    from boardmodeler.pipeline.project import Project, ProjectError

    try:
        project = Project(args.project)
    except ProjectError as exc:
        print(f"error: {exc}")
        return 2
    try:
        from boardmodeler.providers.registry import select_provider
        from boardmodeler.requirements.extract import extract_requirements
    except ImportError as exc:
        print(f"error: the extraction layer is unavailable: {exc}")
        return 2

    from boardmodeler.config import load_config

    config = load_config()
    if args.allow_remote:
        config.data_policy.allow_remote = True
    if args.doc is not None:
        from boardmodeler.documents.store import DocumentStore

        store = DocumentStore(project.root)
        record = store.add_file(
            Path(args.doc),
            doc_type="datasheet",
            provenance="user_supplied",
            remote_inference_allowed=bool(args.allow_remote),
        )
        print(f"ingested {record.doc_id} ({record.page_count} pages, {record.text_extraction})")
    selection = select_provider(
        config,
        requested=args.provider,
        allow_bob_shell=False,
        fixture_dir=str(project.root / "evidence" / "cache"),
    )
    result = extract_requirements(project, provider=selection.provider)
    payload = {
        "tool": "boardmodeler",
        "command": "extract",
        "project": str(project.root),
        "provider": {"name": selection.detail, "kind": selection.kind.value},
        "detail": result.detail,
        "requirements": len(result.requirements),
        "pins": len(result.pins),
        "cache_hits": result.cache_hits,
        "issues": [issue.code for issue in result.issues],
        "disclosures": [json.loads(d.model_dump_json()) for d in result.disclosures],
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"provider: {selection.detail}")
        print(f"  requirements: {len(result.requirements)}  pins: {len(result.pins)}")
        print(f"  cache hits: {result.cache_hits}  issues: {len(result.issues)}")
        print(f"  {result.detail}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in (None, "version"):
        if args.command == "version" and getattr(args, "json", False):
            print(json.dumps({"tool": "boardmodeler", "version": __version__}, indent=2))
        else:
            print(f"boardmodeler {__version__}")
        return 0

    if args.command == "doctor":
        payload = doctor_payload(
            run_smoke=not getattr(args, "no_smoke", False),
            smoke_workdir=getattr(args, "smoke_workdir", None),
        )
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2))
        else:
            print(_render_doctor_human(payload))
        return 0

    if args.command == "ui":
        from boardmodeler.ui.app import main as ui_main

        forwarded: list[str] = []
        if args.project is not None:
            forwarded += ["--project", str(args.project)]
        if getattr(args, "installer", False):
            forwarded.append("--installer")
        return ui_main(forwarded)

    if args.command == "setup":
        from boardmodeler.ui.installer import main as setup_main

        forwarded = ["--json"] if getattr(args, "json", False) else []
        if args.project is not None:
            forwarded += ["--project", str(args.project)]
        return setup_main(forwarded)

    if args.command == "run" and args.run_command == "tests":
        return _cmd_run_tests(args)

    if args.command == "run" and args.run_command == "mutations":
        return _cmd_run_mutations(args)

    if args.command == "demo" and args.demo_command == "build":
        return _cmd_demo_build(args)

    if args.command == "circuit" and args.circuit_command == "check":
        return _cmd_circuit_check(args)

    if args.command == "export":
        return _cmd_export(args)

    if args.command == "extract":
        return _cmd_extract(args)

    parser.error(f"unhandled command {args.command!r}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
