"""Verify the whole release journey and write down only what was observed.

The owner's question is one sentence: *does the download a user gets actually reach a
built SPICE model on this machine?* This tool answers it by running the journey in
order and recording every stage, so nothing is called verified because a step was
skipped:

1. ``archive``   - locate/download the ZIP, verify its required members, hash the ZIP
   and the ``Install.exe`` inside it, compare the installer's own version stamp with
   ``pyproject.toml``, and check the installer against ``SHA256SUMS.txt``.
2. ``extract``   - unpack with :mod:`zipfile` into a fresh directory, rejecting
   absolute, drive-qualified and ``..`` members here rather than trusting the library.
3. ``install``   - run ``Install.exe --silent --no-launch`` with a timeout and assert
   the documented folder layout and ``.setup.log``.
4. ``environment`` - drive the installed copy's *own* interpreter: version, doctor,
   ``pyvenv.cfg``, config/credential paths inside the copy, and a before/after
   snapshot proving nothing was created outside it.
5. ``ltspice-path`` - write the detected LTspice executable through the copy's own
   config module, read it back with ``setup --json`` and confirm ``doctor`` names it.
6. ``model-build`` / ``model-test`` / ``model-open`` - build a model inside the copy
   on the real product path, publish its artefacts, re-judge it, and open it when the
   revision has that command.
7. ``zip``       - generate a *new* release ZIP through ``installer/build.ps1`` when
   ``--build-zip`` is passed; otherwise say plainly that it was not generated.
8. ``cleanup``   - remove the working copy unless ``--keep``.

Honesty rules this tool enforces on itself:

* every stage reports ``PASS``/``FAIL``/``SKIP(reason)`` with its wall clock and the
  exact command(s) it ran, including the observed output tail and any hashes;
* a stage is never ``PASS`` because a check could not run;
* ``verdict`` is ``PASS`` only when no stage failed and no stage was skipped for an
  environmental reason. Skips the operator asked for (``--no-ltspice``, a missing
  ``--build-zip``, ``--until``) or that a capability in this revision makes impossible
  (``model open`` not implemented yet) are listed in ``skipped_stages`` and make
  ``fully_verified`` false, but they do not turn the run into a failure;
* exit code is 0 for ``PASS``, 1 for ``FAIL``, 2 for ``INCOMPLETE``.

Nothing here writes outside the working directory it creates (except the report
files the caller names) and it never writes into the LTspice installation.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import tomllib
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
GITHUB_REPO = "BasamAhmed640/spice-maker"
CODELOAD_URL = "https://codeload.github.com/{repo}/zip/refs/heads/{ref}"
WORK_MARKER = ".release-verify-work"

#: Entries the harness itself creates in the work directory; they are never reported
#: as "something appeared outside the install root".
HARNESS_OWNED = frozenset({"archive", "extract", "inputs", WORK_MARKER})

#: Stages in execution order. ``--until`` stops after the named one.
STAGES = (
    "archive",
    "extract",
    "install",
    "environment",
    "ltspice-path",
    "model-build",
    "model-test",
    "model-open",
    "zip",
    "cleanup",
)

#: What each source's ZIP is expected to contain. The release bundle ships the same
#: document as ``Read me.txt`` while the repository tracks it as ``INSTALL.txt``;
#: ``README.md`` is part of the repository archive only.
REQUIRED_FOR_SOURCE = {
    "releases": ("Install.exe", "SHA256SUMS.txt", "INSTALL.txt|Read me.txt"),
    "github": ("Install.exe", "SHA256SUMS.txt", "INSTALL.txt|Read me.txt", "README.md"),
    "zip": ("Install.exe", "SHA256SUMS.txt", "INSTALL.txt|Read me.txt"),
}

INSTALL_TIMEOUT_S = 900
PROBE_TIMEOUT_S = 300
BUILD_TIMEOUT_S = 5400
DOWNLOAD_TIMEOUT_S = 1800
OUTPUT_TAIL_CHARS = 2000

#: The offline model build in this revision: the fixture backend writes the bundled
#: regulator template, the committed extraction result supplies the rows, and the
#: committed bindings supply the probes. The datasheet PDF itself is git-ignored and
#: absent, so a generated stand-in sheet takes its place exactly as
#: ``tests/pipeline/test_make_model.py::datasheet_for`` does.
FIXTURE_DIR = REPO_ROOT / "fixtures" / "regulator" / "tps54320"
MODEL_PART = "TPS54320"
MODEL_SUBCKT = "BM_REG_BUCK"

#: Statuses the domain declares; a model build must report one of these.
HONEST_STATUSES = ("PASS", "FAIL", "UNKNOWN", "BLOCKED", "NOT_APPLICABLE")


# --------------------------------------------------------------------------- #
# small helpers


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tail(text: str, limit: int = OUTPUT_TAIL_CHARS) -> str:
    """The end of a child's output: the part that carries the failure."""
    text = text.replace("\r\n", "\n")
    return text if len(text) <= limit else "…" + text[-limit:]


def normalize_version(text: str) -> str:
    """``1.4.0.0`` and ``1.4.0`` name the same release; compare them as such."""
    parts = [part for part in re.split(r"[.\-+]", text.strip()) if part.isdigit()]
    while len(parts) > 3 and parts[-1] == "0":
        parts.pop()
    return ".".join(parts[:3]) if len(parts) >= 3 else text.strip()


def parse_json(text: str) -> Any:
    """A child's JSON payload, or ``None`` when it printed something else."""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except ValueError:
        return None


def load_json_file(path: Path, what: str) -> Any:
    """Read one JSON file, turning any problem into a reported stage failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise StageFailure(
            f"{what} could not be read: {path}: {type(error).__name__}: {error}"
        ) from error


def declared_version(text: str, what: str) -> str:
    """The declared project version, from TOML text, as a reported failure otherwise."""
    try:
        declared = tomllib.loads(text)
        return str(declared["project"]["version"])
    except (ValueError, KeyError, TypeError) as error:
        raise StageFailure(
            f"{what} declares no readable version: {type(error).__name__}: {error}"
        ) from error


def fail_count(payload: dict[str, Any]) -> int:
    """How many rows a model report judged FAIL, without trusting its shape."""
    counts = payload.get("counts")
    if not isinstance(counts, dict):
        return 0
    try:
        return int(counts.get("FAIL") or 0)
    except TypeError, ValueError:
        return 0


def _as_text(value: bytes | str | None) -> str:
    """A timeout's captured output as text, whichever type the platform gave us."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class StageFailure(RuntimeError):
    """A check inside a stage failed; the stage is reported FAIL with this reason."""


# --------------------------------------------------------------------------- #
# running children


@dataclass
class Command:
    """One child process, recorded the way the report needs it."""

    argv: list[str]
    cwd: str | None
    exit_code: int | None
    seconds: float
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def stdout_tail(self) -> str:
        """The end of stdout: what the report keeps, never what the parser reads."""
        return tail(self.stdout)

    @property
    def stderr_tail(self) -> str:
        return tail(self.stderr)

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": subprocess.list2cmdline(self.argv),
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "seconds": round(self.seconds, 3),
            "timed_out": self.timed_out,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
        }


def clean_environment(root: Path | None = None, ltspice: str | None = None) -> dict[str, str]:
    """A child environment with no inherited overrides that could mask the copy.

    ``SPICE_MAKER_ROOT`` is what ``Boardmodeler.cmd`` sets and what makes the copy's
    package resolve its own ``data/``; without it a bare ``python -m boardmodeler.cli``
    resolves ``app_root()`` to ``.venv/Lib``. ``LTSPICE_EXE`` is set only when a probe
    deliberately wants the override instead of the saved setting.
    """
    env = os.environ.copy()
    for name in list(env):
        if name.upper().startswith(("PYTHON", "QT_", "QML", "VIRTUAL_ENV", "PIP_")):
            env.pop(name)
    env.pop("BOARDMODELER_CONFIG", None)
    env.pop("LTSPICE_EXE", None)
    if root is not None:
        env["SPICE_MAKER_ROOT"] = str(root) + os.sep
    if ltspice is not None:
        env["LTSPICE_EXE"] = ltspice
    return env


class Stage:
    """Collects the commands and evidence of one stage."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.commands: list[Command] = []
        self.evidence: dict[str, Any] = {}

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float = PROBE_TIMEOUT_S,
        allow_failure: bool = False,
        what: str | None = None,
    ) -> Command:
        """Run one child with an explicit timeout and record everything about it."""
        started = time.perf_counter()
        timed_out = False
        stdout = stderr = ""
        code: int | None = None
        try:
            completed = subprocess.run(
                [str(item) for item in argv],
                cwd=str(cwd) if cwd else None,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout, stderr, code = completed.stdout, completed.stderr, completed.returncode
        except subprocess.TimeoutExpired as expired:
            timed_out = True
            stdout = _as_text(expired.stdout)
            stderr = _as_text(expired.stderr)
        record = Command(
            argv=[str(item) for item in argv],
            cwd=str(cwd) if cwd else None,
            exit_code=code,
            seconds=time.perf_counter() - started,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
        )
        self.commands.append(record)
        if timed_out:
            raise StageFailure(f"{what or self.name} did not finish within {timeout:g} s")
        if code != 0 and not allow_failure:
            detail = (record.stderr or record.stdout).strip().splitlines()
            raise StageFailure(
                f"{what or self.name} exited {code}: {detail[-1][:300] if detail else 'no output'}"
            )
        return record


# --------------------------------------------------------------------------- #
# source selection


def newest_release_zip(repo: Path) -> Path:
    """The newest ``releases/*.zip`` by mtime: what the owner most recently built."""
    candidates = sorted(
        (path for path in (repo / "releases").glob("*.zip") if path.is_file()),
        key=lambda path: path.stat().st_mtime_ns,
    )
    if not candidates:
        raise StageFailure(
            f"no release ZIP in {repo / 'releases'}; build one with installer/build.ps1 "
            "or pass --source zip --zip PATH"
        )
    return candidates[-1]


def download_github_zip(ref: str, target: Path, stage: Stage) -> dict[str, Any]:
    """The exact GitHub 'Code -> Download ZIP' archive, fetched with urllib."""
    url = CODELOAD_URL.format(repo=GITHUB_REPO, ref=ref)
    request = urllib.request.Request(url, headers={"User-Agent": "spice-maker-release-verify"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as response:
            body = response.read()
    except OSError as exc:
        raise StageFailure(f"download failed: {url}: {type(exc).__name__}: {exc}") from exc
    target.write_bytes(body)
    return {
        "url": url,
        "ref": ref,
        "path": str(target),
        "bytes": len(body),
        "sha256": sha256_bytes(body),
        "seconds": round(time.perf_counter() - started, 3),
    }


# --------------------------------------------------------------------------- #
# ZIP handling


def _member_parts(name: str) -> list[str]:
    normalized = name.replace("\\", "/")
    return [part for part in normalized.split("/") if part not in ("", ".")]


def _reject_member(info: zipfile.ZipInfo) -> str | None:
    """Why a member may not be extracted, or ``None`` when it is safe."""
    name = info.filename
    if "\x00" in name:
        return "NUL in member name"
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        return "absolute member"
    if re.match(r"^[A-Za-z]:", normalized):
        return "drive-qualified member"
    if any(part == ".." for part in _member_parts(normalized)):
        return "parent-directory escape"
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        return "symbolic link member"
    return None


def _archive_root(names: list[str]) -> str:
    """The single top-level folder the archive wraps its files in, or ``""``.

    Only file members count: a directory entry for the wrapper folder itself (for
    example ``spice-maker-main/``) must not be mistaken for a top-level file.
    """
    files = [name for name in names if not name.endswith(("/", "\\"))]
    tops = {_member_parts(name)[0] for name in files if _member_parts(name)}
    if len(tops) != 1:
        return ""
    return next(iter(tops))


def _find_member(names: list[str], root: str, wanted: str) -> str | None:
    """Locate a required file at the top level of the archive's root folder."""
    target = f"{root}/{wanted}" if root else wanted
    for name in names:
        normalized = name.replace("\\", "/").rstrip("/")
        if normalized.lower() == target.lower():
            return name
    return None


def extract_archive(zip_path: Path, dest: Path) -> dict[str, Any]:
    """Extract with explicit member validation, then prove nothing escaped."""
    dest.mkdir(parents=True, exist_ok=True)
    resolved_dest = dest.resolve()
    rejected: list[dict[str, str]] = []
    files = 0
    total_bytes = 0
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        for info in infos:
            reason = _reject_member(info)
            if reason is not None:
                rejected.append({"member": info.filename, "reason": reason})
                continue
            if info.is_dir():
                continue
            parts = _member_parts(info.filename)
            target = dest.joinpath(*parts)
            if not target.resolve().is_relative_to(resolved_dest):
                rejected.append({"member": info.filename, "reason": "target escaped the root"})
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink, length=1024 * 1024)
            files += 1
            total_bytes += info.file_size
    escaped = [
        str(path)
        for path in dest.rglob("*")
        if path.is_file() and not path.resolve().is_relative_to(resolved_dest)
    ]
    return {
        "dest": str(dest),
        "members": len(infos),
        "files_extracted": files,
        "uncompressed_bytes": total_bytes,
        "rejected_members": rejected,
        "files_outside_dest": escaped,
    }


# --------------------------------------------------------------------------- #
# version stamps and outside-state snapshots


def _powershell_literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def file_version(stage: Stage, path: Path) -> dict[str, Any]:
    """The version the executable carries, read from its PE version resource."""
    script = (
        f"$v=(Get-Item -LiteralPath {_powershell_literal(path)}).VersionInfo;"
        "Write-Output $v.ProductVersion; Write-Output $v.FileVersion"
    )
    record = stage.run(
        ["powershell", "-NoProfile", "-Command", script],
        timeout=120,
        allow_failure=True,
        what=f"reading the version of {path.name}",
    )
    lines = [line.strip() for line in record.stdout.splitlines() if line.strip()]
    product = lines[0] if lines else ""
    file_value = lines[1] if len(lines) > 1 else ""
    return {
        "path": str(path),
        "product_version": product,
        "file_version": file_value,
        "normalized": normalize_version(product or file_value),
    }


def document_version(path: Path) -> dict[str, Any]:
    """The version the shipped INSTALL/Read me text names on its first line."""
    try:
        first = path.read_text(encoding="utf-8-sig").splitlines()[0]
    except OSError, IndexError:
        return {"path": str(path), "line": "", "version": ""}
    match = re.search(r"\b(\d+\.\d+\.\d+)\b", first)
    return {"path": str(path), "line": first.strip(), "version": match.group(1) if match else ""}


def appdata_entries() -> dict[str, Any]:
    """Anything the product must not create in the user profile, by mtime."""
    entries: dict[str, Any] = {}
    for variable in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if not base:
            continue
        for pattern in ("SpiceMaker*", "Spice Maker*", "BoardModeler*"):
            for path in Path(base).glob(pattern):
                with contextlib.suppress(OSError):
                    entries[f"{variable}/{path.name}"] = {
                        "mtime_ns": path.stat().st_mtime_ns,
                        "is_dir": path.is_dir(),
                    }
    return entries


def outside_snapshot(work: Path, install_root: Path | None) -> dict[str, Any]:
    """Parent-folder entries and user-profile app state, so changes can be reported."""
    parent = {}
    with contextlib.suppress(OSError):
        for path in work.iterdir():
            if path.name in HARNESS_OWNED:
                continue
            if install_root is not None and path.resolve() == install_root.resolve():
                continue
            with contextlib.suppress(OSError):
                parent[path.name] = path.stat().st_mtime_ns
    return {"parent": parent, "appdata": appdata_entries()}


def outside_changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    created = sorted(set(after["appdata"]) - set(before["appdata"]))
    changed = sorted(
        name
        for name in set(after["appdata"]) & set(before["appdata"])
        if after["appdata"][name]["mtime_ns"] != before["appdata"][name]["mtime_ns"]
    )
    new_parent = sorted(set(after["parent"]) - set(before["parent"]))
    return {
        "appdata_created": created,
        "appdata_changed": changed,
        "parent_entries_created": new_parent,
    }


# --------------------------------------------------------------------------- #
# LTspice detection


def detect_ltspice() -> dict[str, Any]:
    """The simulator this machine has: an explicit override, then the documented path.

    The application itself never searches; this tool may, because it has to *set* the
    path the copy will use. The repository's own explicit ``discover()`` is the last
    resort so a non-default installation is still found and reported with its source.
    """
    candidates: list[tuple[Path, str]] = []
    override = os.environ.get("LTSPICE_EXE")
    if override:
        candidates.append((Path(override), "LTSPICE_EXE"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(
            (Path(local) / "Programs" / "ADI" / "LTspice" / "LTspice.exe", "LOCALAPPDATA default")
        )
    for path, source in candidates:
        if path.is_file():
            return {"path": str(path), "source": source, "version": None}
    with contextlib.suppress(Exception):
        from boardmodeler.simulation.ltspice import discover

        outcome = discover()
        if outcome.install is not None:
            return {
                "path": str(outcome.install.path),
                "source": f"discover() ({outcome.install.source})",
                "version": None,
            }
    return {"path": None, "source": None, "version": None}


# --------------------------------------------------------------------------- #
# offline model inputs


def write_minimal_pdf(path: Path, lines: list[str]) -> Path:
    """A valid one-page PDF from the standard library alone.

    The product path needs a datasheet *file*; the real PDF is git-ignored and absent
    from a fresh checkout, so the harness writes this stand-in and says so in the
    report. It is deliberately plain text, not a fabricated datasheet.
    """

    def escape(text: str) -> str:
        return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    content = "BT /F1 11 Tf 40 740 Td 16 TL\n" + "\n".join(
        f"({escape(line)}) Tj T*" for line in lines
    )
    content += "\nET"
    stream = content.encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(payload)
    payload += f"xref\n0 {len(objects) + 1}\n".encode()
    payload += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        payload += f"{offset:010d} 00000 n \n".encode()
    payload += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(payload))
    return path


def write_stand_in_requirements(source: Path, target: Path) -> dict[str, Any]:
    """The committed extraction result, declared as the stand-in it now is.

    The rows keep their own limits and citations but are marked ``TEST_FIXTURE`` /
    ``synthetic_fixture`` so the run can never report datasheet citation coverage it
    did not observe. That is the same convention the repository's own offline model
    test uses when the git-ignored datasheet is absent.
    """
    raw = load_json_file(source, "the committed requirements fixture")
    rows = raw.get("requirements")
    if not isinstance(rows, list) or not rows:
        raise StageFailure(f"{source} carries no requirements list")
    for row in rows:
        row["origin"] = "TEST_FIXTURE"
        for evidence in row.get("evidence") or []:
            evidence["extraction"] = "synthetic_fixture"
    target.write_text(json.dumps(raw, indent=2), encoding="utf-8", newline="\n")
    return {"rows": len(rows), "source_sha256": sha256_file(source), "sha256": sha256_file(target)}


# --------------------------------------------------------------------------- #
# stages


def stage_archive(ctx: Context, stage: Stage) -> dict[str, Any]:
    """Locate the ZIP, verify what is inside it, and compare the version stamps."""
    if ctx.options.source == "releases":
        chosen = newest_release_zip(ctx.repo)
        copied = ctx.archive_dir / chosen.name
        shutil.copy2(chosen, copied)
        source = {
            "kind": "releases",
            "located": str(chosen),
            "chosen_because": "newest releases/*.zip by mtime",
            "url": None,
        }
        zip_path = copied
    elif ctx.options.source == "github":
        zip_path = ctx.archive_dir / f"{GITHUB_REPO.split('/')[-1]}-{ctx.options.ref}.zip"
        download = download_github_zip(ctx.options.ref, zip_path, stage)
        source = {"kind": "github", "located": str(zip_path), "chosen_because": None, **download}
    else:
        assert ctx.options.zip_path is not None
        zip_path = ctx.archive_dir / ctx.options.zip_path.name
        shutil.copy2(ctx.options.zip_path, zip_path)
        source = {
            "kind": "zip",
            "located": str(ctx.options.zip_path),
            "chosen_because": "explicit --zip",
            "url": None,
        }
    ctx.zip_path = zip_path

    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        root = _archive_root(names)
        required: dict[str, Any] = {}
        missing: list[str] = []
        for wanted in REQUIRED_FOR_SOURCE[ctx.options.source]:
            alternatives = wanted.split("|")
            member = next(
                (found for name in alternatives if (found := _find_member(names, root, name))),
                None,
            )
            if member is None:
                missing.append(wanted)
                required[wanted] = {"present": False}
                continue
            data = archive.read(member)
            required[wanted] = {
                "present": True,
                "member": member,
                "bytes": len(data),
                "sha256": sha256_bytes(data),
            }
        installer_member = required["Install.exe"].get("member")
        if missing:
            raise StageFailure(f"the ZIP is missing required member(s): {', '.join(missing)}")
        installer_bytes = archive.read(installer_member)
        sums_member = required["SHA256SUMS.txt"].get("member")
        sums_text = archive.read(sums_member).decode("utf-8-sig", "replace") if sums_member else ""
        document_member = next(
            (
                found
                for name in ("INSTALL.txt", "Read me.txt")
                if (found := _find_member(names, root, name))
            ),
            None,
        )
        document_bytes = archive.read(document_member) if document_member else b""
    zip_hash = sha256_file(zip_path)
    installer_hash = sha256_bytes(installer_bytes)

    recorded: str | None = None
    for line in sums_text.splitlines():
        fields = line.split()
        if len(fields) >= 2 and Path(fields[-1]).name.lower() == "install.exe":
            recorded = fields[0].strip().lower()
            break
    if recorded is None:
        raise StageFailure("SHA256SUMS.txt does not name Install.exe")
    if recorded != installer_hash:
        raise StageFailure(
            f"SHA256SUMS.txt records {recorded} but the ZIP's Install.exe is {installer_hash}"
        )

    # The installer carries its own version; "works for the newest version" means it
    # agrees with the pyproject.toml of the checkout that produced it (the archive's
    # own copy for --source github, the local checkout otherwise).
    local_pyproject = ctx.repo / "pyproject.toml"
    archive_pyproject = _find_member(names, root, "pyproject.toml")
    version_source = "local checkout"
    version_path = local_pyproject
    if archive_pyproject is not None:
        with zipfile.ZipFile(zip_path) as archive:
            declared_text = archive.read(archive_pyproject).decode("utf-8")
        version_path = ctx.archive_dir / "pyproject.toml"
        version_path.write_text(declared_text, encoding="utf-8", newline="\n")
        version_source = "archive"
        pyproject_version = declared_version(declared_text, f"{archive_pyproject} in the ZIP")
    else:
        try:
            local_text = local_pyproject.read_text(encoding="utf-8")
        except OSError as error:
            raise StageFailure(
                f"pyproject.toml could not be read: {local_pyproject}: {error}"
            ) from error
        pyproject_version = declared_version(local_text, str(local_pyproject))
    ctx.expected_version = pyproject_version
    ctx.expected_version_source = version_source
    ctx.expected_installer_sha256 = installer_hash

    # The version stamp is read from the bytes the ZIP carries, not from the extracted
    # copy: that is the artefact a downloader receives.
    installer_probe = ctx.archive_dir / "Install.exe"
    installer_probe.write_bytes(installer_bytes)
    document_probe = ctx.archive_dir / (document_member.split("/")[-1] if document_member else "")
    if document_bytes:
        document_probe.write_bytes(document_bytes)
    stamp = file_version(stage, installer_probe)
    document = document_version(document_probe)
    if stamp["normalized"] != pyproject_version:
        raise StageFailure(
            f"Install.exe carries version {stamp['product_version']!r} (normalized "
            f"{stamp['normalized']!r}) while pyproject.toml declares {pyproject_version!r}"
        )
    if document["version"] and document["version"] != pyproject_version:
        raise StageFailure(
            f"the shipped document names version {document['version']!r} while pyproject.toml "
            f"declares {pyproject_version!r}"
        )

    stage.evidence.update(
        {
            "source": source,
            "zip": {"path": str(zip_path), "bytes": zip_path.stat().st_size, "sha256": zip_hash},
            "members": len(names),
            "archive_root": root,
            "required_members": required,
            "missing_required": missing,
            "sha256sums": {"member": sums_member, "recorded_install_exe": recorded},
            "installer_sha256_matches_sums": recorded == installer_hash,
            "installer": {"bytes": len(installer_bytes), "sha256": installer_hash},
            "installer_version": stamp,
            "pyproject_version": {
                "path": str(version_path),
                "version": pyproject_version,
                "source": version_source,
            },
            "document_version": document,
            "version_matches_pyproject": stamp["normalized"] == pyproject_version,
        }
    )
    return stage.evidence


def stage_extract(ctx: Context, stage: Stage) -> dict[str, Any]:
    """Unpack the verified ZIP into a fresh directory with explicit path checks."""
    assert ctx.zip_path is not None
    if ctx.extract_dir.exists():
        try:
            shutil.rmtree(ctx.extract_dir)
        except OSError as error:
            raise StageFailure(f"the previous extraction could not be removed: {error}") from error
    outcome = extract_archive(ctx.zip_path, ctx.extract_dir)
    if outcome["rejected_members"]:
        raise StageFailure(f"the ZIP contains unsafe member(s): {outcome['rejected_members'][:3]}")
    if outcome["files_outside_dest"]:
        raise StageFailure(
            f"extraction wrote outside {ctx.extract_dir}: {outcome['files_outside_dest']}"
        )
    with zipfile.ZipFile(ctx.zip_path) as archive:
        root = _archive_root(archive.namelist())
    install_root = ctx.extract_dir / root if root else ctx.extract_dir
    installer = install_root / "Install.exe"
    if not installer.is_file():
        raise StageFailure(f"no Install.exe under {install_root}")
    ctx.install_root = install_root
    ctx.installer_probe = installer
    for document in ("INSTALL.txt", "Read me.txt"):
        candidate = install_root / document
        if candidate.is_file():
            ctx.installer_document = candidate
            break
    stage.evidence.update(
        {
            **outcome,
            "archive_root": root,
            "install_root": str(install_root),
            "installer": {"path": str(installer), "sha256": sha256_file(installer)},
        }
    )
    if ctx.expected_installer_sha256 and stage.evidence["installer"]["sha256"] != (
        ctx.expected_installer_sha256
    ):
        raise StageFailure("the extracted Install.exe differs from the one inside the ZIP")
    return stage.evidence


def stage_install(ctx: Context, stage: Stage) -> dict[str, Any]:
    """Run the installer silently and assert the folder layout it documents."""
    assert ctx.install_root is not None
    before = outside_snapshot(ctx.work, ctx.install_root)
    ctx.outside_before_install = before
    stage.run(
        [str(ctx.install_root / "Install.exe"), "--silent", "--no-launch"],
        cwd=ctx.install_root,
        env=clean_environment(),
        timeout=INSTALL_TIMEOUT_S,
        what="Install.exe --silent --no-launch",
    )
    layout = {
        "app/SpiceMaker.exe": ctx.install_root / "app/SpiceMaker.exe",
        "app": ctx.install_root / "app",
        ".venv": ctx.install_root / ".venv",
        ".venv/Scripts/python.exe": ctx.install_root / ".venv/Scripts/python.exe",
        "env": ctx.install_root / "env",
        "Start.cmd": ctx.install_root / "Start.cmd",
        "Boardmodeler.cmd": ctx.install_root / "Boardmodeler.cmd",
        "Spice Maker.lnk": ctx.install_root / "Spice Maker.lnk",
        ".spice-maker-root": ctx.install_root / ".spice-maker-root",
        ".setup.log": ctx.install_root / ".setup.log",
    }
    missing = [name for name, path in layout.items() if not path.exists()]
    if missing:
        raise StageFailure(f"the installed copy is missing: {', '.join(missing)}")
    log = ctx.install_root / ".setup.log"
    if log.stat().st_size == 0:
        raise StageFailure(".setup.log exists but is empty; the installer's own record is missing")
    after = outside_snapshot(ctx.work, ctx.install_root)
    changes = outside_changes(before, after)
    if changes["appdata_created"]:
        raise StageFailure(
            f"the installer created user-profile state: {changes['appdata_created']}"
        )
    stage.evidence.update(
        {
            "root": str(ctx.install_root),
            "layout": {
                name: {
                    "present": True,
                    "is_dir": path.is_dir(),
                    "bytes": None if path.is_dir() else path.stat().st_size,
                }
                for name, path in layout.items()
            },
            "setup_log": {
                "path": str(log),
                "bytes": log.stat().st_size,
                "sha256": sha256_file(log),
                "tail": tail(log.read_text(encoding="utf-8", errors="replace"), 1200),
            },
            "app_executable": {
                "bytes": layout["app/SpiceMaker.exe"].stat().st_size,
                "sha256": sha256_file(layout["app/SpiceMaker.exe"]),
            },
            "outside_root_changes": changes,
            "installer_seconds": round(stage.commands[-1].seconds, 3),
        }
    )
    return stage.evidence


def _copy_python(ctx: Context) -> Path:
    assert ctx.install_root is not None
    return ctx.install_root / ".venv/Scripts/python.exe"


def stage_environment(ctx: Context, stage: Stage) -> dict[str, Any]:
    """Drive the installed copy's own interpreter and prove it stays in its folder."""
    assert ctx.install_root is not None
    root = ctx.install_root
    python = _copy_python(ctx)
    if not python.is_file():
        raise StageFailure(f"the copy has no interpreter at {python}")
    env = clean_environment(root)
    before = outside_snapshot(ctx.work, root)

    version_record = stage.run(
        [str(python), "-m", "boardmodeler.cli", "version", "--json"],
        cwd=root,
        env=env,
        timeout=PROBE_TIMEOUT_S,
        what="the copy's version command",
    )
    version_payload = parse_json(version_record.stdout)
    if not isinstance(version_payload, dict) or "version" not in version_payload:
        raise StageFailure("the copy's version command printed no machine-readable payload")
    if version_payload["version"] != ctx.expected_version:
        raise StageFailure(
            f"the installed package reports {version_payload['version']!r} while the installer "
            f"carries {ctx.expected_version!r}"
        )

    doctor_record = stage.run(
        [str(python), "-m", "boardmodeler.cli", "doctor", "--json", "--no-smoke"],
        cwd=root,
        env=env,
        timeout=PROBE_TIMEOUT_S,
        what="the copy's doctor command",
    )
    doctor = parse_json(doctor_record.stdout)
    if not isinstance(doctor, dict):
        raise StageFailure("the copy's doctor command printed no machine-readable payload")
    interpreter = str(doctor.get("python") or "")
    if not interpreter.startswith("3.14"):
        raise StageFailure(f"the copy runs Python {interpreter!r}; 3.14.x is required")
    config_path = Path(str((doctor.get("config") or {}).get("path") or ""))
    if not config_path.is_relative_to(root):
        raise StageFailure(f"the copy resolves its config to {config_path}, outside {root}")

    identity_record = stage.run(
        [
            str(python),
            "-c",
            "import json,sys;from boardmodeler.config import config_path;"
            "from boardmodeler.security.credentials import credential_path;"
            "print(json.dumps({'prefix':sys.prefix,'base':sys.base_prefix,"
            "'config':str(config_path()),'credentials':str(credential_path())}))",
        ],
        cwd=root,
        env=env,
        timeout=PROBE_TIMEOUT_S,
        what="the copy's storage paths",
    )
    identity = parse_json(identity_record.stdout)
    if not isinstance(identity, dict):
        raise StageFailure("the copy's storage probe printed no machine-readable payload")
    for key in ("config", "credentials"):
        if not Path(str(identity[key])).is_relative_to(root):
            raise StageFailure(f"the copy's {key} path {identity[key]} is outside {root}")
    if Path(str(identity["prefix"])) != root / ".venv":
        raise StageFailure(
            f"the interpreter's prefix is {identity['prefix']}, not {root / '.venv'}"
        )
    if Path(str(identity["base"])) != root / "env/python":
        raise StageFailure(
            f"the interpreter's base is {identity['base']}, not {root / 'env/python'}"
        )

    venv_config = (root / ".venv/pyvenv.cfg").read_text(encoding="utf-8", errors="replace")
    home_line = next(
        (line for line in venv_config.splitlines() if line.strip().startswith("home")), ""
    )
    home = home_line.split("=", 1)[1].strip() if "=" in home_line else ""
    if Path(home) != root / "env/python":
        raise StageFailure(f"pyvenv.cfg points at {home!r}, not this copy's env/python")

    override_probe: dict[str, Any] = {"ran": False}
    if ctx.ltspice.get("path"):
        override_env = clean_environment(root, ltspice=str(ctx.ltspice["path"]))
        override_record = stage.run(
            [str(python), "-m", "boardmodeler.cli", "doctor", "--json", "--no-smoke"],
            cwd=root,
            env=override_env,
            timeout=PROBE_TIMEOUT_S,
            what="the copy's doctor command with LTSPICE_EXE",
        )
        override = parse_json(override_record.stdout) or {}
        found = (override.get("ltspice") or {}).get("path")
        override_probe = {
            "ran": True,
            "path": found,
            "env_override": (override.get("ltspice") or {}).get("env_override"),
            "matches": str(found or "") == str(ctx.ltspice["path"]),
        }
        if not override_probe["matches"]:
            raise StageFailure(
                f"doctor did not honour LTSPICE_EXE: reported {found!r}, "
                f"expected {ctx.ltspice['path']!r}"
            )
    else:
        override_probe = {"ran": False, "reason": "no LTspice executable was detected"}

    after = outside_snapshot(ctx.work, root)
    changes = outside_changes(before, after)
    if changes["appdata_created"]:
        raise StageFailure(
            f"running the copy created user-profile state: {changes['appdata_created']}"
        )
    stage.evidence.update(
        {
            "python": str(python),
            "interpreter": interpreter,
            "package_version": version_payload["version"],
            "doctor_config": {
                "path": str(config_path),
                "exists": (doctor.get("config") or {}).get("exists"),
            },
            "doctor_ltspice": {
                "found": (doctor.get("ltspice") or {}).get("found"),
                "path": (doctor.get("ltspice") or {}).get("path"),
                "reason": (doctor.get("ltspice") or {}).get("reason"),
                "setup_required": (doctor.get("ltspice") or {}).get("setup_required"),
            },
            "paths": identity,
            "pyvenv_cfg": {"home": home, "sha256": sha256_bytes(venv_config.encode("utf-8"))},
            "env_override_probe": override_probe,
            "outside_root_changes": changes,
            "run_outside_baseline": outside_changes(ctx.outside_baseline, after),
        }
    )
    return stage.evidence


def stage_ltspice_path(ctx: Context, stage: Stage) -> dict[str, Any]:
    """Write the simulator path through the copy's own config module and read it back."""
    assert ctx.install_root is not None
    root = ctx.install_root
    python = _copy_python(ctx)
    ltspice = str(ctx.ltspice["path"])
    env = clean_environment(root)
    written = stage.run(
        [
            str(python),
            "-c",
            "from boardmodeler.config import load_config, save_config;"
            "c = load_config();c.ltspice.path = __import__('sys').argv[1];"
            "print(save_config(c))",
            ltspice,
        ],
        cwd=root,
        env=env,
        timeout=PROBE_TIMEOUT_S,
        what="writing the LTspice path through the copy's config module",
    )
    config_file = Path(written.stdout.strip().splitlines()[-1])
    if not config_file.is_relative_to(root):
        raise StageFailure(f"the copy wrote its config to {config_file}, outside {root}")

    # The Qt-free .venv is the documented CLI environment, but ``setup`` imports the
    # dialog module before it looks at --json; the frozen app is the documented route
    # for commands that open a window. Try the venv first and report what happened.
    venv_attempt = stage.run(
        [str(python), "-m", "boardmodeler.cli", "setup", "--json"],
        cwd=root,
        env=env,
        timeout=PROBE_TIMEOUT_S,
        allow_failure=True,
        what="the copy's setup --json through .venv",
    )
    payload = parse_json(venv_attempt.stdout)
    served_by = "venv"
    if not isinstance(payload, dict) or "ltspice_path" not in payload:
        served_by = "frozen app"
        app = root / "app/SpiceMaker.exe"
        if not app.is_file():
            raise StageFailure("setup --json is unavailable: no frozen app to serve it")
        app_record = stage.run(
            [str(app), "--cli", "setup", "--json"],
            cwd=root,
            env=env,
            timeout=PROBE_TIMEOUT_S,
            what="the copy's setup --json through the frozen app",
        )
        payload = parse_json(app_record.stdout)
        if not isinstance(payload, dict) or "ltspice_path" not in payload:
            raise StageFailure("setup --json printed no machine-readable settings")
    read_back = str(payload.get("ltspice_path") or "")
    if read_back != ltspice:
        raise StageFailure(f"setup --json read back {read_back!r} after writing {ltspice!r}")

    doctor_record = stage.run(
        [str(python), "-m", "boardmodeler.cli", "doctor", "--json", "--no-smoke"],
        cwd=root,
        env=env,
        timeout=PROBE_TIMEOUT_S,
        what="the copy's doctor command after saving LTspice",
    )
    doctor = parse_json(doctor_record.stdout) or {}
    section = doctor.get("ltspice") or {}
    if str(section.get("path") or "") != ltspice or not section.get("found"):
        raise StageFailure(f"doctor does not name the saved LTspice: {section}")
    stage.evidence.update(
        {
            "written": ltspice,
            "written_by": "the copy's own boardmodeler.config.load_config/save_config",
            "config_file": {"path": str(config_file), "sha256": sha256_file(config_file)},
            "read_back": read_back,
            "read_back_served_by": served_by,
            "venv_setup_json": {
                "exit_code": venv_attempt.exit_code,
                "detail": venv_attempt.stderr_tail.strip().splitlines()[-1]
                if venv_attempt.stderr_tail.strip()
                else "",
            },
            "doctor": {
                "path": section.get("path"),
                "found": section.get("found"),
                "source": section.get("source"),
            },
        }
    )
    return stage.evidence


def stage_model_build(ctx: Context, stage: Stage) -> dict[str, Any]:
    """Build a model on the real product path inside the installed copy."""
    assert ctx.install_root is not None
    root = ctx.install_root
    python = _copy_python(ctx)
    inputs = ctx.inputs_dir
    inputs.mkdir(parents=True, exist_ok=True)
    sheet = write_minimal_pdf(
        inputs / "standin_datasheet.pdf",
        [
            "TEST FIXTURE: harness stand-in sheet for the release-verification journey.",
            f"Part under test: {MODEL_PART}; no datasheet content is reproduced here.",
        ],
    )
    derived_path = inputs / "requirements-standin.json"
    derived = write_stand_in_requirements(FIXTURE_DIR / "requirements.json", derived_path)
    bindings = FIXTURE_DIR / "probes.json"
    out_dir = root / "models/release-verify"
    if out_dir.exists():
        try:
            shutil.rmtree(out_dir)
        except OSError as error:
            raise StageFailure(
                f"the previous model output could not be removed: {error}"
            ) from error

    # What the offline sanity path does in this revision, recorded whatever it says.
    sanity_out = root / "models/release-verify-sanity-probe"
    if sanity_out.exists():
        try:
            shutil.rmtree(sanity_out)
        except OSError as error:
            raise StageFailure(f"the sanity probe output could not be removed: {error}") from error
    sanity_record = stage.run(
        [
            str(python),
            "-m",
            "boardmodeler.cli",
            "model",
            "build",
            "--part",
            MODEL_PART,
            "--requirements",
            str(derived_path),
            "--bindings",
            str(bindings),
            "--backend",
            "fixture",
            "--sanity",
            "--out",
            str(sanity_out),
            "--json",
        ],
        cwd=root,
        env=clean_environment(root),
        timeout=PROBE_TIMEOUT_S,
        allow_failure=True,
        what="the offline --sanity model build",
    )
    sanity_payload = parse_json(sanity_record.stdout) or {}
    ctx.model_inputs = {
        "datasheet": {
            "path": str(sheet),
            "sha256": sha256_file(sheet),
            "stand_in": True,
            "why": "the real TPS54320 PDF is git-ignored and absent; the committed extraction "
            "is taken at its own word as tests/pipeline/test_make_model.py does",
        },
        "requirements": {
            "path": str(derived_path),
            "source": str(FIXTURE_DIR / "requirements.json"),
            **derived,
            "transforms": [
                "rows marked origin=TEST_FIXTURE and evidence extraction=synthetic_fixture",
                "pin_map omitted: the full path does not need it and the committed pinmap.json "
                "exceeds PinDefinition's evidence limit",
            ],
        },
        "bindings": {"path": str(bindings), "sha256": sha256_file(bindings)},
    }
    sanity_probe = {
        "command": subprocess.list2cmdline(sanity_record.argv),
        "exit_code": sanity_record.exit_code,
        "status": sanity_payload.get("status"),
        "detail": sanity_payload.get("detail"),
    }
    if ctx.options.no_ltspice:
        stage.evidence.update(
            {
                "inputs": ctx.model_inputs,
                "sanity_probe": sanity_probe,
                "out_dir": str(out_dir),
            }
        )
        raise SkipStage(
            "the full offline model build requires LTspice; the --sanity path was attempted and "
            f"refused ({sanity_probe['status']}: {sanity_probe['detail']})",
            expected=True,
        )

    record = stage.run(
        [
            str(python),
            "-m",
            "boardmodeler.cli",
            "model",
            "build",
            "--part",
            MODEL_PART,
            "--subckt",
            MODEL_SUBCKT,
            "--datasheet",
            str(sheet),
            "--requirements",
            str(derived_path),
            "--bindings",
            str(bindings),
            "--backend",
            "fixture",
            "--no-reinforce",
            "--out",
            str(out_dir),
            "--json",
        ],
        cwd=root,
        env=clean_environment(root),
        timeout=BUILD_TIMEOUT_S,
        what="the model build",
    )
    payload = parse_json(record.stdout) or {}
    status = str(payload.get("status") or "")
    if status not in HONEST_STATUSES:
        raise StageFailure(
            f"the model build reported status {status!r}, not one of {HONEST_STATUSES}"
        )
    if status == "BLOCKED":
        raise StageFailure(f"the model build was BLOCKED: {payload.get('detail')}")
    artifacts = {
        "lib": out_dir / f"{MODEL_SUBCKT}.lib",
        "asy": out_dir / f"{MODEL_SUBCKT}.asy",
        "card": out_dir / "MODEL_CARD.md",
        "results": out_dir / "results.json",
    }
    missing = [name for name, path in artifacts.items() if not path.is_file()]
    if missing:
        raise StageFailure(f"the model build published no {', '.join(missing)}")
    if artifacts["lib"].stat().st_size == 0:
        raise StageFailure("the published .lib is empty")
    counts = payload.get("counts") or {}
    if fail_count(payload) > 0:
        raise StageFailure(f"the model build judged FAIL row(s): {payload.get('detail')}")
    ctx.model_out = out_dir
    stage.evidence.update(
        {
            "inputs": ctx.model_inputs,
            "sanity_probe": sanity_probe,
            "out_dir": str(out_dir),
            "status": status,
            "detail": payload.get("detail"),
            "counts": counts,
            "rows": len(payload.get("rows") or []),
            "artifacts": {
                name: {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
                for name, path in artifacts.items()
            },
            "load_check": _load_check(ctx, out_dir),
        }
    )
    return stage.evidence


def _load_check(ctx: Context, out_dir: Path) -> dict[str, Any]:
    """The sanity/load receipt the build wrote, when it wrote one."""
    receipt = out_dir / "sanity-report.json"
    if not receipt.is_file():
        return {"present": False}
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {"present": True, "path": str(receipt), "unreadable": True}
    return {
        "present": True,
        "path": str(receipt),
        "sha256": sha256_file(receipt),
        "load": payload.get("load"),
        "electrical_accuracy_verified": payload.get("electrical_accuracy_verified"),
    }


class SkipStage(RuntimeError):
    """A stage cannot run here; the reason and whether the operator asked for it."""

    def __init__(self, reason: str, *, expected: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.expected = expected


def stage_model_test(ctx: Context, stage: Stage) -> dict[str, Any]:
    """Re-run the probe harness on the built model and compare the part it names."""
    assert ctx.install_root is not None and ctx.model_out is not None
    root = ctx.install_root
    record = stage.run(
        [
            str(_copy_python(ctx)),
            "-m",
            "boardmodeler.cli",
            "model",
            "test",
            "--out",
            str(ctx.model_out),
            "--json",
        ],
        cwd=root,
        env=clean_environment(root),
        timeout=BUILD_TIMEOUT_S,
        what="the model re-test",
    )
    payload = parse_json(record.stdout) or {}
    status = str(payload.get("status") or "")
    if status not in HONEST_STATUSES:
        raise StageFailure(f"model test reported status {status!r}, not one of {HONEST_STATUSES}")
    if str(payload.get("part") or "") != MODEL_PART:
        raise StageFailure(
            f"model test names part {payload.get('part')!r}, expected {MODEL_PART!r}"
        )
    report = ctx.model_out / "harness-report.json"
    if not report.is_file():
        raise StageFailure("model test wrote no harness-report.json")
    counts = payload.get("counts") or {}
    if fail_count(payload) > 0:
        raise StageFailure("the re-test judged FAIL row(s)")
    stage.evidence.update(
        {
            "out_dir": str(ctx.model_out),
            "status": status,
            "part": payload.get("part"),
            "counts": counts,
            "harness_report": {
                "path": str(report),
                "bytes": report.stat().st_size,
                "sha256": sha256_file(report),
            },
        }
    )
    return stage.evidence


def stage_model_open(ctx: Context, stage: Stage) -> dict[str, Any]:
    """Open the built model through the copy's CLI when this revision has the command."""
    assert ctx.install_root is not None and ctx.model_out is not None
    root = ctx.install_root
    record = stage.run(
        [
            str(_copy_python(ctx)),
            "-m",
            "boardmodeler.cli",
            "model",
            "open",
            "--out",
            str(ctx.model_out),
            "--json",
        ],
        cwd=root,
        env=clean_environment(root),
        timeout=PROBE_TIMEOUT_S,
        allow_failure=True,
        what="the model open command",
    )
    output = (record.stdout + "\n" + record.stderr).lower()
    if record.exit_code != 0 and (
        "invalid choice" in output or "specify a model subcommand" in output
    ):
        raise SkipStage(
            "model open is not implemented in this revision (another agent is adding it); "
            "wire this stage when the command lands",
            expected=True,
        )
    if record.exit_code != 0:
        raise StageFailure(f"model open exited {record.exit_code}: {record.stderr_tail.strip()}")
    payload = parse_json(record.stdout) or {}
    stage.evidence.update(
        {
            "out_dir": str(ctx.model_out),
            "status": payload.get("status"),
            "payload": payload if isinstance(payload, dict) else None,
        }
    )
    return stage.evidence


def stage_zip(ctx: Context, stage: Stage) -> dict[str, Any]:
    """Generate a new release ZIP through the installer build when asked."""
    version = ctx.expected_version
    if not ctx.options.build_zip:
        raise SkipStage(
            "requires the installer build; pass --build-zip to run installer/build.ps1 "
            "(long, network-using) and package a fresh release ZIP",
            expected=True,
        )
    build_script = ctx.repo / "installer/build.ps1"
    if not build_script.is_file():
        raise StageFailure(f"no installer build script at {build_script}")
    before = {
        name: sha256_file(ctx.repo / name)
        for name in ("Install.exe", "INSTALL.txt", "SHA256SUMS.txt")
        if (ctx.repo / name).is_file()
    }
    stage.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(build_script),
            "-Version",
            version,
        ],
        cwd=ctx.repo,
        env=clean_environment(),
        timeout=BUILD_TIMEOUT_S,
        what="installer/build.ps1",
    )
    product = "SpiceMaker"
    new_zip = ctx.repo / f"releases/{product}-{version}-Windows-x64.zip"
    if not new_zip.is_file():
        raise StageFailure(f"the build reported success but {new_zip} does not exist")
    with zipfile.ZipFile(new_zip) as archive:
        names = archive.namelist()
        root = _archive_root(names)
        missing = [
            wanted
            for wanted in ("Install.exe", "SHA256SUMS.txt")
            if _find_member(names, root, wanted) is None
        ]
        if missing:
            raise StageFailure(f"the new ZIP is missing {missing}")
        member = _find_member(names, root, "Install.exe")
        built_installer = archive.read(member)
    repo_installer = ctx.repo / "Install.exe"
    if sha256_bytes(built_installer) != sha256_file(repo_installer):
        raise StageFailure("the new ZIP's Install.exe differs from the freshly built Install.exe")
    stamp = file_version(stage, repo_installer)
    if stamp["normalized"] != version:
        raise StageFailure(
            f"the freshly built Install.exe carries {stamp['product_version']!r}, expected {version!r}"
        )
    after = {
        name: sha256_file(ctx.repo / name)
        for name in ("Install.exe", "INSTALL.txt", "SHA256SUMS.txt")
        if (ctx.repo / name).is_file()
    }
    stage.evidence.update(
        {
            "version": version,
            "new_zip": {
                "path": str(new_zip),
                "bytes": new_zip.stat().st_size,
                "sha256": sha256_file(new_zip),
                "members": names,
                "archive_root": root,
            },
            "installer_version": stamp,
            "installer_rebuilt": before.get("Install.exe") != after.get("Install.exe"),
            "documents_refreshed": {name: before.get(name) != after.get(name) for name in before},
        }
    )
    return stage.evidence


def stage_cleanup(ctx: Context, stage: Stage) -> dict[str, Any]:
    """Delete the working copy unless the operator asked to keep it."""
    marker = ctx.work / WORK_MARKER
    if ctx.options.keep:
        stage.evidence.update({"kept": True, "why": "--keep", "work_dir": str(ctx.work)})
        return stage.evidence
    if not marker.is_file():
        stage.evidence.update(
            {
                "kept": True,
                "why": f"{ctx.work} is not marked as a harness work directory; refusing to delete it",
                "work_dir": str(ctx.work),
            }
        )
        return stage.evidence
    try:
        shutil.rmtree(ctx.work)
    except OSError as error:
        raise StageFailure(f"the working directory could not be removed: {error}") from error
    stage.evidence.update(
        {"kept": False, "work_dir": str(ctx.work), "exists_after": ctx.work.exists()}
    )
    return stage.evidence


# --------------------------------------------------------------------------- #
# the journey


@dataclass
class Options:
    source: str
    zip_path: Path | None
    ref: str
    work: Path
    keep: bool
    no_ltspice: bool
    build_zip: bool
    until: str | None
    report: Path
    markdown: Path


@dataclass
class Context:
    repo: Path
    work: Path
    options: Options
    archive_dir: Path
    extract_dir: Path
    inputs_dir: Path
    ltspice: dict[str, Any]
    outside_baseline: dict[str, Any]
    expected_version: str = ""
    expected_version_source: str = ""
    zip_path: Path | None = None
    install_root: Path | None = None
    installer_probe: Path | None = None
    installer_document: Path | None = None
    expected_installer_sha256: str = ""
    outside_before_install: dict[str, Any] = field(default_factory=dict)
    model_out: Path | None = None
    model_inputs: dict[str, Any] = field(default_factory=dict)


def run_journey(options: Options) -> dict[str, Any]:
    """Run every stage in order and return the report, whatever happened."""
    repo = REPO_ROOT
    work = options.work
    work.mkdir(parents=True, exist_ok=True)
    (work / WORK_MARKER).write_text("release verification work directory\n", encoding="utf-8")
    ctx = Context(
        repo=repo,
        work=work,
        options=options,
        archive_dir=work / "archive",
        extract_dir=work / "extract",
        inputs_dir=work / "inputs",
        ltspice=detect_ltspice(),
        outside_baseline=outside_snapshot(work, None),
    )
    ctx.archive_dir.mkdir(parents=True, exist_ok=True)
    ctx.inputs_dir.mkdir(parents=True, exist_ok=True)

    implementations = {
        "archive": stage_archive,
        "extract": stage_extract,
        "install": stage_install,
        "environment": stage_environment,
        "ltspice-path": stage_ltspice_path,
        "model-build": stage_model_build,
        "model-test": stage_model_test,
        "model-open": stage_model_open,
        "zip": stage_zip,
        "cleanup": stage_cleanup,
    }
    stop_after = STAGES.index(options.until) if options.until else len(STAGES) - 1
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    failed_stage: str | None = None

    for index, name in enumerate(STAGES):
        stage = Stage(name)
        status = "PASS"
        reason: str | None = None
        expected = True
        stage_started = time.perf_counter()
        if index > stop_after:
            status, reason = "SKIP", f"not run: --until {options.until} stops after {options.until}"
        elif failed_stage is not None:
            status, reason = "SKIP", f"blocked by the failed {failed_stage} stage"
        elif name in ("ltspice-path", "model-test") and options.no_ltspice:
            status, reason = "SKIP", "not requested: --no-ltspice"
        elif name in ("ltspice-path", "model-build", "model-test") and not ctx.ltspice.get("path"):
            status, reason = "SKIP", "no LTspice executable was detected on this machine"
            expected = False
        elif name == "model-test" and ctx.model_out is None:
            status, reason = "SKIP", "no model was built to re-test"
            expected = failed_stage is None and not options.no_ltspice
        elif name == "model-open" and ctx.model_out is None:
            status, reason = "SKIP", "no model was built to open"
            expected = True
        else:
            try:
                implementations[name](ctx, stage)
            except SkipStage as skip:
                status, reason, expected = "SKIP", skip.reason, skip.expected
            except StageFailure as failure:
                status, reason = "FAIL", str(failure)
                failed_stage = name
            except Exception as error:  # a defect in the harness, still reported, never hidden
                status, reason = "FAIL", f"{type(error).__name__}: {error}"
                failed_stage = name
        if status == "FAIL" and failed_stage is None:
            failed_stage = name
        results.append(
            {
                "name": name,
                "status": status,
                "seconds": round(time.perf_counter() - stage_started, 3),
                "reason": reason,
                "expected_skip": expected,
                "commands": [command.as_dict() for command in stage.commands],
                "evidence": stage.evidence,
            }
        )

    failures = [row["name"] for row in results if row["status"] == "FAIL"]
    unexpected = [
        row["name"] for row in results if row["status"] == "SKIP" and not row["expected_skip"]
    ]
    skipped = [
        {"stage": row["name"], "reason": row["reason"], "expected": row["expected_skip"]}
        for row in results
        if row["status"] == "SKIP"
    ]
    fully_verified = all(row["status"] == "PASS" for row in results)
    if failures:
        verdict = "FAIL"
        meaning = (
            f"{len(failures)} stage(s) failed: {', '.join(failures)}; the journey is not verified"
        )
    elif unexpected:
        verdict = "INCOMPLETE"
        meaning = (
            "no stage failed, but stage(s) could not run here: "
            f"{', '.join(unexpected)}; the journey is not fully verified"
        )
    else:
        verdict = "PASS"
        meaning = (
            "every stage in this run passed"
            if fully_verified
            else "every stage in this run passed, but "
            f"{len(skipped)} stage(s) were skipped and are not verified"
        )
    report = {
        "tool": "verify_release_zip",
        "schema": 1,
        "started_utc": utc_now(),
        "seconds": round(time.perf_counter() - started, 3),
        "verdict": verdict,
        "verdict_meaning": meaning,
        "fully_verified": fully_verified,
        "failures": failures,
        "skipped_stages": skipped,
        "unverified_stages": [row["name"] for row in results if row["status"] != "PASS"],
        "exit_code_meaning": "0 = PASS, 1 = FAIL, 2 = INCOMPLETE",
        "options": {
            "source": options.source,
            "zip": str(options.zip_path) if options.zip_path else None,
            "ref": options.ref,
            "work": str(options.work),
            "keep": options.keep,
            "no_ltspice": options.no_ltspice,
            "build_zip": options.build_zip,
            "until": options.until,
        },
        "host": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "ltspice": ctx.ltspice,
            "repo": str(repo),
            "git_head": _git_head(repo),
        },
        "outside_root": outside_changes(ctx.outside_baseline, outside_snapshot(work, None)),
        "stages": results,
    }
    return report


def _git_head(repo: Path) -> str | None:
    """The checkout the run measured, so a report can be tied to a revision."""
    with contextlib.suppress(OSError):
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    return None


# --------------------------------------------------------------------------- #
# reporting


def stage_table(report: dict[str, Any]) -> list[dict[str, Any]]:
    """The compact rows the terminal table and the markdown share."""
    rows = []
    for stage in report["stages"]:
        evidence = stage["evidence"]
        highlight = ""
        for key in ("detail", "reason"):
            if stage.get(key):
                highlight = str(stage[key])
                break
        if stage["name"] == "archive":
            highlight = (
                f"zip {evidence.get('zip', {}).get('bytes', 0)} B; installer "
                f"{evidence.get('installer_version', {}).get('product_version', '?')}; "
                f"pyproject {evidence.get('pyproject_version', {}).get('version', '?')}"
            )
        elif stage["name"] == "install":
            highlight = f"root {evidence.get('root', '?')}"
        elif stage["name"] == "environment":
            highlight = (
                f"python {evidence.get('interpreter', '?')}; {evidence.get('package_version', '?')}"
            )
        elif stage["name"] == "ltspice-path":
            highlight = (
                f"{evidence.get('written', '?')} ({evidence.get('read_back_served_by', '?')})"
            )
        elif stage["name"] == "model-build" or stage["name"] == "model-test":
            highlight = f"status {evidence.get('status', '?')}; counts {evidence.get('counts', {})}"
        rows.append(
            {
                "stage": stage["name"],
                "status": stage["status"],
                "seconds": stage["seconds"],
                "what": highlight,
            }
        )
    return rows


def markdown_report(report: dict[str, Any]) -> str:
    """A human-readable rendering of the same report, stage by stage."""
    lines = [
        "# Release ZIP verification",
        "",
        f"**Verdict: {report['verdict']}** — {report['verdict_meaning']}",
        "",
        f"Fully verified (every stage ran): **{report['fully_verified']}**  ",
        f"Started: {report['started_utc']}  ",
        f"Total: {report['seconds']:.1f} s  ",
        f"Source: `{report['options']['source']}`  ",
        f"LTspice: `{report['host']['ltspice'].get('path') or 'not detected'}`  ",
        f"Exit code: 0 = PASS, 1 = FAIL, 2 = INCOMPLETE → this run: "
        f"{0 if report['verdict'] == 'PASS' else (1 if report['verdict'] == 'FAIL' else 2)}",
        "",
        "## Stages",
        "",
        "| stage | status | seconds | observed |",
        "|---|---|---|---|",
    ]
    for row in stage_table(report):
        what = row["what"].replace("|", "\\|").replace("\n", " ")[:160]
        lines.append(f"| {row['stage']} | {row['status']} | {row['seconds']:.1f} | {what} |")
    if report["skipped_stages"]:
        lines += ["", "## Skipped (not verified)", ""]
        for skip in report["skipped_stages"]:
            kind = "requested/capability" if skip["expected"] else "environment"
            lines.append(f"* `{skip['stage']}` ({kind}): {skip['reason']}")
    if report["failures"]:
        lines += ["", "## Failures", ""]
        for stage in report["stages"]:
            if stage["status"] == "FAIL":
                lines.append(f"* `{stage['name']}`: {stage['reason']}")
    for stage in report["stages"]:
        lines += [
            "",
            f"## {stage['name']} — {stage['status']} ({stage['seconds']:.1f} s)",
            "",
        ]
        if stage.get("reason"):
            lines += [f"{stage['reason']}", ""]
        for command in stage["commands"]:
            lines += [
                f"* `{command['command']}`",
                f"  * exit {command['exit_code']} in {command['seconds']:.1f} s"
                + (" (timed out)" if command["timed_out"] else ""),
            ]
            if command["stdout_tail"].strip():
                lines += ["  * stdout tail:", "    ```", _indent(command["stdout_tail"]), "    ```"]
            if command["stderr_tail"].strip():
                lines += ["  * stderr tail:", "    ```", _indent(command["stderr_tail"]), "    ```"]
        if stage["evidence"]:
            lines += ["", "Evidence:", "", "```json", _truncate_json(stage["evidence"]), "```"]
    return "\n".join(lines) + "\n"


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def _truncate_json(payload: Any, limit: int = 6000) -> str:
    text = json.dumps(payload, indent=2, default=str)
    return (
        text
        if len(text) <= limit
        else text[:limit] + "\n… truncated in markdown; see the JSON report"
    )


def write_reports(report: dict[str, Any], options: Options) -> None:
    """Write both reports even when the work directory was cleaned up."""
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    options.markdown.parent.mkdir(parents=True, exist_ok=True)
    options.markdown.write_text(markdown_report(report), encoding="utf-8")


def print_table(report: dict[str, Any]) -> None:
    """The stage table on stdout, so a terminal run is pastable evidence."""
    width = max(len(row["stage"]) for row in stage_table(report))
    print(f"verdict: {report['verdict']}  ({report['verdict_meaning']})")
    print(f"{'stage'.ljust(width)}  status  seconds  observed")
    for row in stage_table(report):
        print(
            f"{row['stage'].ljust(width)}  {row['status']:<6}  {row['seconds']:>7.1f}  "
            f"{row['what'][:100]}"
        )
    if report["skipped_stages"]:
        print("skipped:")
        for skip in report["skipped_stages"]:
            print(f"  - {skip['stage']}: {skip['reason']}")


# --------------------------------------------------------------------------- #
# entry point


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=("releases", "github", "zip"),
        default="releases",
        help="where the ZIP comes from (default: the newest releases/*.zip)",
    )
    parser.add_argument("--zip", type=Path, default=None, help="the ZIP for --source zip")
    parser.add_argument(
        "--ref", default="main", help="the branch for --source github (default: main)"
    )
    parser.add_argument(
        "--work",
        type=Path,
        default=None,
        help="working directory (default: build/release-verify/<timestamp>)",
    )
    parser.add_argument("--keep", action="store_true", help="keep the working copy")
    parser.add_argument(
        "--no-ltspice",
        action="store_true",
        help="do not run the simulator-dependent stages; they are reported as skipped",
    )
    parser.add_argument(
        "--build-zip",
        action="store_true",
        help="run installer/build.ps1 to generate a new release ZIP (long, network-using)",
    )
    parser.add_argument(
        "--until",
        choices=STAGES,
        default=None,
        help="stop after this stage; later stages are reported as not run",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("build/release-verify.json"),
        help="machine-readable report path",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=Path("build/release-verify.md"),
        help="human-readable report path",
    )
    return parser


def resolve_options(args: argparse.Namespace, repo: Path) -> Options:
    if args.source == "zip" and args.zip is None:
        raise SystemExit("--source zip requires --zip PATH")
    work = args.work
    if work is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        work = repo / "build" / "release-verify" / stamp
    work = Path(work) if Path(work).is_absolute() else repo / work
    return Options(
        source=args.source,
        zip_path=Path(args.zip).resolve() if args.zip else None,
        ref=args.ref,
        work=work,
        keep=bool(args.keep),
        no_ltspice=bool(args.no_ltspice),
        build_zip=bool(args.build_zip),
        until=args.until,
        report=Path(args.report) if Path(args.report).is_absolute() else repo / args.report,
        markdown=Path(args.markdown) if Path(args.markdown).is_absolute() else repo / args.markdown,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    options = resolve_options(args, REPO_ROOT)
    report = run_journey(options)
    write_reports(report, options)
    print_table(report)
    print(f"report: {options.report}")
    print(f"markdown: {options.markdown}")
    if report["verdict"] == "PASS":
        return 0
    return 1 if report["verdict"] == "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
