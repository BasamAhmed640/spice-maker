"""Hashing helpers.

All content addressing in BoardModeler (document identity, model identity,
extraction cache keys, baseline hashes) goes through these functions so that
two code paths can never disagree about what "the same bytes" means.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

__all__ = ["canonical_json_bytes", "sha256_bytes", "sha256_file", "sha256_text"]

_CHUNK = 1 << 20


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase hex SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Return the SHA-256 of ``text`` encoded as UTF-8 with no newline translation."""
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: str | os.PathLike[str], *, chunk_size: int = _CHUNK) -> str:
    """Return the lowercase hex SHA-256 of a file's bytes.

    Reads in chunks so that large vendor models and PDFs do not have to fit in
    memory. Raises ``OSError`` (never a fabricated digest) if the file is
    unreadable.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize ``value`` to stable UTF-8 JSON bytes.

    Sort keys, no insignificant whitespace, no NaN/Infinity. Used for hashes of
    pydantic dumps and for cache keys, so that dict ordering never changes a
    hash.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
