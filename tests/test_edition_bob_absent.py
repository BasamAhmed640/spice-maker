"""Structural immunity: the shipped general edition has no reachable Bob.

These tests do not stub the catalog or the factory. They exercise the objects the
general build actually ships — the catalog, the backend constructor, the engine, the
CLI parser and the text setup — and assert that Bob is absent or refused without a
process being created, a PATH entry read or a key looked up. If any of those surfaces
started reaching Bob again, this module fails first.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


def _bomb(*args: object, **kwargs: object) -> object:
    """A stand-in that fails the test the moment an unreachable call is reached."""
    raise AssertionError("a general build must not reach this call")


def test_the_provider_catalog_has_no_bob_entry() -> None:
    from boardmodeler import agent_providers

    assert "bob" not in [provider.id for provider in agent_providers.CATALOG]


def test_the_default_provider_is_not_bob() -> None:
    from boardmodeler import agent_providers

    assert agent_providers.default_provider().id != "bob"


def test_no_catalog_entry_uses_a_cli_wire() -> None:
    from boardmodeler import agent_providers

    assert [provider.id for provider in agent_providers.CATALOG if provider.uses_cli] == []
    assert "bob-shell" not in agent_providers.WIRES


def test_bob_shell_backend_refuses_before_any_key_or_process(monkeypatch) -> None:
    from boardmodeler.authoring import backends

    monkeypatch.setattr(backends.shutil, "which", _bomb)
    monkeypatch.setattr(backends, "get_credential", _bomb)
    monkeypatch.setattr(subprocess, "Popen", _bomb)
    monkeypatch.setattr(subprocess, "run", _bomb)

    with pytest.raises(RuntimeError) as info:
        backends.BobShellBackend(team_id="team-1", timeout_s=5.0)

    assert "general edition does not include Bob Shell" in str(info.value)


def test_the_bob_key_check_refuses_without_spawning_anything(monkeypatch) -> None:
    from boardmodeler.authoring import backends as author_backends
    from boardmodeler.security import key_verification

    monkeypatch.setattr(key_verification.shutil, "which", _bomb)
    monkeypatch.setattr(author_backends, "run_bob_shell", _bomb)

    result = key_verification._verify_bob("secret", 15.0, None)

    assert result.status == "unverified"
    assert "general edition" in result.detail


def test_the_engine_refuses_the_bob_backend_name(tmp_path: Path) -> None:
    from boardmodeler.pipeline.make_model import MakeModelRequest, build_backend

    request = MakeModelRequest(
        part="TPS54320",
        subckt="BM_REG_BUCK",
        datasheet=tmp_path / "datasheet.pdf",
        out_dir=tmp_path / "out",
        backend_name="bob",
    )

    backend = build_backend(request)
    usable, reason = backend.availability()

    assert usable is False
    assert reason.startswith("bob_backend_unavailable:")
    assert "general edition" in reason


def test_the_legacy_provider_registry_refuses_the_bob_kinds() -> None:
    from boardmodeler.config import AppConfig
    from boardmodeler.providers.base import ProviderError
    from boardmodeler.providers.registry import build_provider

    with pytest.raises(ProviderError) as info:
        build_provider(AppConfig(), name="bob_direct")

    assert info.value.code == "bob_not_in_this_edition"
    assert "Bob-only edition" in info.value.detail


def test_doctor_json_lists_no_bob_provider(capsys) -> None:
    from boardmodeler import agent_providers
    from boardmodeler.cli import main

    code = main(["doctor", "--json", "--no-smoke"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert set(payload["credentials"]) == set(agent_providers.ids())
    assert "bob" not in payload["credentials"]


def test_the_cli_refuses_backend_bob_without_a_traceback(tmp_path: Path, capsys) -> None:
    from boardmodeler.cli import main

    with pytest.raises(SystemExit) as info:
        main(
            [
                "model",
                "build",
                "--part",
                "TPS54320",
                "--out",
                str(tmp_path),
                "--backend",
                "bob",
                "--json",
            ]
        )
    captured = capsys.readouterr()
    message = captured.err + captured.out

    assert info.value.code != 0
    assert "bob" in message
    assert "Traceback" not in message


def test_terminal_setup_has_no_bob_shell_switch(capsys) -> None:
    from boardmodeler.config import AppConfig
    from boardmodeler.setup_wizard import main

    assert "allow_bob_shell" not in AppConfig.model_fields
    with pytest.raises(SystemExit) as info:
        main(["--allow-bob-shell"])
    assert info.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_terminal_setup_refuses_bob_provider() -> None:
    from boardmodeler import agent_providers
    from boardmodeler.config import AppConfig
    from boardmodeler.setup_wizard import SetupError, _provider

    assert "bob" not in agent_providers.ids()
    with pytest.raises(SetupError, match="unavailable"):
        _provider("bob", AppConfig(), yes=True)
