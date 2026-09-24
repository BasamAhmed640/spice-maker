"""LTspice is visible only through a path chosen for this project copy."""

from __future__ import annotations

from pathlib import Path

import pytest

from boardmodeler.config import AppConfig, LtspiceConfig, save_config
from boardmodeler.simulation import ltspice


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    target = tmp_path / "config.json"
    monkeypatch.delenv("SPICE_MAKER_ROOT", raising=False)
    monkeypatch.setattr("boardmodeler.config.config_path", lambda: target)
    monkeypatch.setattr("boardmodeler.cli.config_path", lambda: target)
    return target


def test_unset_ignores_inherited_environment_without_probing(
    isolated_config, tmp_path, monkeypatch
):
    ambient = tmp_path / "LTspice.exe"
    ambient.write_bytes(b"test fixture")
    monkeypatch.setenv("LTSPICE_EXE", str(ambient))
    real_is_file = Path.is_file

    def guarded(path):
        assert path != ambient, "an inherited LTSPICE_EXE path was probed"
        return real_is_file(path)

    monkeypatch.setattr(Path, "is_file", guarded)
    outcome = ltspice.locate_outcome()
    assert outcome.reason == "unset"
    assert outcome.install is None and outcome.probed == []


def test_saved_path_is_used(isolated_config, tmp_path):
    exe = tmp_path / "LTspice.exe"
    exe.write_bytes(b"test fixture")
    save_config(AppConfig(ltspice=LtspiceConfig(path=str(exe))), isolated_config)
    outcome = ltspice.locate_outcome()
    assert outcome.reason == "config" and outcome.install is not None
    assert outcome.install.path == exe
    assert ltspice.locate() == outcome.install


def test_explicit_argument_selects_only_that_path(isolated_config, tmp_path):
    saved = tmp_path / "saved.exe"
    chosen = tmp_path / "chosen.exe"
    saved.write_bytes(b"test fixture")
    chosen.write_bytes(b"test fixture")
    save_config(AppConfig(ltspice=LtspiceConfig(path=str(saved))), isolated_config)
    outcome = ltspice.locate_outcome(chosen)
    assert outcome.reason == "configured" and outcome.install is not None
    assert outcome.install.path == chosen


def test_missing_saved_path_never_falls_back_to_environment(isolated_config, tmp_path, monkeypatch):
    missing = tmp_path / "missing.exe"
    ambient = tmp_path / "ambient.exe"
    ambient.write_bytes(b"test fixture")
    save_config(AppConfig(ltspice=LtspiceConfig(path=str(missing))), isolated_config)
    monkeypatch.setenv("LTSPICE_EXE", str(ambient))
    outcome = ltspice.locate_outcome()
    assert outcome.install is None and outcome.reason == "config_missing"
    assert outcome.probed == [(missing, "config")]


def test_default_library_does_not_probe_user_profile(isolated_config, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "profile"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "profile"))
    assert ltspice.default_lib_dir() is None
    library = tmp_path / "chosen-library"
    save_config(AppConfig(ltspice=LtspiceConfig(lib_dir=str(library))), isolated_config)
    assert ltspice.default_lib_dir() == library


def test_doctor_reports_setup_required_without_search(isolated_config, monkeypatch, tmp_path):
    from boardmodeler.cli import doctor_payload

    ambient = tmp_path / "LTspice.exe"
    ambient.write_bytes(b"test fixture")
    monkeypatch.setenv("LTSPICE_EXE", str(ambient))
    payload = doctor_payload(run_smoke=False)
    section = payload["ltspice"]
    assert section["found"] is False and section["path"] is None
    assert section["reason"] == "unset" and section["setup_required"] is True
    assert section["probed"] == [] and section["searched"] is False
    assert "SETUP" in section["smoke_detail"]
