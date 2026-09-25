"""The one network switch: off means no request is built, and nothing dials out.

Why a tripwire instead of just ``pytest.raises``: "the gate raised NetworkRefused" only
says the *branch* was taken. What the owner needs is that no socket was touched, so the
tests below replace ``socket.socket.connect``, ``socket.socket.connect_ex`` and
``socket.create_connection`` with something that fails the test if it is reached, then run
the product's real refusal paths — ``make_model`` (the pipeline entry point) and the
supporting-material stage — and assert both facts: the tripwire was never hit *and* the
reason the user sees names the SETUP switch.

The second half proves the gate is not an always-refuse branch: with the switch on,
``require_network`` returns, the API backend is really constructed, and the product's own
transport really dials — at a port on loopback that nothing listens on, with a guard that
fails the test on any non-loopback destination, so no packet can leave this machine.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from boardmodeler.pipeline import make_model as engine
from boardmodeler.pipeline.make_model import MakeModelRequest, build_backend
from boardmodeler.security.network import (
    NETWORK_ENV_VAR,
    NetworkRefused,
    internet_allowed,
    refusal_detail,
    require_network,
)


@pytest.fixture
def config_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """A config file whose single switch is off, with no environment pin involved."""
    from boardmodeler.config import AppConfig

    config = AppConfig()
    config.internet_access = False
    monkeypatch.setattr("boardmodeler.config.load_config", lambda *args, **kwargs: config)
    monkeypatch.delenv(NETWORK_ENV_VAR, raising=False)


@pytest.fixture
def config_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """A config file whose switch is on and no environment pin."""
    from boardmodeler.config import AppConfig

    monkeypatch.setattr("boardmodeler.config.load_config", lambda *args, **kwargs: AppConfig())
    monkeypatch.delenv(NETWORK_ENV_VAR, raising=False)
    # A proxy would be a real route out of the machine; loopback is excluded from it.
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost,::1")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")


@contextmanager
def tripwire() -> Iterator[list[str]]:
    """Fail the test if anything dials; ``reached`` stays empty in a passing run."""

    reached: list[str] = []

    def refuse(*args: Any, **kwargs: Any) -> Any:
        reached.append(repr(args[:1]))
        raise AssertionError("network attempt")

    originals = [
        (socket.socket, "connect", socket.socket.connect),
        (socket.socket, "connect_ex", socket.socket.connect_ex),
        (socket, "create_connection", socket.create_connection),
    ]
    for owner, name, _original in originals:
        setattr(owner, name, refuse)
    try:
        yield reached
    finally:
        for owner, name, original in originals:
            setattr(owner, name, original)


def _request(tmp_path: Path, **overrides: Any) -> MakeModelRequest:
    """A build request for the API author, which is the one that leaves the machine."""
    values: dict[str, Any] = {
        "part": "TPS54320",
        "subckt": "TPS54320",
        "datasheet": tmp_path / "datasheet.pdf",
        "out_dir": tmp_path / "out",
    }
    values.update(overrides)
    return MakeModelRequest(**values)


# --------------------------------------------------------------------------- #
# the switch itself


def test_the_file_switch_off_refuses_and_names_the_control(config_off: None) -> None:
    assert internet_allowed() is False
    with pytest.raises(NetworkRefused) as caught:
        require_network("authoring")
    refused = caught.value
    assert refused.code == "internet_access_off"
    assert refused.detail.startswith("internet_access_off:")
    assert "INTERNET ACCESS" in refused.detail
    assert NETWORK_ENV_VAR in refused.detail
    assert refused.detail == refusal_detail("authoring")


def test_the_environment_pin_forces_it_off_whatever_the_file_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from boardmodeler.config import AppConfig

    monkeypatch.setattr("boardmodeler.config.load_config", lambda *args, **kwargs: AppConfig())
    monkeypatch.setenv(NETWORK_ENV_VAR, "1")
    assert internet_allowed() is False
    with pytest.raises(NetworkRefused) as caught:
        require_network("the supporting-material search")
    assert "the supporting-material search" in caught.value.detail

    # A spelling nobody reads as "on" would be a silent way back to the network.
    monkeypatch.setenv(NETWORK_ENV_VAR, "yes")
    assert internet_allowed() is False
    # Only an explicit false spelling releases it; the file is on, so it is allowed.
    monkeypatch.setenv(NETWORK_ENV_VAR, "0")
    assert internet_allowed() is True


# --------------------------------------------------------------------------- #
# (c) the tripwire: switch off, nothing dials


def test_the_switch_off_stops_the_build_before_any_socket(config_off: None, tmp_path: Path) -> None:
    """The pipeline's own entry point refuses, immediately, with the switch's reason."""
    with tripwire() as reached:
        result = engine.make_model(_request(tmp_path))
    assert reached == [], f"the refusal path dialled out: {reached}"
    assert result.status == "BLOCKED"
    assert "internet_access_off" in result.detail
    assert "INTERNET ACCESS" in result.detail
    # Told at the start: none of the local stages ran, so the user is not made to wait.
    assert [event.stage for event in result.stages] == ["author"]


def test_the_switch_off_refuses_the_api_backend_but_not_the_offline_author(
    config_off: None, tmp_path: Path
) -> None:
    from boardmodeler.authoring.api_backend import build_api_backend

    with tripwire() as reached:
        api = build_backend(_request(tmp_path))
        usable, reason = api.availability()
        direct_api = build_api_backend("openai")
        direct_usable, direct_reason = direct_api.availability()
        offline = build_backend(_request(tmp_path, backend_name="fixture"))
    assert reached == [], f"building a backend dialled out: {reached}"
    assert usable is False
    assert "internet_access_off" in reason
    assert "INTERNET ACCESS" in reason
    assert direct_usable is False
    assert "internet_access_off" in direct_reason
    # The bundled offline author writes the template locally and must keep working: the
    # switch governs the network, not the product.
    assert "internet_access_off" not in offline.availability()[1]


@pytest.mark.parametrize("reinforce", [None, True])
def test_the_supporting_material_stage_skips_with_the_stated_reason(
    config_off: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reinforce: bool | None
) -> None:
    """The stage that reads the vendor's site must skip, not search with the switch off."""
    called: list[dict[str, Any]] = []

    def must_not_run(**kwargs: Any) -> Any:  # pragma: no cover - only on the failing path
        called.append(kwargs)
        raise AssertionError("the search ran with the switch off")

    monkeypatch.setattr(engine, "reinforce", must_not_run)
    events: list[engine.StageEvent] = []
    run = engine._Run(_request(tmp_path, reinforce=reinforce), engine._StageLog(events.append))
    with tripwire() as reached:
        run._gather_supporting_material(None)

    assert reached == [], f"the search dialled out: {reached}"
    assert called == [], "reinforce() must not be called at all"
    skipped = [event for event in events if event.stage == "reinforce"]
    assert skipped and skipped[-1].status == "skipped"
    assert "INTERNET ACCESS" in skipped[-1].detail


# --------------------------------------------------------------------------- #
# the switch on: the gate lets the same path through, hermetically


def _closed_loopback_port() -> int:
    """A port on loopback that nothing is listening on (unroutable, no egress)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_the_gate_is_not_an_always_refuse_branch(
    config_on: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Switch on: the same call passes the gate, builds the API backend, and dials."""
    from boardmodeler import agent_providers
    from boardmodeler.authoring.api_backend import env_sources
    from boardmodeler.providers.http_inference import HttpRequest, urllib_transport
    from boardmodeler.security import credentials

    monkeypatch.setattr(credentials, "_read_saved", lambda: None)
    for provider in agent_providers.CATALOG:
        for variable in env_sources(provider):
            monkeypatch.delenv(variable, raising=False)

    assert internet_allowed() is True
    require_network("authoring")  # must not raise

    api = build_backend(_request(tmp_path))
    usable, reason = api.availability()
    assert "internet_access_off" not in reason, reason
    # Without a key the backend is unusable for a different, honest reason — which is
    # itself proof the network refusal is not what stopped it.
    assert usable is False and "credential" in reason.lower(), reason

    # The product's own transport really opens a socket on loopback, and the OS refuses
    # the connection. A non-loopback destination fails this test instead of dialling.
    real_create = socket.create_connection

    def loopback_only(address: Any, *args: Any, **kwargs: Any) -> Any:
        host = str(address[0])
        assert host in {"127.0.0.1", "localhost", "::1"}, f"the test reached out to {host!r}"
        return real_create(address, *args, **kwargs)

    reached: list[tuple[str, int]] = []

    def record(address: Any, *args: Any, **kwargs: Any) -> Any:
        reached.append((str(address[0]), int(address[1])))
        return loopback_only(address, *args, **kwargs)

    socket.create_connection = record  # type: ignore[assignment]
    try:
        port = _closed_loopback_port()
        request = HttpRequest(
            method="POST",
            url=f"http://127.0.0.1:{port}/v1/chat/completions",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"model": "test", "messages": []}).encode("utf-8"),
            timeout_s=5.0,
        )
        with pytest.raises(OSError) as caught:
            urllib_transport(request)
    finally:
        socket.create_connection = real_create  # type: ignore[assignment]

    assert reached == [("127.0.0.1", port)], reached
    assert not isinstance(caught.value, NetworkRefused)
    assert "internet_access_off" not in str(caught.value)
