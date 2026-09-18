"""OCR state tests.

The machine this runs on has no tesseract, so the interesting assertions are
that the absence is reported as a machine-readable, non-empty reason, that no
engine claims availability it does not have, and that an engine which cannot
read a page raises instead of returning empty text.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from boardmodeler.documents.ocr import (
    TESSERACT_FAILED,
    TESSERACT_NOT_FOUND,
    OcrFailed,
    TesseractOcr,
    detect_ocr_engine,
    probe_ocr,
    select_ocr_engine,
)


def _no_binary(name: str) -> str | None:
    """A ``which`` that finds nothing, standing in for a machine without tesseract."""
    return None


def _fake_which(name: str) -> str | None:
    """A ``which`` that reports a tesseract path without needing one on disk."""
    return r"C:\tools\tesseract.exe" if "tesseract" in name else None


def test_probe_ocr_reports_a_machine_readable_reason_for_the_missing_engine() -> None:
    reason = probe_ocr()

    assert reason is not None, "tesseract is absent on this machine"
    assert re.fullmatch(r"[a-z][a-z0-9_]*", reason.reason), reason.reason
    assert reason.detail.strip(), "a reason without a detail cannot be acted on"
    assert reason.engine is None or isinstance(reason.engine, str)
    assert reason.reason == TESSERACT_NOT_FOUND
    # It really is unavailable: neither API hands back an engine here.
    assert detect_ocr_engine() is None
    assert probe_ocr(which=_no_binary) is not None


def test_probe_ocr_stays_available_when_the_engine_is_found() -> None:
    reason = probe_ocr(which=_fake_which)

    assert reason is None


def test_detect_ocr_engine_returns_an_available_engine_when_the_binary_exists() -> None:
    looked_up: list[str] = []

    def which(name: str) -> str | None:
        looked_up.append(name)
        return r"C:\tools\tesseract.exe" if "tesseract" in name else None

    engine = detect_ocr_engine(which=which)

    assert engine is not None
    assert engine.available() is True
    assert engine.describe() is None
    assert any("tesseract" in name for name in looked_up)


def test_unavailable_engine_refuses_to_produce_text() -> None:
    engine = select_ocr_engine(which=_no_binary)

    assert engine.available() is False
    reason = engine.describe()
    assert reason is not None
    assert reason.reason == TESSERACT_NOT_FOUND
    assert reason.detail.strip()
    with pytest.raises(OcrFailed) as excinfo:
        engine.page_text(b"\x89PNG\r\n\x1a\n")
    assert excinfo.value.reason == TESSERACT_NOT_FOUND


def test_failing_engine_raises_instead_of_returning_empty_text(tmp_path: Path) -> None:
    # sys.executable cannot execute a PNG, so tesseract's contract ("text or a
    # non-zero exit") is exercised with a real process that must fail.
    engine = TesseractOcr(sys.executable)
    assert engine.available() is True
    image = tmp_path / "page.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n not really an image")

    with pytest.raises(OcrFailed) as excinfo:
        engine.page_text(image.read_bytes())

    assert str(excinfo.value).strip()
    assert excinfo.value.reason == TESSERACT_FAILED


def test_empty_image_is_refused_before_any_process_starts() -> None:
    engine = TesseractOcr("tesseract-does-not-exist")

    with pytest.raises(OcrFailed):
        engine.page_text(b"")


def test_engine_without_a_path_reports_itself_unavailable() -> None:
    engine = TesseractOcr("")

    assert engine.available() is False
    reason = engine.describe()
    assert reason is not None
    assert reason.engine == "tesseract"
