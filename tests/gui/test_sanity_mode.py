import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytestmark = pytest.mark.gui


def test_full_verification_is_a_window_choice_and_remembered(qtbot, tmp_path, monkeypatch):
    """The per-build choice lives in the window now, and the config only remembers it.

    It used to be SETUP's second checkbox; the repository's own rule puts per-build
    choices in the build window, so the box sits beside GO and writes the default the
    next build opens with.
    """
    from boardmodeler.config import AppConfig
    from boardmodeler.ui import model_maker as ui

    config = AppConfig()
    saved: list[AppConfig] = []
    monkeypatch.setattr("boardmodeler.config.load_config", lambda *a, **k: config)
    monkeypatch.setattr(
        "boardmodeler.config.save_config", lambda value, path=None: saved.append(value)
    )
    # The window reads the persisted default when it is built; go through a real one so
    # "opens with the remembered value" is what is being tested.
    config.full_verification = True
    window = ui.ModelMakerWindow()
    qtbot.addWidget(window)
    assert window.full_check.text() == "FULL VERIFICATION"
    assert window.full_check.isChecked() is True

    window.full_check.setChecked(False)
    assert saved and saved[-1].full_verification is False

    window.full_check.setChecked(True)
    assert saved[-1].full_verification is True


def test_quick_result_is_unverified_and_full_action_uses_worker(qtbot, tmp_path, monkeypatch):
    from boardmodeler.pipeline.make_model import MakeModelRequest, MakeModelResult
    from boardmodeler.ui import model_maker as ui

    window = ui.ModelMakerWindow()
    qtbot.addWidget(window)
    request = MakeModelRequest(
        "TEST", "TEST", tmp_path / "test.pdf", tmp_path, verification="sanity"
    )
    result = SimpleNamespace(
        status="UNKNOWN",
        detail="Sanity checked; electrical accuracy unverified",
        request=request,
        lib_path=tmp_path / "TEST.lib",
    )
    window._out_dir = tmp_path
    window._on_result(result)
    assert "accuracy unverified" in window.status_label.text()
    assert window.again_button.text() == "Run full verification"
    started = []
    monkeypatch.setattr(window, "_start", started.append)
    window.again_button.click()
    assert started[0].verification == "full"
    assert started[0].datasheet == request.datasheet
    persisted = MakeModelResult(
        "UNKNOWN", "quick", "TEST", tmp_path, None, None, None, (), {}, (), request
    )
    restored = MakeModelResult.from_json(persisted.to_json())
    assert restored.request is not None
    assert restored.request.verification == "sanity"


def test_go_passes_the_windows_verification_choice(qtbot, tmp_path, monkeypatch):
    """What GO sends is the box's state, not a setting read from the config file."""
    from boardmodeler.pipeline.make_model import MakeModelRequest
    from boardmodeler.ui import model_maker as ui

    datasheet = tmp_path / "part.pdf"
    datasheet.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(
        "boardmodeler.config.load_config", lambda *a, **k: _config_with(full_verification=False)
    )
    monkeypatch.setattr("boardmodeler.config.save_config", lambda value, path=None: None)
    monkeypatch.setattr(ui, "_agent_availability", lambda: (True, "ok"))
    monkeypatch.setattr(ui, "_configured_provider", lambda: None)
    monkeypatch.setattr(ui, "_configured_provider_id", lambda: "")
    started: list[MakeModelRequest] = []

    window = ui.ModelMakerWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_start", started.append)
    window.part_edit.setText("TPS54320")
    window.datasheet_edit.setText(str(datasheet))

    window.full_check.setChecked(False)
    window.go_button.click()
    assert started[-1].verification == "sanity"

    window.full_check.setChecked(True)
    window.go_button.click()
    assert started[-1].verification == "full"


def _config_with(**values):
    """An :class:`AppConfig` with overrides, for the two tests above."""
    from boardmodeler.config import AppConfig

    return AppConfig(**values)
