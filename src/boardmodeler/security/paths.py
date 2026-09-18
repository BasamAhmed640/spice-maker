"""Path guards (D11).

Every place BoardModeler turns a user- or archive-supplied name into a
filesystem path goes through this module: document originals, model files,
extracted vendor archives, and ``.include``/``.lib`` resolution.

The guard is deliberately strict. :func:`resolve_within` refuses any ``..``
component outright instead of trying to prove that a particular traversal stays
inside the root, so a caller can never be surprised by a path that leaves it,
and :func:`safe_extract` validates every archive member before writing a single
byte (absolute paths, ``..``, symlinks/hardlinks, device nodes, non-portable
names, and entry-count/size overflows are all rejected with the observed
member named in the error).
"""

from __future__ import annotations

import contextlib
import re
import stat
import tarfile
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Any

__all__ = [
    "ExtractionLimits",
    "PathGuardError",
    "resolve_within",
    "safe_extract",
    "safe_filename",
]


class PathGuardError(ValueError):
    """A path or archive member escaped the allowed root, or was unusable."""


# --------------------------------------------------------------------------- #
# single paths


def resolve_within(root: str | Path, candidate: str | Path) -> Path:
    """Resolve ``candidate`` under ``root``, rejecting any escape.

    ``root`` may be relative and is resolved first. ``candidate`` may be
    relative (joined to ``root``) or absolute (allowed only when it resolves
    inside ``root``). Any ``..`` component is rejected even when the result
    would stay inside the root, and symlinks are resolved before the containment
    check, so a link pointing outside the root is rejected too.
    """
    root_path = Path(root).expanduser().resolve()
    candidate_path = Path(candidate)
    if ".." in candidate_path.parts:
        raise PathGuardError(f"{str(candidate)!r} contains a '..' component")
    joined = candidate_path if candidate_path.is_absolute() else root_path / candidate_path
    resolved = joined.expanduser().resolve()
    if not resolved.is_relative_to(root_path):
        raise PathGuardError(
            f"{str(candidate)!r} resolves to {resolved}, outside the allowed root {root_path}"
        )
    return resolved


# --------------------------------------------------------------------------- #
# file names


_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_UNSAFE_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"|?*\\/]')
_MAX_EXTENSION_CHARS = 20


def safe_filename(name: str, *, fallback: str = "file", max_len: int = 80) -> str:
    """Return ``name`` as a single filename that is safe to write on Windows.

    Path separators are cut down to the last component, control characters and
    the characters Windows forbids (``<>:"|?*``) are removed, trailing dots and
    spaces are stripped, reserved device names (``CON``, ``COM1``, ``LPT9``, ...)
    are neutralized with a leading underscore, and the result is clipped to
    ``max_len`` characters while keeping the extension when that is possible.
    An empty or unusable result becomes ``fallback``.
    """
    if not isinstance(name, str):
        raise TypeError(f"filename must be str, got {type(name).__name__}")
    if max_len < 1:
        raise ValueError(f"max_len must be >= 1, got {max_len}")

    base = re.split(r"[\\/]", name)[-1]
    clean = _UNSAFE_CHARS.sub("", base).strip().rstrip(".")
    if not clean:
        clean = fallback
    clean = _neutralize_reserved(_clip(clean, max_len).strip().rstrip(".") or fallback)
    return clean


def _neutralize_reserved(name: str) -> str:
    stem = name.split(".", 1)[0]
    if stem.upper() in _WINDOWS_RESERVED:
        return "_" + name
    return name


def _clip(name: str, max_len: int) -> str:
    if len(name) <= max_len:
        return name
    dot = name.rfind(".")
    if 0 < dot < len(name) - 1 and len(name) - dot <= _MAX_EXTENSION_CHARS:
        keep = max_len - (len(name) - dot)
        if keep >= 1:
            return name[:keep] + name[dot:]
    return name[:max_len]


# --------------------------------------------------------------------------- #
# archive extraction


@dataclass(frozen=True)
class ExtractionLimits:
    """Caps applied to one archive before and while it is extracted."""

    max_entries: int = 2000
    max_total_bytes: int = 512 * 1024 * 1024
    max_entry_bytes: int = 128 * 1024 * 1024


_ILLEGAL_WINDOWS_CHARS = frozenset('<>:"|?*')
_COPY_CHUNK = 1 << 20


def safe_extract(
    archive: str | Path, dest: str | Path, *, limits: ExtractionLimits | None = None
) -> list[Path]:
    """Extract a zip or tar archive into ``dest`` after validating every member.

    Returns the regular files written, in archive order. Symlinks, hardlinks,
    device nodes, absolute member paths, ``..`` traversal, member names that are
    not portable to Windows, and archives exceeding ``limits`` raise
    :class:`PathGuardError`; validation happens before any write, and on a
    streaming failure the files written so far are removed.
    """
    limit = limits or ExtractionLimits()
    source = Path(archive)
    destination = Path(dest).expanduser().resolve()
    if not source.is_file():
        raise PathGuardError(f"archive {source} does not exist")
    destination.mkdir(parents=True, exist_ok=True)

    if zipfile.is_zipfile(source):
        return _extract_zip(source, destination, limit)
    if tarfile.is_tarfile(source):
        return _extract_tar(source, destination, limit)
    raise PathGuardError(f"{source} is neither a zip nor a tar archive")


def _member_parts(source: Path, name: str) -> PurePosixPath:
    """Validate one archive member name and return its relative POSIX parts."""
    if not name or name in {".", ".."}:
        raise PathGuardError(f"{source}: member name {name!r} is not a usable path")
    if "\x00" in name:
        raise PathGuardError(f"{source}: member name {name!r} contains a NUL byte")
    if "\\" in name:
        raise PathGuardError(f"{source}: member name {name!r} contains a backslash separator")
    parts = PurePosixPath(name)
    if parts.is_absolute():
        raise PathGuardError(f"{source}: member name {name!r} is an absolute path")
    if any(part == ".." for part in parts.parts):
        raise PathGuardError(f"{source}: member name {name!r} escapes the extraction directory")
    for part in parts.parts:
        bad = sorted(ch for ch in part if ch in _ILLEGAL_WINDOWS_CHARS)
        if bad:
            raise PathGuardError(
                f"{source}: member name {name!r} is not portable to Windows: {bad} in {part!r}"
            )
    return parts


def _member_target(source: Path, dest: Path, name: str) -> Path:
    return resolve_within(dest, _member_parts(source, name).as_posix())


def _check_entry_size(
    source: Path, name: str, size: int, declared_total: int, limit: ExtractionLimits
) -> None:
    if size > limit.max_entry_bytes:
        raise PathGuardError(
            f"{source}: member {name!r} declares {size} bytes, "
            f"over max_entry_bytes={limit.max_entry_bytes}"
        )
    if declared_total > limit.max_total_bytes:
        raise PathGuardError(
            f"{source}: members declare at least {declared_total} bytes, "
            f"over max_total_bytes={limit.max_total_bytes}"
        )


def _copy_capped(
    source: Path, name: str, reader: IO[bytes], writer: IO[bytes], limit_bytes: int
) -> int:
    written = 0
    while True:
        block = reader.read(_COPY_CHUNK)
        if not block:
            return written
        written += len(block)
        if written > limit_bytes:
            raise PathGuardError(
                f"{source}: member {name!r} exceeds the declared size "
                f"(>{limit_bytes} bytes); the archive header lied"
            )
        writer.write(block)


def _extract_zip(source: Path, dest: Path, limit: ExtractionLimits) -> list[Path]:
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if len(infos) > limit.max_entries:
            raise PathGuardError(
                f"{source}: {len(infos)} entries exceeds max_entries={limit.max_entries}"
            )
        declared_total = 0
        directories: list[Path] = []
        files: list[tuple[Path, zipfile.ZipInfo]] = []
        for info in infos:
            target = _member_target(source, dest, info.filename)
            if info.is_dir():
                directories.append(target)
                continue
            if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
                raise PathGuardError(f"{source}: member {info.filename!r} is a symlink")
            declared_total += info.file_size
            _check_entry_size(source, info.filename, info.file_size, declared_total, limit)
            files.append((target, info))
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        return _write_archive(source, files, lambda info: archive.open(info), limit=limit)


def _extract_tar(source: Path, dest: Path, limit: ExtractionLimits) -> list[Path]:
    with tarfile.open(source, "r:*") as archive:
        members = archive.getmembers()
        if len(members) > limit.max_entries:
            raise PathGuardError(
                f"{source}: {len(members)} entries exceeds max_entries={limit.max_entries}"
            )
        declared_total = 0
        directories: list[Path] = []
        files: list[tuple[Path, tarfile.TarInfo]] = []
        for member in members:
            target = _member_target(source, dest, member.name)
            if member.isdir():
                directories.append(target)
                continue
            if member.issym() or member.islnk():
                kind = "symlink" if member.issym() else "hardlink"
                raise PathGuardError(f"{source}: member {member.name!r} is a {kind}")
            if member.isdev() or member.isfifo():
                raise PathGuardError(f"{source}: member {member.name!r} is not a regular file")
            if not member.isfile():
                raise PathGuardError(
                    f"{source}: member {member.name!r} has unsupported type {member.type!r}"
                )
            declared_total += member.size
            _check_entry_size(source, member.name, member.size, declared_total, limit)
            files.append((target, member))
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        return _write_archive(
            source, files, lambda member: archive.extractfile(member), limit=limit
        )


def _write_archive(
    source: Path,
    files: Sequence[tuple[Path, Any]],
    opener: Callable[[Any], IO[bytes] | None],
    *,
    limit: ExtractionLimits,
) -> list[Path]:
    written: list[Path] = []
    total_written = 0
    try:
        for target, member in files:
            name = getattr(member, "filename", None) or getattr(member, "name", "?")
            target.parent.mkdir(parents=True, exist_ok=True)
            reader = opener(member)
            if reader is None:
                raise PathGuardError(f"{source}: member {name!r} has no readable content")
            with reader, open(target, "wb") as writer:
                written.append(target)
                total_written += _copy_capped(source, name, reader, writer, limit.max_entry_bytes)
                if total_written > limit.max_total_bytes:
                    raise PathGuardError(
                        f"{source}: extracted bytes exceed max_total_bytes={limit.max_total_bytes}"
                    )
    except Exception:
        for path in written:
            with contextlib.suppress(OSError):
                path.unlink()
        raise
    return written
