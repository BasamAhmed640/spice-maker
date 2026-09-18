"""Retro installer wizard tests: steps, observation honesty, config writing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from boardmodeler.simulation.ltspice import SmokeResult
from boardmodeler.ui.installer import (
    STEP_IDS,
    InstallerWizard,
    outcome_payload,
    retro_font,
)

pytestmark = pytest.mark.gui

SECRET = "sk-do-not-write-me-to-disk"


def _wizard(tmp_path: Path, answers: dict | None = None, **kwargs) -> InstallerWizard:
    return InstallerWizard(
        answers=answers,
        config_file=tmp_path / "config.json",
        **kwargs,
    )


def _full_answers(tmp_path: Path, **overrides) -> dict:
    answers = {
        "project_dir": str(tmp_path / "project"),
        "skip": ["ltspice"],
        "provider": "http_inference",
        "endpoint": "https://api.example.invalid/v1/chat/completions",
        "model": "example-model",
        "allow_remote": True,
        "acknowledge_privacy": True,
    }
    answers.update(overrides)
    return answers


def _distinct_colours(image: QImage) -> set[int]:
    colours: set[int] = set()
    for y in range(0, image.height(), 3):
        for x in range(0, image.width(), 3):
            colours.add(image.pixel(x, y))
    return colours


def test_page_ids_are_the_five_steps(qapp, tmp_path: Path) -> None:
    wizard = _wizard(tmp_path)
    assert wizard.page_ids() == ["welcome", "ltspice", "provider", "policy", "finish"]
    assert wizard.page_ids() == list(STEP_IDS)
    assert wizard.step_number("policy") == 4


def test_every_page_paints_a_limited_palette(qapp, tmp_path: Path) -> None:
    wizard = _wizard(tmp_path)
    wizard.resize(760, 520)
    wizard.show()
    qapp.processEvents()
    for step_id in wizard.page_ids():
        page = wizard.page_widget(step_id)
        image = page.grab().toImage()
        assert image.width() > 200 and image.height() > 150, (step_id, image.size())
        colours = _distinct_colours(image)
        # the frame, title bar, body text and scanline background: drawn in
        # code, deliberately few colours, no images
        assert 4 <= len(colours) <= 40, (step_id, len(colours))
    wizard.close()


def test_run_to_completion_skips_and_writes_config(qapp, tmp_path: Path) -> None:
    wizard = _wizard(tmp_path, _full_answers(tmp_path))
    outcome = wizard.run_to_completion()

    assert outcome.completed
    assert outcome.skipped == ["ltspice"]
    assert [step for step, _ in outcome.steps] == ["welcome", "provider", "policy", "finish"]
    assert outcome.config_path == tmp_path / "config.json"

    saved = json.loads(outcome.config_path.read_text(encoding="utf-8"))
    assert saved["provider_order"][0] == "HTTP_INFERENCE"
    assert saved["providers"]["http_inference"]["endpoint"].startswith("https://")
    assert saved["providers"]["http_inference"]["model"] == "example-model"
    assert saved["data_policy"]["allow_remote"] is True
    assert saved["data_policy"]["deny_unknown_classification"] is True
    assert SECRET not in outcome.config_path.read_text(encoding="utf-8")


def test_skipping_every_step_still_saves_the_config(qapp, tmp_path: Path) -> None:
    wizard = _wizard(tmp_path, {"skip": list(STEP_IDS)})
    outcome = wizard.run_to_completion()
    assert outcome.completed
    assert outcome.skipped == list(STEP_IDS)
    assert outcome.steps == []
    assert outcome.config_path.is_file()


@pytest.mark.ltspice
def test_failed_smoke_test_is_reported_as_a_failure(qapp, tmp_path: Path) -> None:
    calls: list[tuple[Path, Path]] = []

    def fake_smoke(exe: Path, workdir: Path, *, timeout_s: float = 60.0) -> SmokeResult:
        calls.append((exe, workdir))
        return SmokeResult(
            status="fail",
            detail="observed 0.100 V at 1 ms, outside 0.632 V +/- 2%",
            measured_v=0.1,
            expected_v=0.632,
            tolerance_pct=2.0,
            exit_code=0,
        )

    wizard = _wizard(
        tmp_path,
        _full_answers(tmp_path, skip=["welcome", "provider", "policy"]),
        smoke_runner=fake_smoke,
    )
    outcome = wizard.run_to_completion()

    assert calls, "the wizard must call simulation.ltspice.smoke_test"
    ltspice_step = dict(outcome.steps)["ltspice"]
    assert ltspice_step.startswith("fail:")
    assert "0.100 V" in ltspice_step
    body = "\n".join(wizard.body_lines("ltspice"))
    assert "SMOKE    FAIL" in body
    assert "MEASURED 0.100000 V" in body


def test_a_missing_ltspice_is_never_reported_as_a_pass(qapp, tmp_path: Path) -> None:
    wizard = _wizard(
        tmp_path,
        _full_answers(
            tmp_path,
            skip=["welcome", "provider", "policy"],
            ltspice_exe=str(tmp_path / "no-such-LTspice.exe"),
        ),
    )
    outcome = wizard.run_to_completion()
    detail = dict(outcome.steps)["ltspice"]
    assert detail.startswith("fail:")
    assert "not found" in detail
    assert "smoke test not run" in detail


@pytest.mark.ltspice
def test_real_smoke_test_result_is_reported_verbatim(qapp, tmp_path: Path) -> None:
    wizard = _wizard(tmp_path, _full_answers(tmp_path, skip=["welcome", "provider", "policy"]))
    outcome = wizard.run_to_completion()
    detail = dict(outcome.steps)["ltspice"]
    body = "\n".join(wizard.body_lines("ltspice"))
    if detail.startswith("pass:"):
        assert "SMOKE    PASS" in body
        assert "MEASURED" in body and " V" in body
    else:
        assert detail.startswith("fail:")
        assert "SMOKE    FAIL" in body


def test_secret_goes_to_the_keyring_only(qapp, tmp_path: Path, monkeypatch) -> None:
    stored: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "boardmodeler.ui.installer.set_credential",
        lambda name, value: stored.append((name, value)),
    )
    wizard = _wizard(tmp_path, _full_answers(tmp_path, api_key=SECRET))
    outcome = wizard.run_to_completion()

    assert stored == [("http_inference", SECRET)]
    assert wizard.credential_stored()
    detail = dict(outcome.steps)["provider"]
    assert "stored in the OS keyring" in detail
    text = outcome.config_path.read_text(encoding="utf-8")
    assert SECRET not in text
    assert "api_key" not in text


def test_keyring_failure_is_reported_not_hidden(qapp, tmp_path: Path, monkeypatch) -> None:
    def broken(name: str, value: str) -> None:
        raise RuntimeError("keyring backend unavailable")

    monkeypatch.setattr("boardmodeler.ui.installer.set_credential", broken)
    wizard = _wizard(tmp_path, _full_answers(tmp_path, api_key=SECRET))
    outcome = wizard.run_to_completion()

    detail = dict(outcome.steps)["provider"]
    assert "NOT STORED" in detail
    assert "keyring backend unavailable" in detail
    assert not wizard.credential_stored()
    assert SECRET not in outcome.config_path.read_text(encoding="utf-8")
    assert outcome.completed


def test_remote_inference_requires_the_privacy_acknowledgement(qapp, tmp_path: Path) -> None:
    wizard = _wizard(
        tmp_path, _full_answers(tmp_path, allow_remote=True, acknowledge_privacy=False)
    )
    outcome = wizard.run_to_completion()
    saved = json.loads(outcome.config_path.read_text(encoding="utf-8"))
    assert saved["data_policy"]["allow_remote"] is False
    assert "forced OFF" in dict(outcome.steps)["policy"]


def test_skip_current_marks_the_step(qapp, tmp_path: Path) -> None:
    wizard = _wizard(tmp_path)
    wizard.setCurrentId(0)
    wizard.skip_current()
    assert wizard.skipped_steps() == ["welcome"]
    assert wizard.currentPage().step_id == "ltspice"


def test_finish_page_lists_exact_commands(qapp, tmp_path: Path) -> None:
    wizard = _wizard(tmp_path, {"project_dir": str(tmp_path / "project")})
    commands = wizard.commands()
    assert "boardmodeler doctor --json" in commands[0]
    assert any(command.startswith("boardmodeler ui --project") for command in commands)
    assert any(command.startswith("boardmodeler run tests --project") for command in commands)
    assert "boardmodeler setup" in commands


def test_outcome_payload_is_json_serialisable(qapp, tmp_path: Path) -> None:
    wizard = _wizard(tmp_path, _full_answers(tmp_path))
    payload = outcome_payload(wizard.run_to_completion())
    assert set(payload) == {"completed", "config_path", "steps", "skipped"}
    assert json.loads(json.dumps(payload))["skipped"] == ["ltspice"]


def test_retro_font_is_fixed_pitch_and_unantialiased(qapp) -> None:
    font = retro_font(10, bold=True)
    assert font.family() == "Consolas"
    assert font.bold()
    assert font.styleStrategy() & font.StyleStrategy.NoAntialias
