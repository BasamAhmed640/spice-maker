"""Hash-keyed extraction cache (D11, AGENTS rule 6).

One JSON document per key under ``<root>/<key>.json``:

```json
{"key": "...", "provider": "...", "model": null, "task": "REQUIREMENTS",
 "payload": {...}, "notes": null, "created_utc": "2026-09-18T00:00:00Z"}
```

The key is :func:`boardmodeler.providers.base.request_hash`, which covers every
input that changes what a provider would see. Reusing an entry therefore
requires the provider, model, task, prompt, schema *and* the exact page text to
be identical; anything else misses and the provider is asked again. Nothing here
can invent a payload — a corrupt or mismatched entry raises instead of being
silently accepted.

Writes are deterministic (``sort_keys=True``, LF endings, one trailing newline)
and atomic (temp file beside the target, then ``os.replace``), so an interrupted
run never leaves a half-written cache entry behind.

The file layout matches :meth:`boardmodeler.providers.fixture.FixtureProvider.write_fixture`
on purpose: a committed fixture and a cache entry are the same artifact, so a
replayed fixture can be moved into the cache and vice versa.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from boardmodeler.domain.hashing import canonical_json_bytes
from boardmodeler.security.paths import resolve_within

__all__ = ["ExtractionCache", "ExtractionCacheError"]

_KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class ExtractionCacheError(RuntimeError):
    """A cache entry exists but cannot be trusted (unreadable, or a different key)."""


class ExtractionCache:
    """A directory of hash-keyed extraction payloads, with hit/miss counters."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.hits = 0
        self.misses = 0
        self.written = 0

    # ------------------------------------------------------------------ paths

    def path_for(self, key: str) -> Path:
        """The file holding ``key`` (containment-checked; the file may not exist)."""
        return resolve_within(self.root, f"{_check_key(key)}.json")

    def contains(self, key: str) -> bool:
        """Whether an entry for ``key`` exists (does not count as a hit)."""
        return self.path_for(key).is_file()

    # ------------------------------------------------------------------ access

    def get(self, key: str) -> dict[str, Any] | None:
        """The cached payload for ``key``, or ``None`` when there is no entry.

        A file that exists but is not a valid entry (bad JSON, missing payload,
        or a recorded key that differs from its file name) raises
        :class:`ExtractionCacheError`: a corrupt cache must never be mistaken for
        a miss, and must never be returned as an answer.
        """
        path = self.path_for(key)
        if not path.is_file():
            self.misses += 1
            return None
        text = path.read_text(encoding="utf-8")
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ExtractionCacheError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(document, dict) or not isinstance(document.get("payload"), dict):
            raise ExtractionCacheError(f"{path} must be a JSON object with an object 'payload'")
        recorded = document.get("key")
        if recorded != key:
            raise ExtractionCacheError(
                f"{path} records key {recorded!r} but was looked up as {key!r}"
            )
        self.hits += 1
        return document["payload"]

    def put(
        self,
        key: str,
        payload: Mapping[str, Any],
        *,
        provider: str,
        model: str | None,
        task: str,
        notes: str | None = None,
    ) -> Path:
        """Write ``payload`` for ``key`` and return the file written."""
        path = self.path_for(key)
        _check_payload(payload)
        document = {
            "key": key,
            "provider": provider,
            "model": model,
            "task": task.value if hasattr(task, "value") else str(task),
            "payload": dict(payload),
            "notes": notes,
            "created_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
        tmp.write_text(
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(tmp, path)
        self.written += 1
        return path

    def stats(self) -> dict[str, int]:
        """``{"entries", "hits", "misses"}`` — entries on disk, lookups in memory."""
        entries = 0
        if self.root.is_dir():
            entries = sum(1 for path in self.root.glob("*.json") if path.is_file())
        return {"entries": entries, "hits": self.hits, "misses": self.misses}


def _check_key(key: str) -> str:
    if not isinstance(key, str) or not _KEY_PATTERN.fullmatch(key):
        raise ValueError(
            f"cache key must match {_KEY_PATTERN.pattern!r} (a request hash), got {key!r}"
        )
    return key


def _check_payload(payload: Mapping[str, Any]) -> None:
    if not isinstance(payload, Mapping):
        raise TypeError(f"payload must be a mapping, got {type(payload).__name__}")
    try:
        canonical_json_bytes(dict(payload))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"payload is not JSON-serializable: {type(exc).__name__}: {exc}") from exc
