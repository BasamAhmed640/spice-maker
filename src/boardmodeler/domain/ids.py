"""Identifier construction.

Ids are deterministic where determinism helps (requirement and test ids are
referenced from baselines and reports) and unique where it does not matter
(run ids carry a random suffix so two runs can never collide on disk).
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

__all__ = ["make_req_id", "make_test_id", "normalize_slug", "run_id", "utc_now_iso"]

_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


def normalize_slug(text: str, *, max_len: int = 40, fallback: str = "x") -> str:
    """Reduce ``text`` to ``[A-Za-z0-9_]`` suitable for use inside an id."""
    slug = _SLUG_RE.sub("_", text.strip()).strip("_")
    if not slug:
        return fallback
    return slug[:max_len]


def make_req_id(part: str, kind: str, ordinal: int) -> str:
    """Build a stable requirement id, e.g. ``REQ_TPS54320_ELEC_004``."""
    if ordinal < 0:
        raise ValueError("ordinal must be >= 0")
    return f"REQ_{normalize_slug(part, max_len=24)}_{normalize_slug(kind, max_len=12).upper()}_{ordinal:03d}"


def make_test_id(scenario: str, ordinal: int) -> str:
    """Build a stable test id, e.g. ``T_nominal_startup_002``."""
    if ordinal < 0:
        raise ValueError("ordinal must be >= 0")
    return f"T_{normalize_slug(scenario, max_len=48)}_{ordinal:03d}"


def run_id(prefix: str = "run", *, now: datetime | None = None) -> str:
    """Build a filesystem-safe run id: ``<prefix>_<utc-stamp>_<4 hex>``."""
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{normalize_slug(prefix, max_len=24)}_{stamp}_{secrets.token_hex(2)}"


def utc_now_iso() -> str:
    """Current UTC time as ``YYYY-MM-DDTHH:MM:SSZ`` (second resolution)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
