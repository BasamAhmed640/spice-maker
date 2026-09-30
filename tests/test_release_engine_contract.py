"""A current source tree must not bless stale same-version desktop payloads."""

from __future__ import annotations

import pytest
from tools.verify_release_zip import StageFailure, verify_engine_choices, verify_engine_contract

EXPECTED = {
    "schema_version": 1,
    "default_engine": "behavioral",
    "engines": ["behavioral", "pin_only", "legacy_ai"],
    "source_sha256": "a" * 64,
}


@pytest.mark.parametrize("surface", ["installed wheel", "desktop executable"])
def test_release_checks_both_engine_identity_and_public_cli(surface):
    payload = {"version": "1.7.0", "engine_contract": dict(EXPECTED)}
    assert verify_engine_contract(payload, EXPECTED, surface) == EXPECTED
    verify_engine_choices("--engine {behavioral,pin_only,legacy_ai}", surface)


@pytest.mark.parametrize(
    "change",
    [
        None,
        {"default_engine": "legacy_ai"},
        {"source_sha256": "b" * 64},
        {"engines": ["legacy_ai"]},
        {"schema_version": 0},
    ],
)
def test_same_version_stale_engine_cannot_pass_release_check(change):
    payload = {"version": "1.7.0"}
    if change is not None:
        payload["engine_contract"] = {**EXPECTED, **change}
    with pytest.raises(StageFailure, match="engine"):
        verify_engine_contract(payload, EXPECTED, "desktop executable")


@pytest.mark.parametrize(
    "help_text",
    ["--part --datasheet --out", "--engine {legacy_ai}", "behavioral pin_only legacy_ai"],
)
def test_old_cli_cannot_pass_engine_release_check(help_text):
    with pytest.raises(StageFailure, match="CLI is stale"):
        verify_engine_choices(help_text, "installed wheel")
