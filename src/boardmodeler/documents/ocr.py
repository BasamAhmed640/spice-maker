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
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from boardmodeler.security import execution

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

#: The engine's basename, named explicitly on the spec because the executable comes
#: from settings and is not one of the tools the guard's allowlist knows. A named
#: permission, not a directory: the user may have installed tesseract anywhere, so the
#: policy's ``allowed_dirs`` would be the wrong check (and refusing every location but
#: our own would make the feature unusable).
_OCR_EXECUTABLE = "tesseract"

#: What the OCR child may see: the OS basics it cannot start without, plus the engine's
#: own documented variable for its language-data directory -- an installation whose
#: tessdata is configured through the environment is a real configuration, and dropping
#: it would turn a working engine into one that reports "no language data". Nothing
#: else is inherited: the previous call site handed the child this process's whole
#: environment, including the agent API key, for no benefit.
_OCR_ENV_ALLOWLIST: tuple[str, ...] = (*execution.WINDOWS_BASE_ENV, "TESSDATA_PREFIX")

#: A broken or wedged OCR binary can print without end, and every byte would be kept in
#: this process's memory. 8 MiB is far above a page of recognized text (a dense A4 page
#: is tens of kilobytes) and far below anything that would hurt; a capture that reaches
#: it is reported as a failure rather than returned as a partial page.
_OCR_MAX_OUTPUT_BYTES = 8 << 20


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

    def _resolved_executable(self) -> Path:
        """The engine as an absolute, existing path, or :class:`OcrFailed`.

        The execution policy refuses a relative executable, because PATH and the current
        directory would then decide what runs. A settings value that is a bare name is
        therefore looked up once, here, in this process: the *absolute* result is what
        the policy pins. A name that cannot be found fails exactly as the old
        ``subprocess.run`` did -- an ``OSError``/``FileNotFoundError`` became
        :class:`OcrFailed`, and it still does.
        """
        candidate = Path(self.path).expanduser()
        if candidate.is_absolute():
            return candidate
        found = shutil.which(self.path)
        if not found:
            raise OcrFailed(f"could not run {self.path!r}: executable not found")
        return Path(found)

    def page_text(self, image_png: bytes) -> str:
        """Run tesseract on ``image_png`` and return its text.

        A non-zero exit, a missing binary, a timeout, or an output flood raises
        :class:`OcrFailed`; an engine that could not read the page never
        returns an empty string that could be mistaken for "no text here".

        The call goes through :func:`boardmodeler.security.execution.run`: the engine
        is the spec's executable (permitted by name through
        ``extra_allowed_executables``, because the path comes from settings and may live
        anywhere), the page image is a ``PATH_VALUE`` pinned inside the private scratch
        directory the child also uses as its working directory, the 120 s timeout is
        mandatory, and both streams are bounded. The child inherits
        ``_OCR_ENV_ALLOWLIST`` and nothing else.
        """
        if not image_png:
            raise OcrFailed("refusing to OCR an empty image")
        executable = self._resolved_executable()
        with tempfile.TemporaryDirectory(prefix="boardmodeler-ocr-") as scratch:
            image = Path(scratch) / "page.png"
            image.write_bytes(image_png)
            # The scratch directory is both the scope a value may resolve in and the
            # child's working directory, so the only path tesseract is given is the one
            # this method just wrote -- the run root is deliberately not the copy, and
            # the engine's own location is permitted by name instead.
            spec = execution.CommandSpec(
                name="tesseract-ocr",
                executable=executable,
                argv_tail=(execution.PATH_VALUE, "stdout"),
                timeout_s=_OCR_TIMEOUT_S,
                max_output_bytes=_OCR_MAX_OUTPUT_BYTES,
                env_allowlist=_OCR_ENV_ALLOWLIST,
                extra_allowed_executables=(_OCR_EXECUTABLE,),
            )
            try:
                completed = execution.run(
                    execution.CommandCall(spec=spec, argv=(str(image), "stdout")),
                    cwd=scratch,
                    root=scratch,
                )
            except execution.CommandRefused as exc:
                # Same user-visible outcome as the old OSError path, with the policy's
                # stable code and reason instead of a bare exception message.
                raise OcrFailed(f"could not run {self.path!r}: {exc.detail}") from exc
            except OSError as exc:
                raise OcrFailed(f"could not run {self.path!r}: {exc}") from exc
        if completed.timed_out:
            raise OcrFailed(f"tesseract did not finish within {_OCR_TIMEOUT_S:.0f} s")
        if completed.truncated:
            raise OcrFailed(
                f"tesseract produced more than {_OCR_MAX_OUTPUT_BYTES} bytes of output; "
                "the capture is incomplete, so no text is reported"
            )
        if completed.returncode != 0:
            raise OcrFailed(
                f"tesseract exited with code {completed.returncode}: {_tail(completed.stderr)}"
            )
        return completed.stdout.strip()


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


def _tail(data: bytes | str | None) -> str:
    text = (
        (data or b"").decode("utf-8", errors="replace") if isinstance(data, bytes) else (data or "")
    )
    text = text.strip()
    if len(text) <= _ERROR_TAIL_CHARS:
        return text or "(no output)"
    return "..." + text[-_ERROR_TAIL_CHARS:]
