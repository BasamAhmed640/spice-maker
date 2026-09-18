"""OCR engine interface and the explicit "OCR is not available" state.

``tesseract`` is not installed on the development machine, and it may not be
installed on a user's machine either. The document layer therefore has to be
able to say so: :func:`probe_ocr` returns the machine-readable reason, and
:func:`select_ocr_engine` hands back an engine object that reports the same
reason instead of returning text. OCR output is never substituted with embedded
text, and embedded text is never presented as OCR output.

The interface is deliberately small so another engine can be plugged in without
touching the rest of the document layer.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

__all__ = [
    "TESSERACT_FAILED",
    "TESSERACT_NOT_FOUND",
    "OcrEngine",
    "OcrFailed",
    "OcrUnavailable",
    "TesseractOcr",
    "UnavailableOcrEngine",
    "detect_ocr_engine",
    "probe_ocr",
    "select_ocr_engine",
]

TESSERACT_NOT_FOUND = "tesseract_not_found"
TESSERACT_FAILED = "tesseract_failed"

_LOCAL_TESSERACT_NAMES = ("tesseract", "tesseract.exe")
_OCR_TIMEOUT_S = 120.0
_ERROR_TAIL_CHARS = 400


@dataclass(frozen=True)
class OcrUnavailable:
    """Why OCR cannot be used, in a form a caller can branch on and print."""

    reason: str  # machine code, e.g. "tesseract_not_found"
    detail: str  # human-readable, always non-empty
    engine: str | None  # the engine the reason refers to, when one was named


class OcrFailed(RuntimeError):
    """An OCR engine was expected to produce text and did not."""

    def __init__(self, message: str, *, reason: str = TESSERACT_FAILED) -> None:
        super().__init__(message)
        self.reason = reason


class OcrEngine(Protocol):
    """A page-level OCR backend."""

    def available(self) -> bool:
        """Whether this engine can be used right now."""
        ...

    def describe(self) -> OcrUnavailable | None:
        """``None`` when OCR is available, otherwise the reason it is not."""
        ...

    def page_text(self, image_png: bytes) -> str:
        """Recognize text in a PNG page image, or raise :class:`OcrFailed`."""
        ...


class TesseractOcr:
    """The tesseract command-line engine."""

    def __init__(self, path: str) -> None:
        self.path = str(path)
        self.name = "tesseract"

    def available(self) -> bool:
        return bool(self.path)

    def describe(self) -> OcrUnavailable | None:
        if self.available():
            return None
        return OcrUnavailable(
            reason=TESSERACT_NOT_FOUND,
            detail="tesseract was located by the caller but no executable path was given",
            engine=self.name,
        )

    def page_text(self, image_png: bytes) -> str:
        """Run tesseract on ``image_png`` and return its text.

        A non-zero exit, a missing binary, or a timeout raises
        :class:`OcrFailed`; an engine that could not read the page never
        returns an empty string that could be mistaken for "no text here".
        """
        if not image_png:
            raise OcrFailed("refusing to OCR an empty image")
        with tempfile.TemporaryDirectory(prefix="boardmodeler-ocr-") as scratch:
            image = Path(scratch) / "page.png"
            image.write_bytes(image_png)
            try:
                # List argv only, no shell: the engine is a fixed binary and the
                # only variable part is a path inside our own scratch directory.
                completed = subprocess.run(
                    [self.path, str(image), "stdout"],
                    capture_output=True,
                    timeout=_OCR_TIMEOUT_S,
                    check=False,
                    shell=False,
                )
            except OSError as exc:
                raise OcrFailed(f"could not run {self.path!r}: {exc}") from exc
            except subprocess.TimeoutExpired as exc:
                raise OcrFailed(f"tesseract did not finish within {_OCR_TIMEOUT_S:.0f} s") from exc
        if completed.returncode != 0:
            raise OcrFailed(
                f"tesseract exited with code {completed.returncode}: {_tail(completed.stderr)}"
            )
        return completed.stdout.decode("utf-8", errors="replace").strip()


class UnavailableOcrEngine:
    """Stand-in engine that reports why OCR cannot run.

    It exists so a caller can hold an :class:`OcrEngine` unconditionally: asking
    it for text raises instead of quietly returning embedded text or an empty
    string.
    """

    def __init__(self, reason: OcrUnavailable) -> None:
        self.reason = reason

    def available(self) -> bool:
        return False

    def describe(self) -> OcrUnavailable | None:
        return self.reason

    def page_text(self, image_png: bytes) -> str:
        raise OcrFailed(
            f"OCR is unavailable ({self.reason.reason}): {self.reason.detail}",
            reason=self.reason.reason,
        )


def detect_ocr_engine(*, which: Callable[[str], str | None] = shutil.which) -> OcrEngine | None:
    """Return a tesseract engine when the binary exists, else ``None``."""
    for name in _LOCAL_TESSERACT_NAMES:
        path = which(name)
        if path:
            return TesseractOcr(path)
    return None


def probe_ocr(*, which: Callable[[str], str | None] = shutil.which) -> OcrUnavailable | None:
    """``None`` when OCR is available, otherwise the reason it is not."""
    engine = detect_ocr_engine(which=which)
    if engine is None:
        return _not_found()
    return engine.describe()


def select_ocr_engine(*, which: Callable[[str], str | None] = shutil.which) -> OcrEngine:
    """Return the real engine when there is one, else an explicit unavailable one.

    This is not a silent fallback: the returned engine tells the truth about
    itself through :meth:`OcrEngine.available` and :meth:`OcrEngine.describe`,
    and refuses to produce text.
    """
    engine = detect_ocr_engine(which=which)
    return engine if engine is not None else UnavailableOcrEngine(_not_found())


def _not_found() -> OcrUnavailable:
    return OcrUnavailable(
        reason=TESSERACT_NOT_FOUND,
        detail=(
            "tesseract was not found on PATH; pages whose text is not embedded cannot be "
            "read here and must be reported as an evidence gap rather than guessed"
        ),
        engine=None,
    )


def _tail(data: bytes | None) -> str:
    text = (data or b"").decode("utf-8", errors="replace").strip()
    if len(text) <= _ERROR_TAIL_CHARS:
        return text or "(no output)"
    return "..." + text[-_ERROR_TAIL_CHARS:]
