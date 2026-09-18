"""Subprocess guard (D11).

Every child process BoardModeler starts goes through :func:`run_guarded`: a
list argv (never a shell string), ``shell=False``, an executable allowlist, and
a process-tree kill when the timeout expires. The allowlist only contains what
the product actually needs — the simulator, the interpreter running the tests,
``taskkill`` for the Windows tree kill — plus anything the caller adds
explicitly.

:func:`resolve_include_path` keeps ``.include``/``.lib`` targets inside the
project roots plus the configured LTspice library directory, so a deck can never
pull in a file from somewhere else.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from boardmodeler.security.paths import PathGuardError

__all__ = [
    "ALLOWED_EXECUTABLES",
    "GuardedProcess",
    "check_argv",
    "resolve_include_path",
    "run_guarded",
]

_BASE_EXECUTABLES = ("ltspice.exe", "xvii64.exe", "scad3.exe", "taskkill")


def _interpreter_basename() -> str | None:
    if not sys.executable:
        return None
    return Path(sys.executable).name.lower()


def _allowed_executables() -> frozenset[str]:
    names = set(_BASE_EXECUTABLES)
    interpreter = _interpreter_basename()
    if interpreter:
        names.add(interpreter)
    return frozenset(names)


ALLOWED_EXECUTABLES: frozenset[str] = _allowed_executables()

_SHELL_METACHARACTERS = frozenset("&|;<>^%!$`\n\r\"'")


def check_argv(argv: Sequence[str], *, extra_allowed: Iterable[str] = ()) -> Path:
    """Validate a list argv and return the resolved executable path.

    Rejects a raw string argv (the classic shell-injection shape), non-string or
    NUL-bearing arguments, shell metacharacters in ``argv[0]``, and any
    executable whose basename is not on the allowlist. ``extra_allowed`` accepts
    additional basenames for a specific caller.
    """
    if isinstance(argv, (str, bytes)):
        raise PathGuardError(
            f"argv must be a sequence of arguments, got {type(argv).__name__}: {argv!r}"
        )
    args = list(argv)
    if not args:
        raise PathGuardError("argv is empty")
    for index, argument in enumerate(args):
        if not isinstance(argument, str):
            raise PathGuardError(
                f"argv[{index}] is {type(argument).__name__}; every argument must be str"
            )
        if "\x00" in argument:
            raise PathGuardError(f"argv[{index}] contains a NUL byte")

    executable = args[0]
    metacharacters = sorted(set(executable) & _SHELL_METACHARACTERS)
    if metacharacters:
        raise PathGuardError(
            f"argv[0]={executable!r} contains shell metacharacters {metacharacters}"
        )
    allowed = {name.lower() for name in ALLOWED_EXECUTABLES}
    allowed |= {name.lower() for name in extra_allowed}
    basename = Path(executable).name.lower()
    if basename not in allowed:
        raise PathGuardError(
            f"executable {basename!r} is not on the allowlist; allowed: {sorted(allowed)}"
        )
    return Path(executable).expanduser().resolve()


@dataclass(frozen=True)
class GuardedProcess:
    """Result of one guarded child process."""

    returncode: int
    stdout: str
    stderr: str
    wall_s: float
    timed_out: bool


def _kill_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            shell=False,
            capture_output=True,
            check=False,
        )
    else:
        with contextlib.suppress(OSError, AttributeError):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    if process.poll() is None:
        with contextlib.suppress(OSError):
            process.kill()


def run_guarded(
    argv: Sequence[str],
    *,
    cwd: str | Path,
    timeout_s: float,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
) -> GuardedProcess:
    """Run ``argv`` with ``shell=False`` in ``cwd`` and return its output.

    On timeout the whole process tree is killed (``taskkill /T`` on Windows,
    ``killpg`` elsewhere) and ``timed_out`` is True with whatever output was
    produced. ``env`` replaces the child environment when given; ``input_text``
    is fed to stdin. The child never shares the caller's stdin.
    """
    executable = check_argv(argv)
    args = [str(executable), *argv[1:]]
    working_dir = Path(cwd).expanduser().resolve()
    if not working_dir.is_dir():
        raise PathGuardError(
            f"working directory {working_dir} does not exist or is not a directory"
        )
    if timeout_s <= 0:
        raise ValueError(f"timeout_s must be > 0, got {timeout_s}")

    start = time.monotonic()
    process = subprocess.Popen(
        args,
        cwd=str(working_dir),
        shell=False,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=dict(env) if env is not None else None,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                process.kill()
            stdout, stderr = process.communicate()
    return GuardedProcess(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=stdout or "",
        stderr=stderr or "",
        wall_s=time.monotonic() - start,
        timed_out=timed_out,
    )


def resolve_include_path(
    name: str, roots: Sequence[Path], *, ltspice_lib_dir: Path | None = None
) -> Path:
    """Resolve a ``.include``/``.lib`` target inside the allowed roots.

    Raises :class:`PathGuardError` when the target resolves outside every
    allowed root (the project roots plus ``ltspice_lib_dir`` when configured).
    A candidate that exists wins over one that does not; a target that resolves
    inside a root but does not exist yet is still returned so the caller can
    report the missing file together with its resolved path.
    """
    if not isinstance(name, str) or not name.strip():
        raise PathGuardError(f"include name must be a non-empty string, got {name!r}")
    allowed: list[Path] = [Path(root).expanduser().resolve() for root in roots]
    if ltspice_lib_dir is not None:
        allowed.append(Path(ltspice_lib_dir).expanduser().resolve())
    if not allowed:
        raise PathGuardError(f"no allowed roots to resolve {name!r} against")

    candidate = Path(name)
    if candidate.is_absolute():
        resolved = candidate.expanduser().resolve()
        if not any(resolved.is_relative_to(root) for root in allowed):
            raise PathGuardError(
                f"{name!r} resolves to {resolved}, outside every allowed root {allowed}"
            )
        return resolved

    inside: list[Path] = []
    for root in allowed:
        resolved = (root / candidate).resolve()
        if resolved.is_relative_to(root):
            inside.append(resolved)
    if not inside:
        raise PathGuardError(
            f"{name!r} resolves outside every allowed root; tried "
            f"{[str(root / candidate) for root in allowed]}"
        )
    for resolved in inside:
        if resolved.exists():
            return resolved
    return inside[0]
