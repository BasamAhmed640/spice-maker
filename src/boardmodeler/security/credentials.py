"""Credential access (D11, AGENTS rule 7).

Lookup precedence for one credential name:

1. the OS keyring entry ``boardmodeler`` / ``provider:<name>:api_key``
2. the ``BOARDMODELER_<NAME>_API_KEY`` environment variable (CI fallback)
3. missing — returned as a :class:`Credential` with ``source=MISSING`` and the
   observed reason, never as an exception

Keyring failures (no backend, locked collection, unavailable vault, keyring not
importable) degrade to the environment fallback with the failure recorded in
``detail``; ``get_credential`` therefore never raises for a missing secret.
Values are never logged, written into project files, or exported: only
``source`` is reported, and :func:`redact` exists to scrub values that leak into
provider error text.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

try:  # a broken keyring install must not stop the application from starting
    import keyring
except Exception:  # pragma: no cover - import failure is environment-specific
    keyring = None

__all__ = [
    "SERVICE_NAME",
    "Credential",
    "SecretSource",
    "credential_key",
    "delete_credential",
    "describe_credential",
    "env_var_name",
    "get_credential",
    "redact",
    "set_credential",
]

SERVICE_NAME = "boardmodeler"
REDACTED = "[REDACTED]"
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")


class SecretSource(StrEnum):
    """Where a credential value came from."""

    KEYRING = "KEYRING"
    ENV = "ENV"
    MISSING = "MISSING"


@dataclass(frozen=True)
class Credential:
    """One credential lookup result.

    ``value`` is ``None`` unless a source supplied it; ``detail`` explains the
    lookup outcome and never contains a secret value.
    """

    name: str
    value: str | None
    source: SecretSource
    detail: str


def credential_key(name: str) -> str:
    """Keyring key for provider ``name``."""
    return f"provider:{name}:api_key"


def env_var_name(name: str) -> str:
    """Environment fallback variable: ``BOARDMODELER_<NAME>_API_KEY``.

    The name is upper-cased and every non-alphanumeric character becomes ``_``,
    so ``bob-direct`` and ``bob_direct`` both map to
    ``BOARDMODELER_BOB_DIRECT_API_KEY``.
    """
    return f"BOARDMODELER_{_NON_ALNUM.sub('_', name).upper()}_API_KEY"


def _require_keyring() -> Any:
    if keyring is None:
        raise RuntimeError(
            "the keyring package is not importable; cannot store credentials "
            f"(set {env_var_name('<name>')} for CI instead)"
        )
    return keyring


def get_credential(name: str, *, keyring_backend: Any | None = None) -> Credential:
    """Look up ``name`` without raising for a missing or broken keyring.

    ``keyring_backend`` replaces the process keyring for this call (tests inject
    fakes; production leaves it ``None``). Any exception from the backend is
    captured in ``detail`` and the environment fallback is still consulted.
    """
    key = credential_key(name)
    backend = keyring_backend if keyring_backend is not None else keyring
    backend_error: str | None = None
    value: str | None = None
    if backend is None:
        backend_error = "the keyring package is not importable in this environment"
    else:
        try:
            value = backend.get_password(SERVICE_NAME, key)
        except Exception as exc:  # keyring backends raise platform-specific errors
            backend_error = f"{type(exc).__name__}: {exc}"

    if value:
        return Credential(
            name=name,
            value=value,
            source=SecretSource.KEYRING,
            detail=f"keyring service={SERVICE_NAME!r} key={key!r}",
        )

    variable = env_var_name(name)
    env_value = os.environ.get(variable)
    if env_value:
        detail = f"environment variable {variable}"
        if backend_error:
            detail += f" (keyring lookup failed: {backend_error})"
        return Credential(name=name, value=env_value, source=SecretSource.ENV, detail=detail)

    if backend_error:
        detail = f"no keyring value for key {key!r} ({backend_error}) and {variable} is not set"
    else:
        detail = f"no keyring value for key {key!r} and {variable} is not set"
    return Credential(name=name, value=None, source=SecretSource.MISSING, detail=detail)


def set_credential(name: str, value: str) -> None:
    """Store ``name`` in the OS keyring.

    Raises ``ValueError`` for an empty value and ``RuntimeError``/keyring errors
    when no backend is usable — an explicit user action must fail loudly rather
    than silently discard a secret.
    """
    if not value:
        raise ValueError("refusing to store an empty credential value")
    _require_keyring().set_password(SERVICE_NAME, credential_key(name), value)


def delete_credential(name: str) -> None:
    """Delete ``name`` from the OS keyring.

    A keyring that has no such entry raises ``keyring.errors.PasswordDeleteError``;
    that is propagated rather than reported as success.
    """
    _require_keyring().delete_password(SERVICE_NAME, credential_key(name))


def describe_credential(name: str) -> str:
    """Log/CLI-safe description of a credential: the source only, never the value."""
    credential = get_credential(name)
    return f"credential {name!r}: source={credential.source.value.lower()}"


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace every occurrence of each secret in ``text`` with ``[REDACTED]``.

    Secrets are removed longest-first so one secret contained in another is
    replaced as a unit, and empty/whitespace-only entries are ignored so they
    cannot mangle the whole message.
    """
    result = text
    for secret in sorted((s for s in secrets if s and s.strip()), key=len, reverse=True):
        result = result.replace(secret, REDACTED)
    return result
