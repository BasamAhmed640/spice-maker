"""Path guard behaviour (D11).

The tests cover the two halves the rest of the product depends on: resolving a
single path without ever leaving the root, and extracting a vendor archive only
after every member passed validation.
"""

from __future__ import annotations

import io
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from boardmodeler.security.paths import (
    ExtractionLimits,
    PathGuardError,
    resolve_within,
    safe_extract,
    safe_filename,
)


def test_resolve_within_keeps_relative_paths_inside(tmp_path: Path) -> None:
    resolved = resolve_within(tmp_path, "sub/dir/file.txt")
    assert resolved == (tmp_path / "sub" / "dir" / "file.txt").resolve()


def test_resolve_within_allows_an_absolute_path_inside_the_root(tmp_path: Path) -> None:
    target = tmp_path / "inside.txt"
    assert resolve_within(tmp_path, target) == target.resolve()


def test_resolve_within_rejects_dotdot_even_when_it_stays_inside(tmp_path: Path) -> None:
    with pytest.raises(PathGuardError, match=r"'\.\.'"):
        resolve_within(tmp_path, "sub/../inside.txt")


def test_resolve_within_rejects_escapes_and_names_the_observed_path(tmp_path: Path) -> None:
    with pytest.raises(PathGuardError):
        resolve_within(tmp_path, "../outside.txt")
    outside = tmp_path.parent / "outside.txt"
    with pytest.raises(PathGuardError) as info:
        resolve_within(tmp_path, outside)
    assert str(outside) in str(info.value)
    with pytest.raises(PathGuardError):
        resolve_within(tmp_path, tmp_path.parent)


def _make_directory_link(link: Path, target: Path) -> bool:
    """Create a directory link; returns False when the platform refuses.

    Symlinks need SeCreateSymbolicLinkPrivilege (developer mode) on Windows, but
    junctions do not, so the escape check is exercised on ordinary machines too.
    """
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except OSError, NotImplementedError:
        pass
    try:
        import _winapi
    except ImportError:
        return False
    try:
        _winapi.CreateJunction(str(target), str(link))
    except OSError:
        return False
    return True


def test_resolve_within_rejects_a_link_that_escapes(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    if not _make_directory_link(root / "link", outside):
        pytest.skip("neither symlinks nor junctions can be created here")
    with pytest.raises(PathGuardError):
        resolve_within(root, "link/secret.txt")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"C:\temp\evil.txt", "evil.txt"),
        ("../../etc/passwd", "passwd"),
        ("report\x00.txt", "report.txt"),
        ('bad<>:"|?*name.pdf', "badname.pdf"),
        ("name.  ", "name"),
        ("..", "file"),
        ("", "file"),
        ("CON.txt", "_CON.txt"),
        ("com1", "_com1"),
    ],
)
def test_safe_filename_produces_a_writable_name(raw: str, expected: str) -> None:
    assert safe_filename(raw) == expected


def test_safe_filename_clips_long_names_but_keeps_the_extension() -> None:
    clipped = safe_filename("a" * 200 + ".pdf")
    assert len(clipped) <= 80
    assert clipped.endswith(".pdf")
    assert len(safe_filename("b" * 200 + ".pdf", max_len=20)) <= 20


def test_safe_filename_uses_the_fallback_for_unusable_names() -> None:
    assert safe_filename("///", fallback="doc") == "doc"


def _make_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(zipfile.ZipInfo(name), data)
    return path


def test_safe_extract_writes_zip_members_in_order(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path / "good.zip", {"a.txt": b"alpha", "sub/b.txt": b"beta"})
    dest = tmp_path / "out"
    extracted = safe_extract(archive, dest)
    assert [path.relative_to(dest).as_posix() for path in extracted] == ["a.txt", "sub/b.txt"]
    assert (dest / "sub" / "b.txt").read_bytes() == b"beta"


def test_safe_extract_creates_explicitly_archived_directories(tmp_path: Path) -> None:
    with zipfile.ZipFile(tmp_path / "dirs.zip", "w") as archive:
        archive.writestr("empty/", b"")
    dest = tmp_path / "out"
    assert safe_extract(tmp_path / "dirs.zip", dest) == []
    assert (dest / "empty").is_dir()


def test_safe_extract_writes_tar_members(tmp_path: Path) -> None:
    archive_path = tmp_path / "good.tar"
    with tarfile.open(archive_path, "w") as archive:
        for name, data in {"one.txt": b"1", "nested/two.txt": b"2"}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    dest = tmp_path / "out"
    extracted = safe_extract(archive_path, dest)
    assert [path.relative_to(dest).as_posix() for path in extracted] == [
        "one.txt",
        "nested/two.txt",
    ]
    assert (dest / "nested" / "two.txt").read_bytes() == b"2"


@pytest.mark.parametrize(
    "member",
    ["../evil.txt", "..\\evil.txt", "/absolute.txt", "C:/absolute.txt", "a/../../evil.txt"],
)
def test_safe_extract_rejects_escaping_members_before_writing(tmp_path: Path, member: str) -> None:
    archive = _make_zip(tmp_path / "evil.zip", {member: b"boom"})
    dest = tmp_path / "out"
    with pytest.raises(PathGuardError):
        safe_extract(archive, dest)
    assert not (tmp_path / "evil.txt").exists()
    assert list(dest.iterdir()) == []


def test_safe_extract_rejects_zip_symlinks(tmp_path: Path) -> None:
    info = zipfile.ZipInfo("link")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    archive_path = tmp_path / "symlink.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(info, "../outside.txt")
    with pytest.raises(PathGuardError, match="symlink"):
        safe_extract(archive_path, tmp_path / "out")


@pytest.mark.parametrize("member_type", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_safe_extract_rejects_tar_links(tmp_path: Path, member_type: bytes) -> None:
    archive_path = tmp_path / "link.tar"
    with tarfile.open(archive_path, "w") as archive:
        info = tarfile.TarInfo("link")
        info.type = member_type
        info.linkname = "../outside.txt"
        archive.addfile(info)
    with pytest.raises(PathGuardError, match="link"):
        safe_extract(archive_path, tmp_path / "out")


def test_safe_extract_enforces_the_entry_cap(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path / "many.zip", {"a.txt": b"a", "b.txt": b"b"})
    with pytest.raises(PathGuardError, match="max_entries"):
        safe_extract(archive, tmp_path / "out", limits=ExtractionLimits(max_entries=1))


def test_safe_extract_enforces_the_size_caps(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path / "big.zip", {"big.txt": b"x" * 64})
    with pytest.raises(PathGuardError, match="max_entry_bytes"):
        safe_extract(archive, tmp_path / "out", limits=ExtractionLimits(max_entry_bytes=16))
    with pytest.raises(PathGuardError, match="max_total_bytes"):
        safe_extract(archive, tmp_path / "out2", limits=ExtractionLimits(max_total_bytes=16))


def test_safe_extract_rejects_missing_and_unknown_archives(tmp_path: Path) -> None:
    with pytest.raises(PathGuardError):
        safe_extract(tmp_path / "nope.zip", tmp_path / "out")
    plain = tmp_path / "plain.txt"
    plain.write_text("not an archive", encoding="utf-8")
    with pytest.raises(PathGuardError, match="neither a zip nor a tar"):
        safe_extract(plain, tmp_path / "out")
