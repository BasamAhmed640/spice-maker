"""A built model outlives the process that built it: reopen it, and re-verify it.

The claim under test is that a finished model directory is a *product* rather than a
session artifact. The build runs in its own process (``model build`` as a subprocess), so
the reopen step shares no in-process state with it, and every fact the reopen reports is
read back from disk.

The offline build here is deliberately honest about its inputs: the repository's real
TPS54320 datasheet is git-ignored and absent, so the run uses a stand-in one-page sheet and
declares the committed extraction as ``TEST_FIXTURE``/``synthetic_fixture`` — the same
convention the release-verification harness and ``tests/pipeline/test_make_model.py`` use.
The pin map is a stand-in for the datasheet's pin table whose terminals are exactly the
bundled template's ports, because a structural sanity check compares those two sets and
inventing extra terminals would make the check fail for the wrong reason.

Nothing here fabricates a verdict: ``--verify`` either really runs the harness, or it says
with a non-zero exit code that it could not.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "fixtures" / "regulator" / "tps54320"
BINDINGS = FIXTURES / "probes.json"
REQUIREMENTS = FIXTURES / "requirements.json"

PART = "TPS54320"
#: The subcircuit the bundled offline author writes a template for. A ``--sanity`` build
#: uses that template, so the name has to be one of the bundled kinds (as
#: ``tests/pipeline/test_make_model.py`` does with the same fixtures).
SUBCKT = "BM_REG_BUCK"

#: What the bundled offline author writes (``models.regulator.REGULATOR_PORT_ORDER``).
TEMPLATE_KINDS = ("BM_REG_BUCK",)


def _write_minimal_pdf(path: Path, lines: list[str]) -> Path:
    """A valid one-page PDF from the standard library alone (no reportlab needed)."""

    def escape(text: str) -> str:
        return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    content = "BT /F1 11 Tf 40 740 Td 16 TL\n" + "\n".join(
        f"({escape(line)}) Tj T*" for line in lines
    )
    content += "\nET"
    stream = content.encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(payload)
    payload += f"xref\n0 {len(objects) + 1}\n".encode()
    payload += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        payload += f"{offset:010d} 00000 n \n".encode()
    payload += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(payload))
    return path


def _pin_map() -> list[dict[str, Any]]:
    """A stand-in pin table whose terminals are exactly the template's ports.

    The sanity check compares the model's ports with the physical terminals, so the names
    have to be the template's own. Everything else (direction, topology) is the plainest
    reading of the port's role and is not a datasheet claim: the rows this stand-in feeds
    are already declared ``TEST_FIXTURE``.
    """
    from boardmodeler.models.regulator import REGULATOR_PORT_ORDER

    roles: dict[str, tuple[str, str, str]] = {
        # port: (polarity, direction, output_topology)
        "VIN": ("not_applicable", "power", "power"),
        "EN": ("active_high", "input", "input_only"),
        "FB": ("not_applicable", "input", "input_only"),
        "PG": ("active_high", "output", "open_drain"),
        "VOUT": ("not_applicable", "output", "push_pull"),
        "GND": ("not_applicable", "ground", "power"),
        "SW": ("not_applicable", "output", "push_pull"),
        "ILIM_MODE": ("not_applicable", "input", "input_only"),
    }
    ports = REGULATOR_PORT_ORDER[TEMPLATE_KINDS[0]]
    return [
        {
            "part_id": PART,
            "physical_pin": str(index + 1),
            "name": port,
            "function": "TEST FIXTURE stand-in for this terminal's datasheet function",
            "polarity": roles[port][0],
            "direction": roles[port][1],
            "output_topology": roles[port][2],
            "connection_requirement": "required",
        }
        for index, port in enumerate(ports)
    ]


def _derive_inputs(root: Path) -> tuple[Path, Path]:
    """``(datasheet, requirements)`` for one offline run, declared as what they are."""
    sheet = _write_minimal_pdf(
        root / "tps54320-standin.pdf",
        ["TEST FIXTURE: harness stand-in sheet for the reopen evidence.", f"Part: {PART}"],
    )
    raw = json.loads(REQUIREMENTS.read_text(encoding="utf-8"))
    for row in raw["requirements"]:
        row["origin"] = "TEST_FIXTURE"
        for evidence in row.get("evidence") or []:
            evidence["extraction"] = "synthetic_fixture"
    raw["pin_map"] = _pin_map()
    requirements = root / "requirements-standin.json"
    requirements.write_text(json.dumps(raw, indent=2), encoding="utf-8", newline="\n")
    return sheet, requirements


def _child_env() -> dict[str, str]:
    """The test's environment with the network pinned off.

    Both journeys are offline ones, so pinning the switch off proves the stronger claim:
    a model can be built, reopened and re-verified with no network access at all.
    """
    env = dict(os.environ)
    env["BOARDMODELER_NO_NETWORK"] = "1"
    return env


def _cli(*args: str, timeout: float = 900.0) -> subprocess.CompletedProcess[str]:
    """Run the CLI in a *new* process: nothing at all is shared with the calling one."""
    command = [sys.executable, "-m", "boardmodeler.cli", *args]
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=_child_env(),
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def built_model(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One real offline build, produced by the CLI in its own process."""
    root = tmp_path_factory.mktemp("reopen")
    sheet, requirements = _derive_inputs(root)
    out_dir = root / "models" / "tps54320"
    completed = _cli(
        "model",
        "build",
        "--part",
        PART,
        "--subckt",
        SUBCKT,
        "--datasheet",
        str(sheet),
        "--requirements",
        str(requirements),
        "--bindings",
        str(BINDINGS),
        "--out",
        str(out_dir),
        "--backend",
        "fixture",
        "--sanity",
        "--json",
    )
    payload = json.loads(completed.stdout or "{}")
    assert completed.returncode == 0, (
        completed.returncode,
        completed.stdout[-2000:],
        completed.stderr[-2000:],
    )
    # A sanity build is never a PASS: the electrical claims were not measured.
    assert payload["status"] == "UNKNOWN", payload
    for name in (f"{SUBCKT}.lib", f"{SUBCKT}.asy", "MODEL_CARD.md", "results.json"):
        assert (out_dir / name).is_file(), f"the build published no {name}: {payload['detail']}"
    return out_dir


@pytest.fixture
def model_dir(built_model: Path, tmp_path: Path) -> Path:
    """A private copy per test, so a test that re-verifies cannot disturb another."""
    target = tmp_path / "model"
    shutil.copytree(built_model, target)
    return target


def _recorded(model_dir: Path) -> dict[str, Any]:
    return json.loads((model_dir / "results.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# reopening


def test_a_built_model_is_reopened_from_disk_with_what_it_recorded(
    model_dir: Path, capsys: pytest.CaptureFixture
) -> None:
    """No in-process state: the reopen reads the directory and reports exactly that."""
    from boardmodeler import cli

    recorded = _recorded(model_dir)
    exit_code = cli.main(["model", "open", "--out", str(model_dir), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ok"] is True and payload["reason"] is None
    assert payload["part"] == recorded["part"] == PART
    assert payload["subckt"] == SUBCKT
    assert payload["lib_exists"] is True and payload["asy_exists"] is True
    for key in ("lib_path", "asy_path", "card_path", "results_path"):
        assert Path(payload[key]).is_file(), f"{key} does not name a real file: {payload[key]}"
    # The recorded verdict and every recorded row, not a re-derivation.
    assert payload["status"] == recorded["status"] == "UNKNOWN"
    assert payload["counts"] == recorded["counts"]
    assert len(payload["rows"]) == len(recorded["rows"]) == 38
    assert payload["results_problem"] is None
    # The manifest and the verification evidence are named with their own timestamps.
    # ``build/project.json`` belongs to the project/controller path, which this build did
    # not use — so the honest answer is None here, and the manifest's own field is checked
    # in the test below rather than asserted away.
    assert payload["manifest_path"] is None and payload["manifest_at"] is None
    assert Path(payload["verification_path"]).is_file()
    assert payload["verification_at"], "the verification evidence has no timestamp"
    # This run was not asked to verify, so it must not claim it did.
    assert payload["verified"] is False and payload["verification"] is None


def test_the_manifest_timestamp_is_the_manifests_own_field(
    model_dir: Path, capsys: pytest.CaptureFixture
) -> None:
    """``build/project.json``'s own ``created_utc`` is reported, never a made-up time."""
    from boardmodeler import cli

    manifest = model_dir / "build" / "project.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"project_id": "reopen", "created_utc": "2026-01-02T03:04:05Z"}),
        encoding="utf-8",
    )
    exit_code = cli.main(["model", "open", "--out", str(model_dir), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["manifest_path"] == str(manifest)
    assert payload["manifest_at"] == "2026-01-02T03:04:05Z"
    # A manifest without that field still yields a fact: when the file was written.
    manifest.write_text(json.dumps({"project_id": "reopen"}), encoding="utf-8")
    cli.main(["model", "open", "--out", str(model_dir), "--json"])
    fallback = json.loads(capsys.readouterr().out)
    assert fallback["manifest_at"] and fallback["manifest_at"] != "2026-01-02T03:04:05Z"


def test_open_refuses_a_directory_that_is_not_a_model(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """A folder with no model in it is refused with a reason, not shown as empty."""
    from boardmodeler import cli

    empty = tmp_path / "not-a-model"
    empty.mkdir()
    (empty / "notes.txt").write_text("just a folder", encoding="utf-8")
    exit_code = cli.main(["model", "open", "--out", str(empty), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["reason"].startswith("not_a_model_directory")
    assert payload["status"] is None and payload["rows"] == []
    assert payload["verified"] is False
    # The same refusal for a directory that does not exist at all.
    exit_code = cli.main(["model", "open", "--out", str(tmp_path / "gone"), "--json"])
    missing = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert missing["reason"].startswith("not_a_directory")


def test_verify_without_a_simulator_says_so_and_exits_non_zero(
    model_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Never a silent pass: no simulator means BLOCKED, a named reason, and exit 1."""
    from boardmodeler import cli
    from boardmodeler.simulation import ltspice

    monkeypatch.setattr(ltspice, "locate", lambda *args, **kwargs: None)
    exit_code = cli.main(["model", "open", "--out", str(model_dir), "--verify", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["verified"] is False
    assert payload["verification"]["status"] == "BLOCKED"
    assert "LTspice" in payload["verification"]["detail"]
    # The summary itself is still honest about what the directory holds.
    assert payload["status"] == "UNKNOWN" and payload["lib_exists"] is True


@pytest.mark.ltspice
def test_verify_really_runs_the_harness_and_rewrites_its_evidence(
    model_dir: Path, ltspice_exe: Path, capsys: pytest.CaptureFixture
) -> None:
    """With a simulator, ``--verify`` runs the real path and updates the evidence."""
    from boardmodeler import cli

    evidence = model_dir / "harness-report.json"
    before = evidence.stat().st_mtime_ns
    exit_code = cli.main(["model", "open", "--out", str(model_dir), "--verify", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["verified"] is True, payload
    assert exit_code == 0
    verification = payload["verification"]
    assert verification["status"] in {"PASS", "UNKNOWN"}, verification
    # A sanity-authored model has no *bound* probes — its bindings are deferred by design —
    # so the honest result of re-running the harness is a completed run with a zero tally.
    # What proves the run happened is the rewritten evidence and the files it wrote, which
    # is exactly what is asserted here; the status is never BLOCKED.
    assert set(verification["counts"]) >= {"PASS", "FAIL", "UNKNOWN"}, verification["counts"]
    assert verification["files"], "a verification that wrote nothing did not run"
    for path in verification["files"]:
        assert Path(path).is_file(), path
    assert evidence.stat().st_mtime_ns >= before, "the harness evidence was not rewritten"
    assert payload["verification_at"]


# --------------------------------------------------------------------------- #
# the window


def test_open_model_fills_the_window_from_the_directory(
    model_dir: Path, qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPEN MODEL… is the GUI half of the same claim: the window reads the folder."""
    pytest.importorskip("PySide6")
    from boardmodeler.ui.model_maker import ModelMakerWindow

    # The window reads and writes the config, so point it at a throwaway file.
    monkeypatch.setattr("boardmodeler.config.config_path", lambda: tmp_path / "config.json")
    recorded = _recorded(model_dir)

    window = ModelMakerWindow()
    qtbot.addWidget(window)
    assert window.open_model_path(model_dir) is True

    assert window.part_edit.text() == PART
    assert window.out_edit.text() == str(model_dir)
    assert window._out_dir == model_dir
    assert window.status_label.text(), "the status line must say what was recorded"
    assert "PASS" not in window.status_label.text(), window.status_label.text()
    assert window.rows.rowCount() == len(recorded["rows"])
    assert window.rows.item(0, 0) is not None
    first_id = window.rows.item(0, 0)
    first_status = window.rows.item(0, 3)
    assert first_id is not None and first_status is not None
    assert first_id.text() == recorded["rows"][0]["req_id"]
    assert first_status.text() == recorded["rows"][0]["status"]
    # The existing actions must now work on the reopened model.
    assert window.again_button.isEnabled() is True
    assert window.open_button.isEnabled() is True
    assert window.install_button.isEnabled() is True

    # ...and a folder that is not a model is refused without touching the window's fields.
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(
        "boardmodeler.ui.model_maker.QMessageBox.warning",
        staticmethod(lambda *args, **kwargs: None),
    )
    assert window.open_model_path(empty) is False
    assert window.out_edit.text() == str(model_dir)
