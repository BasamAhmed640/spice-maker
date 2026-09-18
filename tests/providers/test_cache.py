"""Extraction cache (D11, AGENTS rule 6).

The cache is what makes a repeat run perform zero inference requests, so these
tests pin the properties that makes it trustworthy: an exact round trip, a
content-addressed single file per key, deterministic bytes, atomic writes, and a
loud failure for a corrupt or mismatched entry (never a silent miss).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from boardmodeler.providers.base import ExtractionTask
from boardmodeler.providers.cache import ExtractionCache, ExtractionCacheError

KEY = "a" * 64
OTHER_KEY = "b" * 64
PAYLOAD = {"requirements": [{"req_id": "REQ_1", "statement": "input range 4.5 V to 60 V"}]}


def test_round_trip_keeps_the_payload_exactly(tmp_path: Path) -> None:
    cache = ExtractionCache(tmp_path / "cache")
    assert cache.get(KEY) is None
    path = cache.put(
        KEY, PAYLOAD, provider="fixture", model=None, task=ExtractionTask.REQUIREMENTS, notes="n"
    )
    assert path.name == f"{KEY}.json"
    assert json.loads(path.read_text(encoding="utf-8"))["payload"] == PAYLOAD
    assert cache.get(KEY) == PAYLOAD
    assert cache.stats() == {"entries": 1, "hits": 1, "misses": 1}


def test_entry_file_is_deterministic_and_has_the_agreed_shape(tmp_path: Path) -> None:
    cache = ExtractionCache(tmp_path)
    path = cache.put(KEY, PAYLOAD, provider="fixture", model="m", task="REQUIREMENTS", notes=None)
    text = path.read_text(encoding="utf-8")
    stored = json.loads(text)
    assert set(stored) == {"key", "provider", "model", "task", "payload", "notes", "created_utc"}
    assert stored["key"] == KEY
    assert stored["provider"] == "fixture"
    assert stored["model"] == "m"
    assert stored["task"] == "REQUIREMENTS"
    assert stored["notes"] is None
    assert list(stored) == sorted(stored), "keys must be written sorted (byte-stable diffs)"
    assert text.endswith("\n")
    assert "\r" not in text


def test_put_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    cache = ExtractionCache(tmp_path)
    cache.put(KEY, PAYLOAD, provider="p", model=None, task="IDENTITY")
    assert [path.name for path in sorted(tmp_path.iterdir())] == [f"{KEY}.json"]


def test_corrupt_entry_is_an_error_not_a_miss(tmp_path: Path) -> None:
    cache = ExtractionCache(tmp_path)
    (tmp_path / f"{KEY}.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ExtractionCacheError):
        cache.get(KEY)
    assert cache.stats()["misses"] == 0, "a corrupt entry must not be counted as a miss"


def test_entry_recorded_under_a_different_key_is_refused(tmp_path: Path) -> None:
    cache = ExtractionCache(tmp_path)
    path = cache.put(KEY, PAYLOAD, provider="p", model=None, task="IDENTITY")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["key"] = OTHER_KEY
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ExtractionCacheError, match="records key"):
        cache.get(KEY)


def test_payload_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    cache = ExtractionCache(tmp_path)
    (tmp_path / f"{KEY}.json").write_text(json.dumps({"key": KEY, "payload": [1, 2]}))
    with pytest.raises(ExtractionCacheError):
        cache.get(KEY)


@pytest.mark.parametrize("key", ["", "../escape", "a" * 200, "sub/dir", "a b"])
def test_unsafe_keys_are_rejected(tmp_path: Path, key: str) -> None:
    cache = ExtractionCache(tmp_path)
    with pytest.raises(ValueError):
        cache.get(key)


def test_distinct_keys_are_distinct_entries(tmp_path: Path) -> None:
    cache = ExtractionCache(tmp_path)
    cache.put(KEY, PAYLOAD, provider="p", model=None, task="IDENTITY")
    cache.put(OTHER_KEY, {"pins": []}, provider="p", model=None, task="PINMAP")
    assert cache.get(KEY) == PAYLOAD
    assert cache.get(OTHER_KEY) == {"pins": []}
    assert cache.stats()["entries"] == 2


def test_contains_does_not_count_as_a_lookup(tmp_path: Path) -> None:
    cache = ExtractionCache(tmp_path)
    assert cache.contains(KEY) is False
    cache.put(KEY, PAYLOAD, provider="p", model=None, task="IDENTITY")
    assert cache.contains(KEY) is True
    assert cache.stats()["hits"] == 0
