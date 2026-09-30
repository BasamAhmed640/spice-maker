"""Opt-in end-to-end proof that the release a user downloads reaches a model.

Why this is not part of the default suite: the journey downloads or copies a ~90 MB
ZIP, runs ``Install.exe`` (which builds a ~1 GB environment), drives the installed
copy's CLI and, when LTspice is present, runs real simulator probes. That is minutes
of wall clock and gigabytes of disk, so it is enabled only by
``BOARDMODELER_INSTALLER_TESTS=1`` and the skip message names that flag and the cost.

Two journeys are covered:

* the newest ``releases/*.zip`` through ``tools/verify_release_zip.py`` with
  ``--no-ltspice`` (the machine-independent half of the journey);
* the *tracked* ``Install.exe`` through archive + extract + install only, asserting
  that its version stamp agrees with ``pyproject.toml`` - the same check that catches
  "the installer is older than the source".

Nothing here fabricates a result: both tests read the harness's machine-readable
report and assert on what it observed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "verify_release_zip.py"
FLAG = "BOARDMODELER_INSTALLER_TESTS"

#: What the flag buys, so the skip message can state the cost honestly.
#: Measured here: both tests together take about 40 s and one installed copy at a time
#: (about 1 GB), which the tests delete themselves.
COST = "~2 minutes and about 1 GB of disk in the pytest temporary directory"

pytestmark = [pytest.mark.installer, pytest.mark.slow]


def _require_flag() -> None:
    """The explicit capability probe: enabled by the flag, never skipped silently."""
    if os.environ.get(FLAG) != "1":
        pytest.skip(f"set {FLAG}=1 to run the release-zip journey ({COST})")


def _run_harness(args: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run the harness exactly as a person would, and keep its output for the report."""
    command = [sys.executable, str(TOOL), *args]
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as expired:
        raise AssertionError(
            f"the journey did not finish within {timeout:g} s: {subprocess.list2cmdline(command)}"
        ) from expired
    assert completed.returncode in (0, 1, 2), (
        f"the harness itself failed (exit {completed.returncode}):\n{completed.stderr[-2000:]}"
    )
    return completed


def _read_report(path: Path) -> dict:
    assert path.is_file(), f"the harness wrote no report at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _stage(report: dict, name: str) -> dict:
    matches = [stage for stage in report["stages"] if stage["name"] == name]
    assert matches, f"the report has no {name!r} stage"
    return matches[0]


def _declared_version() -> str:
    declared = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(declared["project"]["version"])


def test_newest_release_zip_journey_passes(tmp_path: Path) -> None:
    """The newest built release ZIP must reach an installed copy that answers its CLI."""
    _require_flag()
    releases = sorted(
        (path for path in (REPO_ROOT / "releases").glob("*.zip") if path.is_file()),
        key=lambda path: path.stat().st_mtime_ns,
    )
    if not releases:
        pytest.skip(
            "no releases/*.zip exists in this checkout; build one with installer/build.ps1 first"
        )
    report_path = tmp_path / "release-verify.json"
    started = time.monotonic()
    completed = _run_harness(
        [
            "--source",
            "releases",
            "--no-ltspice",
            "--work",
            str(tmp_path / "work"),
            "--report",
            str(report_path),
            "--markdown",
            str(tmp_path / "release-verify.md"),
        ],
        timeout=1800,
    )
    report = _read_report(report_path)
    assert report["verdict"] == "PASS", (
        f"{report['verdict']}: {report['verdict_meaning']}\n{completed.stdout[-2000:]}"
    )
    assert report["options"]["no_ltspice"] is True
    for name in ("archive", "extract", "install", "environment", "cleanup"):
        assert _stage(report, name)["status"] == "PASS", _stage(report, name)["reason"]
    skipped = {skip["stage"]: skip for skip in report["skipped_stages"]}
    for name in ("ltspice-path", "model-build", "model-test"):
        assert name in skipped, f"{name} must be reported as skipped under --no-ltspice"
        assert skipped[name]["expected"] is True, skipped[name]
    assert report["fully_verified"] is False, "a --no-ltspice run cannot be fully verified"
    archive = _stage(report, "archive")
    assert archive["evidence"]["installer_sha256_matches_sums"] is True
    assert archive["evidence"]["version_matches_pyproject"] is True
    environment = _stage(report, "environment")
    assert (
        environment["evidence"]["package_version"]
        == archive["evidence"]["pyproject_version"]["version"]
    )
    assert report["seconds"] > 0
    # The elapsed time is in the report already; the print keeps a terminal run useful.
    print(f"\nrelease journey: {time.monotonic() - started:.1f} s")


def test_tracked_installer_version_stamp_and_layout(tmp_path: Path) -> None:
    """The tracked Install.exe must install here and carry the declared version.

    The ZIP is assembled from the tracked files rather than downloaded: the tracked
    ``Install.exe`` is the one the repository ships, and the archive stage then checks
    its version stamp against ``pyproject.toml`` exactly as it does for a download.
    """
    _require_flag()
    tracked = (
        REPO_ROOT / "Install.exe",
        REPO_ROOT / "INSTALL.txt",
        REPO_ROOT / "SHA256SUMS.txt",
        REPO_ROOT / "README.md",
    )
    missing = [str(path) for path in tracked if not path.is_file()]
    if missing:
        pytest.skip(f"the tracked installer files are not present in this checkout: {missing}")
    zip_path = tmp_path / "tracked-installer.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for path in tracked:
            archive.write(path, path.name)
    work = tmp_path / "work"
    report_path = tmp_path / "tracked-report.json"
    try:
        completed = _run_harness(
            [
                "--source",
                "zip",
                "--zip",
                str(zip_path),
                "--until",
                "install",
                "--work",
                str(work),
                "--report",
                str(report_path),
                "--markdown",
                str(tmp_path / "tracked-report.md"),
            ],
            timeout=1800,
        )
        report = _read_report(report_path)
        assert report["verdict"] == "PASS", (
            f"{report['verdict']}: {report['verdict_meaning']}\n{completed.stdout[-2000:]}"
        )
        declared = _declared_version()
        archive_stage = _stage(report, "archive")
        evidence = archive_stage["evidence"]
        assert evidence["pyproject_version"]["version"] == declared
        assert evidence["installer_version"]["normalized"] == declared, evidence[
            "installer_version"
        ]
        assert evidence["version_matches_pyproject"] is True
        assert evidence["installer_sha256_matches_sums"] is True
        install = _stage(report, "install")
        assert install["status"] == "PASS", install["reason"]
        assert install["evidence"]["layout"]["app/SpiceMaker.exe"]["present"] is True
        assert install["evidence"]["layout"][".venv/Scripts/python.exe"]["present"] is True
        assert install["evidence"]["setup_log"]["bytes"] > 0
        assert _stage(report, "extract")["status"] == "PASS"
        assert _stage(report, "extract")["evidence"]["files_outside_dest"] == []
        not_run = {skip["stage"] for skip in report["skipped_stages"]}
        assert "environment" in not_run and "cleanup" in not_run
    finally:
        # --until install leaves the installed copy behind by design; the test owns it.
        shutil.rmtree(work, ignore_errors=True)
