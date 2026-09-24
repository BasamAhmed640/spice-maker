"""Shared pytest fixtures.

The application never searches for LTspice. A test session may supply an explicit
``LTSPICE_EXE`` fixture path; this file writes that path to a disposable project
configuration so in-process and CLI tests use the same selected executable.

``ltspice_exe`` skips (never fakes) when the simulator is unavailable; tests that
assert simulator behaviour must be able to prove they ran against a real
executable, so the fixture also exposes the located path.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from boardmodeler.config import AppConfig, LtspiceConfig, save_config
from boardmodeler.simulation.ltspice import LtspiceInstall, locate

#: Test-only input, never read by the application as a simulator path.
_CONFIGURED_ENV = "LTSPICE_EXE"


def _resolve_install() -> LtspiceInstall | None:
    """The session's simulator: saved configuration or explicit test input only."""
    supplied = os.environ.get(_CONFIGURED_ENV)
    install = LtspiceInstall(Path(supplied), "test-input") if supplied else locate()
    if install is None or not install.path.is_file():
        return None
    return install


def _no_simulator_message(install: LtspiceInstall | None) -> str:
    """Say what is true: nothing was configured/found, and how to configure one."""
    if install is None:
        return (
            "no LTspice executable was configured, and the test session did not "
            f"configure one; set {_CONFIGURED_ENV} to an LTspice.exe to run this test"
        )
    return (
        f"the LTspice candidate {install.path} does not exist, and the test session did "
        f"not configure one; set {_CONFIGURED_ENV} to an LTspice.exe to run this test"
    )


@pytest.fixture(scope="session")
def session_ltspice_install() -> LtspiceInstall | None:
    """Resolve the session's simulator once; ``None`` is a result, not a skip."""
    return _resolve_install()


@pytest.fixture(scope="session", autouse=True)
def session_ltspice_env(
    session_ltspice_install: LtspiceInstall | None, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    """Save one disposable LTspice setting for tests that launch child CLIs."""
    if session_ltspice_install is None:
        yield
        return
    config_path = tmp_path_factory.mktemp("configured-ltspice") / "config.json"
    save_config(
        AppConfig(ltspice=LtspiceConfig(path=str(session_ltspice_install.path))), config_path
    )
    patch = pytest.MonkeyPatch()
    patch.setenv("BOARDMODELER_CONFIG", str(config_path))
    try:
        yield
    finally:
        patch.undo()


@pytest.fixture(scope="session")
def ltspice_install(session_ltspice_install: LtspiceInstall | None) -> LtspiceInstall:
    if session_ltspice_install is None:
        pytest.skip(_no_simulator_message(None))
    if not session_ltspice_install.path.is_file():
        pytest.skip(_no_simulator_message(session_ltspice_install))
    return session_ltspice_install


@pytest.fixture(scope="session")
def ltspice_exe(ltspice_install: LtspiceInstall) -> Path:
    return ltspice_install.path


@pytest.fixture(autouse=True)
def isolated_credential_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "boardmodeler.security.credentials.credential_path",
        lambda: tmp_path / "credentials.json",
    )
