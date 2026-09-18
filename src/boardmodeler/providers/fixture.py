"""Deterministic fixture provider (D11).

Fixtures are committed JSON keyed by the content hash of the extraction request
(:func:`boardmodeler.providers.base.request_hash`), so replay is deterministic,
a repeat extraction makes zero inference requests, and a changed prompt, task,
schema, provider, or snippet can never silently reuse an old answer — the key
changes and the lookup misses with ``fixture_missing``.

This provider is the default for every automated test; it never opens a socket.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from boardmodeler.domain.enums import ProviderKind
from boardmodeler.domain.records import ProviderIdentity
from boardmodeler.providers.base import (
    MAX_SNIPPET_CHARS,
    ExtractionRequest,
    ExtractionResponse,
    ProviderCapabilities,
    ProviderError,
    ProviderHealth,
    request_hash,
)

__all__ = ["FixtureProvider"]


class FixtureProvider:
    """Replays committed JSON fixtures keyed by request hash."""

    def __init__(self, fixture_dir: str | Path, *, name: str = "fixture") -> None:
        if not name:
            raise ValueError("provider name must be non-empty")
        self.fixture_dir = Path(fixture_dir)
        self.name = name
        self.requests: list[str] = []
        """Lookup keys in call order — the work this provider did, for tests."""

    def key_for(self, request: ExtractionRequest) -> str:
        """The fixture key (and cache key) for ``request``."""
        return request_hash(request, provider=self.name, model=None)

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            provider=self.name,
            kind=ProviderKind.FIXTURE,
            model=None,
            endpoint=None,
            usage_units="none",
            usage={},
            detail={"fixture_dir": str(self.fixture_dir), "mode": "deterministic_replay"},
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            structured_output=True,
            max_snippet_chars_pages=MAX_SNIPPET_CHARS,
            streaming=False,
            usage_units="none",
            notes=(
                "deterministic replay of committed JSON fixtures; no inference, "
                "no network, zero requests on replay"
            ),
        )

    def health(self, timeout_s: float) -> ProviderHealth:
        if self.fixture_dir.is_dir():
            return ProviderHealth(
                ok=True,
                code="ok",
                detail=f"fixture directory {self.fixture_dir} is present",
            )
        return ProviderHealth(
            ok=False,
            code="fixture_dir_missing",
            detail=(
                f"fixture directory {self.fixture_dir} does not exist; "
                "extraction will report fixture_missing until fixtures are authored"
            ),
        )

    def extract(
        self, request: ExtractionRequest, cancel: threading.Event | None = None
    ) -> ExtractionResponse:
        if cancel is not None and cancel.is_set():
            raise ProviderError(
                "cancelled", f"cancelled before fixture lookup ({request.task.value})"
            )
        key = self.key_for(request)
        self.requests.append(key)
        path = self.fixture_dir / f"{key}.json"
        if not path.is_file():
            raise ProviderError(
                "fixture_missing",
                f"no fixture for request key {key}: {path} does not exist "
                "(author it with write_fixture, or point fixture_dir at the committed fixtures)",
            )
        text = path.read_text(encoding="utf-8")
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError("fixture_invalid", f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(document, dict) or not isinstance(document.get("payload"), dict):
            raise ProviderError(
                "fixture_invalid", f"{path} must be a JSON object with an object 'payload'"
            )
        recorded_key = document.get("key")
        if recorded_key != key:
            raise ProviderError(
                "fixture_mismatch",
                f"{path} records key {recorded_key!r} but the request hashes to {key!r}",
            )
        if cancel is not None and cancel.is_set():
            raise ProviderError("cancelled", f"cancelled before returning fixture {path.name}")

        notes = document.get("notes")
        detail = notes if isinstance(notes, str) and notes else f"replayed {path.name}"
        return ExtractionResponse(
            payload=document["payload"],
            identity=self.identity(),
            raw_text=text,
            from_cache=False,
            request_hash=key,
            detail=detail,
        )

    def write_fixture(
        self, request: ExtractionRequest, payload: dict[str, Any], *, notes: str | None = None
    ) -> Path:
        """Author (or refresh) the committed fixture for ``request``.

        ``payload`` must be JSON-serializable; it is written sorted and
        byte-stable so the committed fixture diffs cleanly. Replay returns the
        payload exactly as authored.
        """
        key = self.key_for(request)
        document = {
            "key": key,
            "provider": self.name,
            "model": None,
            "task": request.task.value,
            "payload": payload,
            "notes": notes,
            "created_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        path = self.fixture_dir / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path
