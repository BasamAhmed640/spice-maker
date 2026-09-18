"""The explicit blocked path for real PCIe-switch qualification (Phase 6 step 2).

A real device cannot be qualified without real documentation.  The gate in
``models/regression.py`` answers a request with a status and a reason and never
produces an identity, a requirement list or a model — and a *synthetic* fixture
is refused as device data, because its requirements carry
``origin=TEST_FIXTURE`` and state nothing about any real part.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from boardmodeler.domain.enums import Status
from boardmodeler.domain.hashing import sha256_file
from boardmodeler.domain.records import DocumentRecord, PartIdentity
from boardmodeler.models.regression import (
    QUALIFICATION_BLOCKED_REASON,
    QUALIFICATION_UNKNOWN_REASON,
    QualificationOutcome,
    QualificationRequest,
    request_device_qualification,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "fixtures" / "switch_fixture"
CONTRACT_PATH = FIXTURE_DIR / "SYNTHETIC_SWITCH_CONTRACT.md"
REQUIREMENTS_PATH = FIXTURE_DIR / "requirements.json"

REAL_PART = "PCIE-SWITCH-48L"
"""A real-device request with no documentation: it must stay blocked."""


def _request(**overrides: object) -> QualificationRequest:
    defaults: dict[str, object] = {
        "part": REAL_PART,
        "identity": PartIdentity(
            manufacturer="Example Semiconductors",
            base_part=REAL_PART,
            confidence="unresolved",
        ),
        "documents": (),
    }
    defaults.update(overrides)
    return QualificationRequest(**defaults)  # type: ignore[arg-type]


def _synthetic_document() -> DocumentRecord:
    return DocumentRecord(
        doc_id="doc_synthetic_switch_contract",
        title="SYNTHETIC_SWITCH_CONTRACT.md — a test fixture, not device data",
        doc_type="synthetic_contract",
        provenance="synthetic_fixture",
        classification="public",
        file_hash=sha256_file(CONTRACT_PATH),
        path=str(CONTRACT_PATH),
    )


def _real_datasheet() -> DocumentRecord:
    return DocumentRecord(
        doc_id="doc_example_switch_datasheet",
        title="Example PCIe switch datasheet (supplied by the caller)",
        doc_type="datasheet",
        provenance="user_supplied",
        classification="unknown",
        file_hash="0" * 64,
    )


def _assert_nothing_fabricated(outcome: QualificationOutcome) -> None:
    assert outcome.identity is None, "the gate must not fabricate a PartIdentity"
    assert outcome.requirements == (), "the gate must not fabricate requirements"
    assert outcome.model_text is None, "the gate must not fabricate a model"


def test_real_switch_without_documentation_is_blocked() -> None:
    outcome = request_device_qualification(_request())
    assert outcome.status is Status.BLOCKED
    assert outcome.reason == QUALIFICATION_BLOCKED_REASON == "device_documentation_unavailable"
    assert outcome.usable_documents == ()
    assert "no device documentation" in outcome.detail
    _assert_nothing_fabricated(outcome)


def test_synthetic_fixture_cannot_qualify_a_real_device() -> None:
    data = json.loads(REQUIREMENTS_PATH.read_text(encoding="utf-8"))
    requirements = data["requirements"]
    assert requirements, "the synthetic fixture must carry requirements"
    assert {requirement["origin"] for requirement in requirements} == {"TEST_FIXTURE"}
    assert {requirement["evidence"][0]["extraction"] for requirement in requirements} == {
        "synthetic_fixture"
    }
    assert data["document"]["provenance"] == "synthetic_fixture"

    outcome = request_device_qualification(_request(documents=(_synthetic_document(),)))
    assert outcome.status is Status.BLOCKED
    assert outcome.reason == QUALIFICATION_BLOCKED_REASON
    assert outcome.usable_documents == ()
    assert "cannot qualify a real device" in outcome.detail
    assert "TEST_FIXTURE" in outcome.detail
    assert "doc_synthetic_switch_contract" in outcome.detail
    _assert_nothing_fabricated(outcome)


def test_supplied_documentation_never_yields_a_pass_here() -> None:
    outcome = request_device_qualification(_request(documents=(_real_datasheet(),)))
    assert outcome.status is Status.UNKNOWN, "no extraction happened, so no PASS is possible"
    assert outcome.reason == QUALIFICATION_UNKNOWN_REASON
    assert outcome.usable_documents == ("doc_example_switch_datasheet",)
    _assert_nothing_fabricated(outcome)


def test_real_documentation_beside_a_synthetic_one_is_still_unknown() -> None:
    outcome = request_device_qualification(
        _request(documents=(_synthetic_document(), _real_datasheet()))
    )
    assert outcome.status is Status.UNKNOWN
    assert outcome.usable_documents == ("doc_example_switch_datasheet",)
    _assert_nothing_fabricated(outcome)


def test_blank_part_is_rejected() -> None:
    with pytest.raises(ValueError, match="part"):
        request_device_qualification(_request(part="   "))
