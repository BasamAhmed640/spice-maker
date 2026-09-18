"""Credential handling (D11, AGENTS rule 7).

These tests pin the observable contract: precedence (keyring over env), honest
degradation when the keyring is broken, key/value normalization of the env
fallback, and that no surface ever prints a secret.
"""

from __future__ import annotations

import keyring
import pytest

from boardmodeler.security import credentials
from boardmodeler.security.credentials import (
    REDACTED,
    SecretSource,
    delete_credential,
    describe_credential,
    get_credential,
    redact,
    set_credential,
)

ENV_VAR = "BOARDMODELER_FIXTURE_API_KEY"


class FakeKeyring:
    """In-memory keyring backend; ``error`` makes every call raise."""

    def __init__(self, values: dict[str, str] | None = None, *, error: Exception | None = None):
        self.values = dict(values or {})
        self.error = error

    def get_password(self, service: str, key: str) -> str | None:
        if self.error is not None:
            raise self.error
        return self.values.get(key)

    def set_password(self, service: str, key: str, value: str) -> None:
        if self.error is not None:
            raise self.error
        self.values[key] = value

    def delete_password(self, service: str, key: str) -> None:
        if self.error is not None:
            raise self.error
        if key not in self.values:
            raise keyring.errors.PasswordDeleteError(key)
        del self.values[key]


def test_broken_keyring_and_no_env_reports_missing_without_raising(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    credential = get_credential(
        "fixture", keyring_backend=FakeKeyring(error=RuntimeError("no usable backend"))
    )
    assert credential.source is SecretSource.MISSING
    assert credential.value is None
    assert credential.detail.strip()
    assert "no usable backend" in credential.detail


def test_env_fallback_supplies_the_value(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "from-env")
    credential = get_credential("fixture", keyring_backend=FakeKeyring())
    assert credential.source is SecretSource.ENV
    assert credential.value == "from-env"
    assert ENV_VAR in credential.detail
    # An env fallback must still work when the keyring itself is unusable.
    credential = get_credential(
        "fixture", keyring_backend=FakeKeyring(error=RuntimeError("locked"))
    )
    assert credential.source is SecretSource.ENV
    assert credential.value == "from-env"


def test_keyring_value_wins_over_env(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "from-env")
    backend = FakeKeyring({"provider:fixture:api_key": "from-keyring"})
    credential = get_credential("fixture", keyring_backend=backend)
    assert credential.source is SecretSource.KEYRING
    assert credential.value == "from-keyring"


def test_env_var_name_normalizes_non_alphanumeric_characters(monkeypatch):
    monkeypatch.setenv("BOARDMODELER_BOB_DIRECT_API_KEY", "from-env")
    credential = get_credential("bob-direct", keyring_backend=FakeKeyring())
    assert credential.source is SecretSource.ENV
    assert credential.value == "from-env"


def test_describe_credential_never_contains_the_secret(monkeypatch):
    secret = "super-secret-value"
    monkeypatch.setenv(ENV_VAR, secret)
    monkeypatch.setattr(credentials, "keyring", FakeKeyring())
    assert "source=env" in describe_credential("fixture")
    assert secret not in describe_credential("fixture")
    monkeypatch.delenv(ENV_VAR)
    missing = describe_credential("fixture")
    assert "source=missing" in missing
    assert secret not in missing


def test_redact_removes_every_secret_longest_first():
    text = (
        "POST failed: Authorization: Bearer sk-live-abc123; "
        "backup key sk-live-def456 (also sk-live-abc)"
    )
    scrubbed = redact(text, ["sk-live-abc", "sk-live-abc123", "sk-live-def456", "", "   "])
    assert scrubbed == (
        f"POST failed: Authorization: Bearer {REDACTED}; backup key {REDACTED} (also {REDACTED})"
    )
    assert "sk-live" not in scrubbed


def test_set_get_delete_roundtrip_against_a_keyring(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setattr(credentials, "keyring", fake)
    monkeypatch.delenv(ENV_VAR, raising=False)

    set_credential("fixture", "value-1")
    stored = get_credential("fixture")
    assert stored.source is SecretSource.KEYRING
    assert stored.value == "value-1"

    delete_credential("fixture")
    assert get_credential("fixture").source is SecretSource.MISSING


def test_set_credential_refuses_empty_values(monkeypatch):
    monkeypatch.setattr(credentials, "keyring", FakeKeyring())
    with pytest.raises(ValueError):
        set_credential("fixture", "")
