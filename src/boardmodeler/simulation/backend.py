"""``.raw`` reader backend selection (D7).

The native reader is authoritative. ``spicelib`` is used only when it is
importable **and** reproduces the native read of the same smoke ``.raw`` within
1e-9 relative. Selection happens once per process and is recorded in
``RunManifest.reader_backend`` and ``docs/DECISIONS.md``.

A backend that fails later is reported (BLOCKED at the caller) rather than
silently swapped mid-run: switching readers between runs would make two
results incomparable without saying so.
"""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from boardmodeler.domain.enums import ReaderBackend
from boardmodeler.simulation.raw import RawFile, RawFormatError, read_raw, read_raw_spicelib

__all__ = [
    "AGREEMENT_TOLERANCE",
    "BackendProbe",
    "current_backend",
    "probe_backend",
    "read_waveforms",
    "reset_backend_cache",
    "select_backend",
    "spicelib_version",
]

AGREEMENT_TOLERANCE = 1e-9

_PROBE: BackendProbe | None = None


@dataclass(frozen=True)
class BackendProbe:
    """Outcome of the backend comparison, with the evidence for the choice."""

    backend: ReaderBackend
    spicelib_version: str | None = None
    available: bool = False
    max_deviation: float | None = None
    compared_variables: list[str] = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "reader_backend": self.backend.value,
            "spicelib_version": self.spicelib_version,
            "spicelib_available": self.available,
            "agreement_tolerance": AGREEMENT_TOLERANCE,
            "max_deviation": self.max_deviation,
            "compared_variables": list(self.compared_variables),
            "detail": self.detail,
        }


def spicelib_version() -> str | None:
    try:
        return importlib.metadata.version("spicelib")
    except importlib.metadata.PackageNotFoundError:
        return None


def probe_backend(raw_path: Path | None) -> BackendProbe:
    """Compare the two readers on ``raw_path`` and report the evidence.

    ``raw_path=None`` means no reference file was available: the backend stays
    native with an explicit reason (never a silent assumption that spicelib
    agrees).
    """
    installed = spicelib_version()
    if installed is None:
        return BackendProbe(
            backend=ReaderBackend.NATIVE,
            spicelib_version=None,
            available=False,
            detail="spicelib is not installed (optional 'sim' extra); using the native reader",
        )
    if raw_path is None:
        return BackendProbe(
            backend=ReaderBackend.NATIVE,
            spicelib_version=installed,
            available=True,
            detail="spicelib is installed but no reference .raw was available for the "
            "agreement probe; keeping the native reader",
        )

    try:
        native = read_raw(raw_path)
    except (OSError, RawFormatError) as exc:
        return BackendProbe(
            backend=ReaderBackend.NATIVE,
            spicelib_version=installed,
            available=True,
            detail=f"native reader failed on the probe file ({exc}); using native",
        )
    try:
        other = read_raw_spicelib(raw_path)
    except Exception as exc:
        return BackendProbe(
            backend=ReaderBackend.NATIVE,
            spicelib_version=installed,
            available=True,
            detail=f"spicelib failed to read the probe file ({type(exc).__name__}: {exc})",
        )

    shared = [name for name in native.variables if other.has(name)]
    if len(shared) < native.nvars:
        missing = [name for name in native.variables if name not in shared]
        return BackendProbe(
            backend=ReaderBackend.NATIVE,
            spicelib_version=installed,
            available=True,
            compared_variables=shared,
            detail=(
                f"spicelib did not return {len(missing)} of {native.nvars} variables "
                f"({missing[:4]}); using native"
            ),
        )
    if native.npoints != other.npoints:
        return BackendProbe(
            backend=ReaderBackend.NATIVE,
            spicelib_version=installed,
            available=True,
            compared_variables=shared,
            detail=(
                f"spicelib returned {other.npoints} points, native {native.npoints}; using native"
            ),
        )

    max_dev = 0.0
    for name in shared:
        a = native.column(name)
        b = other.column(name)
        scale = max(float(np.max(np.abs(a))), 1e-12)
        max_dev = max(max_dev, float(np.max(np.abs(a - b))) / scale)

    if max_dev > AGREEMENT_TOLERANCE:
        return BackendProbe(
            backend=ReaderBackend.NATIVE,
            spicelib_version=installed,
            available=True,
            max_deviation=max_dev,
            compared_variables=shared,
            detail=(
                f"spicelib disagrees with the native reader by {max_dev:.3e} relative "
                f"(> {AGREEMENT_TOLERANCE:.0e}); using native"
            ),
        )
    return BackendProbe(
        backend=ReaderBackend.SPICELIB,
        spicelib_version=installed,
        available=True,
        max_deviation=max_dev,
        compared_variables=shared,
        detail=(
            f"spicelib {installed} agrees with the native reader on {len(shared)} variables "
            f"of {raw_path.name} (max deviation {max_dev:.3e} relative)"
        ),
    )


def select_backend(raw_path: Path | None = None, *, refresh: bool = False) -> BackendProbe:
    """Probe once per process and cache the result."""
    global _PROBE
    if _PROBE is None or refresh:
        _PROBE = probe_backend(raw_path)
    return _PROBE


def reset_backend_cache() -> None:
    """Drop the cached probe (used by tests and by ``--self-test``)."""
    global _PROBE
    _PROBE = None


def current_backend() -> ReaderBackend:
    return _PROBE.backend if _PROBE else ReaderBackend.NATIVE


def read_waveforms(path: str | Path) -> RawFile:
    """Read a ``.raw`` with the selected backend.

    Raises :class:`boardmodeler.simulation.raw.RawFormatError` on failure — the
    caller reports BLOCKED. The reader is never swapped silently.
    """
    if current_backend() is ReaderBackend.SPICELIB:
        try:
            return read_raw_spicelib(path)
        except RawFormatError:
            raise
        except Exception as exc:
            raise RawFormatError(f"spicelib backend failed on {path}: {exc}") from exc
    return read_raw(path)
