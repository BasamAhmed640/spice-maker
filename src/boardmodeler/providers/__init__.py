"""Extraction providers (D11).

``base`` defines the contract every provider satisfies; ``fixture`` replays
committed JSON and is the default for every automated test; ``registry`` builds
and selects providers explicitly, never silently falling back. The HTTP and Bob
adapters are added in a later phase behind the same contract and are imported
lazily by the registry.
"""

from __future__ import annotations

from boardmodeler.providers.base import (
    MAX_SNIPPET_CHARS,
    DocSnippet,
    ExtractionRequest,
    ExtractionResponse,
    ExtractionTask,
    Provider,
    ProviderCapabilities,
    ProviderError,
    ProviderHealth,
    build_prompt,
    request_hash,
)

__all__ = [
    "MAX_SNIPPET_CHARS",
    "DocSnippet",
    "ExtractionRequest",
    "ExtractionResponse",
    "ExtractionTask",
    "Provider",
    "ProviderCapabilities",
    "ProviderError",
    "ProviderHealth",
    "build_prompt",
    "request_hash",
]
