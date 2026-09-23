"""The one place BoardModeler may create a child process (D11, wave 2).

The rule this module exists to enforce:

* **One policy, and nobody has to work around it.** ``run()`` is the buffered
  runner: every module that can accept "give me the finished output" hands it a
  ``CommandSpec`` and the arguments to run with. A call site that cannot use a
  buffered runner -- one that streams progress, or that watches for a completion
  marker and has to kill a child which outlives it -- calls :func:`validate()`
  first and spawns the returned executable, tail, cwd and environment itself.
  There is one implementation of every check (``run()`` is :func:`validate()` plus
  the bounded capture), so such a caller is *inside* the policy rather than beside
  it. It is still a reviewed exception, because owning the loop means owning the
  kill, the deadline and the output cap too: each one is named, with its reason, in
  the spawn-site allowlist in ``tests/security/test_execution.py``.
* **The process-creation calls themselves are countable.** ``Popen`` appears once
  in this module, and the modules that keep their own loop are exactly the ones the
  allowlist names -- the meta-test fails on any other module that starts anything.
* **Executables are absolute and pinned.** The path lives in a spec written in
  source, is resolved, and its basename must be on ``subprocess_guard``'s
  allowlist or be named explicitly by the spec. Data never chooses a program.
* **No shell, ever.** argv is a list. A string argv, a NUL, or a shell
  metacharacter in a value is refused before a process exists.
* **No inherited environment.** The child's environment is built from scratch:
  only the variables the spec allowlists (taken from the parent), plus values the
  program supplies (``extra_env``), plus values the caller supplies for
  allowlisted names.
* **A timeout is always present.** ``CommandSpec.timeout_s`` has no default and
  must be a positive, finite number; on expiry the whole process tree is killed.
* **Output is bounded.** Reading stops at ``max_output_bytes`` per stream, the
  child is stopped, and the result says ``truncated=True``. A bounded run can
  never be mistaken for a clean one: ``CompletedCommand.ok`` is False.
* **No data ever reaches a flag position.** A spec's tail is a template: flags
  are literals written in source, and the only positions data may fill are marked
  ``ANY_VALUE`` / ``PATH_VALUE``, where a value can never be read as a flag.

This module *extends* :mod:`boardmodeler.security.subprocess_guard` instead of
reimplementing it: the guard owns the executable allowlist, the argv checks and
the process-tree kill, and everything here calls into it. The one thing the guard
cannot do is bound output -- ``run_guarded`` buffers whatever the child writes
through ``communicate()`` -- so ``run()`` starts its own child with the guard's
exact flags and its own capped readers, and stops the child through the guard's
tree kill. Wave 3 should move that bounded reader into ``run_guarded`` so there is
one spawn path again.
"""

from __future__ import annotations

import contextlib
import math
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn

from boardmodeler.security import subprocess_guard
from boardmodeler.security.paths import PathGuardError, resolve_within

__all__ = [
    "ANY_VALUE",
    "PATH_VALUE",
    "WINDOWS_BASE_ENV",
    "CommandCall",
    "CommandRefused",
    "CommandSpec",
    "CompletedCommand",
    "ResolvedCommand",
    "bounded",
    "command_line",
    "run",
    "system_executable",
    "validate",
]


# --------------------------------------------------------------------------- #
# template markers


#: A tail position that accepts one opaque value: shape-checked, never resolved.
#: Use it for a value that is not a path (a mode, a measurement name, a number).
ANY_VALUE = "{value}"

#: A tail position that accepts one path value: shape-checked and pinned inside the
#: run root before it reaches argv. Use it for every path that came from data.
PATH_VALUE = "{path}"

#: Environment a Windows child normally needs before it can start at all: the DLL
#: loader resolves through ``SystemRoot``/``windir`` and scratch files go to
#: ``TEMP``/``TMP``. Nothing here is implicit -- a spec must name what it wants.
#: ``PATH`` is deliberately absent: a child that needs it must say so, because the
#: search path also decides which DLLs an executable can load.
WINDOWS_BASE_ENV: tuple[str, ...] = ("SystemRoot", "windir", "TEMP", "TMP")


# --------------------------------------------------------------------------- #
# shapes and limits


#: Characters refused in a *value*. With ``shell=False`` the OS never interprets
#: these, so this is defence in depth rather than the primary control -- but a
#: child tool may re-split its own command line, ``%VAR%``/``!VAR!``/backticks/
#: ``$(...)`` are expansion in the shells that do, and ``,`` is how a measure or
#: CSV-shaped argument becomes two arguments in the tools this product drives.
#:
#: Deliberately *not* refused: ``[``, ``]``, ``{``, ``}``, ``~``, ``#`` and the
#: space. Datasheet downloads are full of bracketed names
#: (``datasheet[1].pdf``) and "Program Files" needs its space; refusing those
#: would push call sites away from this module for no security gain.
_VALUE_REFUSED = frozenset("&|;<>^%!\"'`$()*?,\x00\r\n")

#: A single value longer than this cannot be a path we would accept as a path
#: (MAX_PATH is 260 and a normalised long path is far below this), and a longer
#: token is a shape we do not want to forward to anything.
_MAX_VALUE_CHARS = 4096

#: Bound on how many values one call may carry, so a call cannot build an
#: unbounded argument list. The Windows command line stops at 32767 characters
#: anyway; this refuses earlier and with a clearer message.
_MAX_VALUES = 64

#: A literal template token: a flag, a subcommand, or the ``--`` separator. No
#: path separator, no drive letter, no space, no glob character, no quote -- only
#: the tokens a call site wrote in its own source. Windows-style ``/switch`` tokens
#: are accepted too: ``taskkill`` (the tree kill) spells its own as ``/PID``, ``/T``,
#: ``/F``, and refusing them would push the one kill this product performs outside the
#: policy. The rule that matters is unchanged -- a *value* can never land in a flag
#: position, because a literal position accepts only the exact token the spec author
#: wrote, and ``/etc/passwd`` is still refused here (a slash *inside* a token is not).
_FLAG_TOKEN = re.compile(
    r"\A(?:--|[-+]{0,2}[A-Za-z0-9_][A-Za-z0-9_.+=\-]{0,63}"
    r"|/[A-Za-z0-9_][A-Za-z0-9_.+=\-]{0,63})\Z"
)

#: Environment variable names, so a name can never smuggle an ``=`` into the
#: child's environment block.
_ENV_NAME = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")

#: Output is read in chunks of this size while waiting for the cap or the timeout.
_READ_CHUNK_BYTES = 1 << 16

#: How often the run loop re-checks for exit, a flooded stream, or the deadline.
_POLL_S = 0.02

#: How long to wait for a child to die after the tree kill, and how long to wait
#: for its readers to see EOF afterwards. Both are bounded so a stuck child cannot
#: hang the caller; a reader that never finished marks the result as incomplete.
_KILL_GRACE_S = 5.0
_DRAIN_GRACE_S = 5.0


# --------------------------------------------------------------------------- #
# result and refusals


class CommandRefused(Exception):
    """A command was refused, with a stable ``code`` and a secret-free ``detail``.

    Codes, and what each one means:

    ``shell_string``
        A sequence of argument tokens was given as a single string -- the classic
        shape that ends up in a shell.
    ``executable_not_absolute``
        The spec's executable is relative, so PATH or the current directory would
        decide what actually ran.
    ``executable_not_allowed``
        The executable's basename is not on ``subprocess_guard``'s allowlist and
        the spec does not permit it explicitly.
    ``executable_not_found``
        The pinned executable does not exist (a stale install path), so the run is
        refused here with a stable code instead of failing inside the spawn.
    ``path_outside_scope``
        A path (the executable, a value, or the working directory) resolved
        outside the scope the caller allowed; UNC paths are refused here too.
    ``argument_not_allowed``
        An argument was malformed or in the wrong position: a NUL, a metacharacter
        in a value, a value longer than the limit, a value that would be read as a
        flag, or a token that does not match the spec's tail template.
    ``timeout_missing``
        No usable timeout was supplied: absent, non-numeric, not finite, or <= 0.
        A command without a timeout cannot be run at all.
    ``output_too_large``
        The spec does not declare a positive output cap. (A cap that is *exceeded
        at runtime* is not a refusal: the child is stopped and the result carries
        ``truncated=True``, which ``CompletedCommand.ok`` reports as not clean.)
    ``cwd_missing``
        The working directory does not exist or is not a directory.
    ``env_not_allowed``
        An environment variable was requested that the spec does not allowlist,
        that the spec pins itself, or that is malformed.

    ``detail`` is built from token positions, labels and counts. It never contains
    an environment value, so a secret passed through ``extra_env`` cannot reach a
    log through a refusal.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CompletedCommand:
    """The outcome of one sanctioned command.

    ``spawn_error`` names the OS failure that stopped the child from starting at all
    (a path that exists but is not a runnable image, a permission the OS refuses).
    Every other field then describes nothing -- ``returncode`` is -1 and both streams
    are empty -- and ``ok`` is False, so "could not start" is an outcome like any other
    rather than an exception escaping a policy entry point. It is ``None`` for every
    command that really ran.
    """

    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool
    truncated: bool
    spawn_error: str | None = None

    @property
    def ok(self) -> bool:
        """True only for a complete, untimed-out, zero-exit run.

        Callers must branch on this rather than on ``returncode``: a killed child
        can otherwise look like a successful one that simply said little.
        """
        return self.returncode == 0 and not self.timed_out and not self.truncated


@dataclass(frozen=True, kw_only=True)
class CommandSpec:
    """A sanctioned command: what may run, how long, and with what.

    ``argv_tail`` is the template for the arguments *after* the executable, one
    entry per argument. Each entry is either one of the markers ``ANY_VALUE`` /
    ``PATH_VALUE``, or a literal token the spec author wrote in source. The tail
    must match the template exactly in length, so a call site cannot add an
    argument the spec did not sanction.

    ``extra_env`` is the one field that may hold a secret (an API key a child needs
    to start). It is stored read-only and omitted from ``repr`` so it cannot leak
    through a traceback or a log line; nothing in this module ever renders its
    values. ``env_allowlist`` names the variables the child may inherit from the
    parent's environment -- nothing is inherited implicitly.
    """

    name: str
    executable: Path
    argv_tail: tuple[str, ...]
    timeout_s: float
    max_output_bytes: int
    env_allowlist: tuple[str, ...] = ()
    extra_env: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)
    inside_root: bool = True
    allowed_dirs: tuple[Path, ...] = ()
    extra_allowed_executables: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.extra_env, MappingProxyType):
            object.__setattr__(self, "extra_env", MappingProxyType(dict(self.extra_env)))


@dataclass(frozen=True, kw_only=True)
class CommandCall:
    """A spec paired with the exact tail it is to be run with.

    ``run()`` accepts either a bare spec (with ``argv`` passed separately) or one
    of these, so a call site can build a command once and hand it through a
    pipeline without the two parts ever drifting apart.
    """

    spec: CommandSpec
    argv: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.argv, (str, bytes)):
            raise CommandRefused(
                "shell_string",
                f"{_label(self.spec)}: argv is a string, not a list of argument tokens",
            )
        object.__setattr__(self, "argv", tuple(self.argv))


@dataclass(frozen=True, kw_only=True)
class ResolvedCommand:
    """Everything :func:`validate` decided about one command, ready to be spawned.

    This is what a call site gets when it keeps its own process loop (streaming
    progress, or a watchdog that watches for a marker and kills a child which
    outlives it). Spawning exactly ``[str(executable), *tail]`` with ``cwd`` as the
    working directory and ``env`` as the child's whole environment is then
    equivalent to what :func:`run` would have started: every field was produced by
    the same checks, so nothing has to be re-checked and nothing may be re-spelled.

    ``timeout_s`` and ``max_output_bytes`` are the spec's numbers, carried along so
    the loop owner enforces the same deadline and cap the policy would have
    enforced. A caller that keeps the loop must use these rather than inventing its
    own: an unbounded or undeadlined child is exactly the failure this module
    exists to prevent.
    """

    spec: CommandSpec
    executable: Path
    tail: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    timeout_s: float
    max_output_bytes: int

    @property
    def command(self) -> tuple[str, ...]:
        """The exact argv to spawn: the resolved executable, then the checked tail."""
        return (str(self.executable), *self.tail)


def system_executable(name: str) -> Path:
    """The absolute path of a Windows system tool whose bare name the guard allows.

    ``taskkill`` -- the tree kill's tool -- is spawned by two modules, and the policy
    refuses a relative executable because PATH would then choose the program. Windows
    documents this one under this machine's system folder, so that copy is preferred
    and PATH is only a fallback for a non-standard layout. The name is a literal
    written at the call site, never data, and the basename is still checked against
    the allowlist by whichever spec spawns it. A tool that cannot be found is returned
    as the canonical (missing) path, so the refusal names it as
    ``executable_not_found`` instead of ``OSError`` escaping from a spawn.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"system_executable() needs a tool name, got {name!r}")
    tool = Path(name).name
    if os.name == "nt" and not tool.lower().endswith(".exe"):
        # The OS appends the extension when it creates a process, not when a path is
        # checked, so the candidate this function verifies has to carry it.
        tool += ".exe"
    # Windows environment lookups are case-insensitive (verified on this machine:
    # the key is ``SYSTEMROOT`` and ``SystemRoot`` reads it), so one spelling is enough.
    canonical = Path(os.environ.get("SYSTEMROOT") or r"C:\Windows") / "System32" / tool
    if canonical.is_file():
        return canonical
    found = shutil.which(tool)
    return Path(found) if found else canonical


# --------------------------------------------------------------------------- #
# building and validating templates and values


def command_line(names: Sequence[str]) -> list[str]:
    """Return a literal argument template: flags and subcommands, nothing else.

    A call site writes only the tokens *it* wrote in its own source -- a mode, a
    flag, a subcommand. Anything that could have come from an agent, a datasheet
    or a filename (a path, a drive letter, a glob, a space, a quote) is refused
    here, so the one place such data can go is a value slot in the spec's tail,
    through :func:`bounded`. Values are deliberately *not* parameters of this
    function: there is no way to interpolate one into a flag position.
    """
    if isinstance(names, (str, bytes)):
        _refuse("shell_string", "command_line() takes a sequence of tokens, not one string")
    tokens = _tokens(names, "command_line() argument")
    for index, token in enumerate(tokens):
        if not isinstance(token, str) or not _FLAG_TOKEN.match(token):
            _refuse(
                "argument_not_allowed",
                f"command_line() token {index} is not a literal flag or subcommand: {_clip(token)}",
            )
    return tokens


def bounded(values: Sequence[str], limit: str | Path) -> list[str]:
    """Validate value tokens (paths) and pin them inside ``limit``.

    Refuses a bare string, a NUL, a carriage return or line feed, any shell
    metacharacter (see ``_VALUE_REFUSED``), a token that would be read as a flag,
    a UNC path, and anything that resolves outside ``limit``. What survives is
    returned as resolved absolute paths, so the value the child receives is the
    one this function checked -- not a relative spelling that could later mean
    something else.
    """
    if isinstance(values, (str, bytes)):
        _refuse("shell_string", "bounded() takes a sequence of value tokens, not one string")
    items = _tokens(values, "bounded() value")
    if len(items) > _MAX_VALUES:
        _refuse("argument_not_allowed", f"{len(items)} values exceed the limit of {_MAX_VALUES}")
    root = Path(limit).expanduser().resolve()
    resolved: list[str] = []
    for index, value in enumerate(items):
        where = f"value[{index}]"
        _check_value_shape(value, where)
        if value.startswith(("\\\\", "//")):
            _refuse("path_outside_scope", f"{where} is a UNC path; network locations are refused")
        try:
            inside = resolve_within(root, value)
        except PathGuardError as exc:
            raise CommandRefused(
                "path_outside_scope", f"{where} is not inside the root: {exc}"
            ) from exc
        resolved.append(str(inside))
    return resolved


# --------------------------------------------------------------------------- #
# the one entry point


def run(
    spec_or_call: CommandSpec | CommandCall,
    *,
    argv: Sequence[str] = (),
    cwd: str | Path,
    root: str | Path,
    env: Mapping[str, str] | None = None,
    env_sources: Sequence[str] = (),
    extra_roots: Sequence[str | Path] = (),
    path_alias: Callable[[Path], str] | None = None,
) -> CompletedCommand:
    """Run exactly one sanctioned command and return its bounded result.

    ``argv`` is the tail *after* the executable (the spec pins argv[0]); a
    ``CommandCall`` supplies it instead. ``cwd`` is resolved against ``root`` when
    relative and must exist; ``env`` values are extra values the caller computed
    for names the spec allowlists -- ``env_sources`` labels where they came from so
    a refusal can name the source instead of the value.

    This is :func:`validate` plus the spawn and the bounded capture: the checks
    themselves live there, so a call site that has to keep its own process loop gets
    the identical policy through that one function instead of a second copy of it.

    Every refusal happens before a process exists: a string argv, an executable
    that is not absolute / not allowlisted / outside the spec's directories, a tail
    that does not match the spec's template, a value that is malformed or resolves
    outside the permitted roots, a working directory that is missing or out of
    scope, a timeout or output cap that is not positive, and an environment
    variable the spec does not allow. The child is then started with a list argv,
    ``shell=False``, a fresh environment, and a timeout and output cap that are
    always present. A spawn the *OS* still refuses (a path that exists but is not a
    runnable image) is not an exception leaving this function either: it comes back as
    a ``CompletedCommand`` whose ``spawn_error`` names the failure and whose ``ok`` is
    False, so "could not start" is data like every other outcome here.
    """
    resolved = validate(
        spec_or_call,
        argv=argv,
        cwd=cwd,
        root=root,
        env=env,
        env_sources=env_sources,
        extra_roots=extra_roots,
        path_alias=path_alias,
    )
    return _execute(resolved, _label(resolved.spec))


def validate(
    spec_or_call: CommandSpec | CommandCall,
    *,
    argv: Sequence[str] = (),
    cwd: str | Path,
    root: str | Path,
    env: Mapping[str, str] | None = None,
    env_sources: Sequence[str] = (),
    extra_roots: Sequence[str | Path] = (),
    path_alias: Callable[[Path], str] | None = None,
) -> ResolvedCommand:
    """Check one command completely and return exactly what may be spawned.

    Same refusals, same order, same code as :func:`run` -- ``run`` calls this and
    then starts the child. It exists so a call site that cannot use a buffered
    runner is not pushed *outside* the policy: the simulator launch streams into a
    watchdog that watches for the simulator's own completion marker and kills a child
    which outlives it, and the UI worker's stdout is a JSON-lines protocol that has to
    be parsed as it arrives. Both keep their own loop and both call this first.

    ``extra_roots`` names directories, besides ``root``, in which a value or the
    working directory may legitimately live -- a user's chosen project folder, a
    private scratch directory. Naming them keeps the containment check on (a
    data-supplied path still has to land somewhere the call site named explicitly)
    instead of switching it off with ``inside_root=False``.

    ``path_alias`` re-spells a resolved path through a callable the call site owns;
    the simulator needs a Windows short-path alias for a deck whose path exceeds the
    legacy MAX_PATH. The policy refuses an alias that resolves to anything other than
    the path it was handed, so an alias can change the spelling but never the target
    -- and a failure inside the callable itself (its own ``OSError``) propagates
    untouched, which is how the long-path refusal keeps working.

    A caller that spawns the result owns the loop, and therefore owns the kill, the
    deadline and the output cap as well; that is why such a module still has to be
    named in the spawn-site allowlist in ``tests/security/test_execution.py``.
    """
    spec, tail = _call_parts(spec_or_call, argv)
    label = _label(spec)
    timeout_s = _timeout(spec, label)
    max_output_bytes = _output_cap(spec, label)
    roots = _roots(root, extra_roots, label)
    args = _tail(spec, tail, roots, label, path_alias)
    executable = _executable(spec, label, args)
    working_dir = _working_directory(cwd, roots, spec, label, path_alias)
    child_env = _child_environment(spec, env or {}, env_sources, label)
    return ResolvedCommand(
        spec=spec,
        executable=executable,
        tail=tuple(args),
        cwd=working_dir,
        env=child_env,
        timeout_s=timeout_s,
        max_output_bytes=max_output_bytes,
    )


# --------------------------------------------------------------------------- #
# specification checks (all before the spawn)


def _call_parts(
    spec_or_call: CommandSpec | CommandCall, argv: Sequence[str]
) -> tuple[CommandSpec, list[str]]:
    """Split a spec (or a spec plus its tail) from the arguments, or refuse."""
    if isinstance(spec_or_call, CommandCall):
        if argv:
            _refuse(
                "argument_not_allowed",
                f"{_label(spec_or_call.spec)}: a CommandCall carries its own argv; do not pass "
                "a second one",
            )
        return spec_or_call.spec, _tokens(spec_or_call.argv)
    if not isinstance(spec_or_call, CommandSpec):
        raise TypeError(
            f"run() needs a CommandSpec or CommandCall, got {type(spec_or_call).__name__}"
        )
    return spec_or_call, _tokens(argv)


def _timeout(spec: CommandSpec, label: str) -> float:
    """Return the spec's timeout, refusing anything that is not positive and finite."""
    value = spec.timeout_s
    seconds: float | None = None
    if not isinstance(value, bool) and isinstance(value, (int, float)):
        try:
            seconds = float(value)
        except OverflowError, ValueError:
            # An integer too large for a float is not a timeout either.
            seconds = None
    if seconds is None or not math.isfinite(seconds) or seconds <= 0:
        _refuse(
            "timeout_missing",
            f"{label}: a timeout is not optional and must be a positive number of seconds; "
            f"got {_clip(value, 40)}",
        )
    return seconds


def _output_cap(spec: CommandSpec, label: str) -> int:
    """Return the spec's output cap, refusing a command that would be unbounded."""
    value = spec.max_output_bytes
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _refuse(
            "output_too_large",
            f"{label}: max_output_bytes must be a positive byte count; got {_clip(value, 40)}",
        )
    return value


def _executable(spec: CommandSpec, label: str, args: Sequence[str]) -> Path:
    """Resolve the pinned executable, then let the guard check the exact argv.

    The guard gets the argv that will really be spawned -- executable first, then
    the validated tail -- so its checks apply to every element, not only argv[0],
    and the return value is the path the guard resolved.
    """
    raw = spec.executable
    if not isinstance(raw, (str, Path)):
        _refuse(
            "executable_not_absolute",
            f"{label}: executable must be a path, got {type(raw).__name__}",
        )
    candidate = Path(raw).expanduser()
    if str(candidate).startswith(("\\\\", "//")):
        # Checked before resolve(): resolving a UNC path can hit the network, and
        # nothing this product runs should ever come off a file share.
        _refuse(
            "path_outside_scope",
            f"{label}: executable {_clip(candidate)} is a UNC path; network locations are refused",
        )
    if not candidate.is_absolute():
        _refuse(
            "executable_not_absolute",
            f"{label}: executable {_clip(candidate)} is not absolute; PATH and the current "
            "directory must never decide what runs",
        )
    resolved = candidate.resolve()
    permitted = {name.lower() for name in subprocess_guard.ALLOWED_EXECUTABLES}
    permitted |= {name.lower() for name in spec.extra_allowed_executables}
    if resolved.name.lower() not in permitted:
        _refuse(
            "executable_not_allowed",
            f"{label}: executable {_clip(resolved.name)} is not on the allowlist "
            f"(allowed: {sorted(permitted)})",
        )
    if spec.allowed_dirs:
        allowed = [Path(item).expanduser().resolve() for item in spec.allowed_dirs]
        if not any(resolved.is_relative_to(item) for item in allowed):
            _refuse(
                "path_outside_scope",
                f"{label}: executable {_clip(resolved)} is outside every allowed directory",
            )
    if not resolved.is_file():
        # A stale install path becomes a refusal with a stable code instead of an
        # OSError escaping from the spawn. This is a diagnosis, not a TOCTOU
        # control: the path itself is pinned by the spec, never chosen by data.
        _refuse("executable_not_found", f"{label}: executable {_clip(resolved)} does not exist")
    try:
        return subprocess_guard.check_argv(
            [str(resolved), *args], extra_allowed=spec.extra_allowed_executables
        )
    except PathGuardError as exc:
        raise CommandRefused("argument_not_allowed", f"{label}: {_clip(exc)}") from exc


def _roots(root: str | Path, extra_roots: Sequence[str | Path], label: str) -> tuple[Path, ...]:
    """The directories a value or the cwd may resolve in: ``root`` first, then extras.

    ``extra_roots`` exists for a call site whose legitimate working directories are not
    inside the application's own tree (a project folder the user picked, a private
    scratch directory). It is a *named* list, so the containment check stays on: a
    path that came from data still has to land in a directory the call site wrote down.
    """
    primary = _one_root(root, f"{label}: root")
    extras: list[Path] = []
    for index, item in enumerate(_tokens(extra_roots, "extra_roots")):
        candidate = _one_root(item, f"{label}: extra_roots[{index}]")
        if candidate != primary and candidate not in extras:
            extras.append(candidate)
    return (primary, *extras)


def _one_root(value: object, where: str) -> Path:
    """Resolve one scope directory, refusing an empty, UNC or unusable one."""
    if isinstance(value, (str, bytes)) and not str(value).strip():
        _refuse("path_outside_scope", f"{where} is empty")
    if not isinstance(value, (str, Path)):
        _refuse("path_outside_scope", f"{where} is {type(value).__name__}, not a path")
    candidate = Path(value).expanduser()
    if str(candidate).startswith(("\\\\", "//")):
        # Checked before resolve(): resolving a UNC path can hit the network.
        _refuse("path_outside_scope", f"{where} {_clip(candidate)} is a UNC path")
    return candidate.resolve()


def _pin(value: str, roots: Sequence[Path]) -> str:
    """Resolve one value inside the first scope that accepts it, or refuse.

    The refusal reported when no root accepts the value is the first root's, so the
    message keeps naming the primary scope the caller passed rather than an
    ``extra_roots`` entry that was only ever a fallback.
    """
    first: CommandRefused | None = None
    for root in roots:
        try:
            return bounded([value], root)[0]
        except CommandRefused as exc:
            if first is None:
                first = exc
    assert first is not None  # _roots always returns at least the primary root
    raise first


def _alias_path(path_alias: Callable[[Path], str], resolved: Path, where: str, label: str) -> str:
    """Re-spell a checked path through the caller's alias function, or refuse.

    The alias exists because a legacy Windows tool cannot open a path at or beyond
    MAX_PATH and needs its 8.3 spelling. It may only ever change the *spelling*: the
    result must be a plain absolute path that resolves to exactly the path the policy
    just checked, so a buggy or hostile alias cannot redirect the child at a
    different file. The callable's own failures propagate (the simulator's
    long-path ``OSError`` is part of its documented contract).
    """
    alias = path_alias(resolved)
    if not isinstance(alias, str):
        _refuse(
            "argument_not_allowed",
            f"{label}: {where} alias is {type(alias).__name__}; an alias must be a path string",
        )
    _check_value_shape(alias, f"{where} alias")
    candidate = Path(alias).expanduser()
    if not candidate.is_absolute():
        _refuse(
            "argument_not_allowed",
            f"{label}: {where} alias {_clip(alias)} is not absolute; an alias may not change "
            "which directory a relative path means",
        )
    if candidate.resolve() != resolved:
        _refuse(
            "path_outside_scope",
            f"{label}: {where} alias {_clip(alias)} does not name the checked path "
            f"{_clip(resolved)}; an alias may only re-spell it",
        )
    return alias


def _tail(
    spec: CommandSpec,
    tail: Sequence[str],
    roots: Sequence[Path],
    label: str,
    path_alias: Callable[[Path], str] | None = None,
) -> list[str]:
    """Match the tail against the spec's template, resolving values, or refuse."""
    rules = spec.argv_tail
    if isinstance(rules, (str, bytes)):
        _refuse(
            "argument_not_allowed",
            f"{label}: argv_tail is a string; write the tail as a sequence of tokens",
        )
    rules = tuple(rules)
    if len(tail) != len(rules):
        _refuse(
            "argument_not_allowed",
            f"{label}: the spec expects {len(rules)} argument(s) after the executable, got "
            f"{len(tail)}",
        )
    if tail and tail[0] in {str(spec.executable), Path(spec.executable).name}:
        _refuse(
            "argument_not_allowed",
            f"{label}: argv starts with the executable; argv holds only the arguments after it",
        )
    checked: list[str] = []
    for index, (rule, token) in enumerate(zip(rules, tail, strict=True)):
        where = f"argv[{index}]"
        if rule == PATH_VALUE:
            _check_value_shape(token, where)
            pinned = _pin(token, roots)
            checked.append(
                pinned
                if path_alias is None
                else _alias_path(path_alias, Path(pinned), where, label)
            )
        elif rule == ANY_VALUE:
            _check_value_shape(token, where)
            checked.append(token)
        else:
            if not isinstance(rule, str) or not _FLAG_TOKEN.match(rule):
                _refuse(
                    "argument_not_allowed",
                    f"{label}: tail rule {index} is not a literal flag; write flags as literals "
                    f"and values as {ANY_VALUE} or {PATH_VALUE}",
                )
            if token != rule:
                _refuse(
                    "argument_not_allowed",
                    f"{where} must be the literal {_clip(rule)!r}, got {_clip(token)!r}",
                )
            checked.append(token)
    return checked


def _working_directory(
    cwd: str | Path,
    roots: Sequence[Path],
    spec: CommandSpec,
    label: str,
    path_alias: Callable[[Path], str] | None = None,
) -> Path:
    """Resolve the working directory, refusing a missing or out-of-scope one.

    A relative ``cwd`` is resolved against the primary root, never against the
    process's current directory: where the application happens to be standing must
    not change what a spec's directory means. ``inside_root`` compares against every
    permitted root, so naming an extra one does not switch the check off.
    """
    if isinstance(cwd, (str, bytes)) and not str(cwd).strip():
        _refuse("cwd_missing", f"{label}: cwd is empty")
    candidate = Path(cwd).expanduser()
    if str(candidate).startswith(("\\\\", "//")):
        # Checked before resolve(): resolving a UNC path can hit the network.
        _refuse(
            "path_outside_scope",
            f"{label}: cwd {_clip(candidate)} is a UNC path; network locations are refused",
        )
    resolved = (candidate if candidate.is_absolute() else roots[0] / candidate).resolve()
    if spec.inside_root and not any(resolved.is_relative_to(root) for root in roots):
        _refuse(
            "path_outside_scope",
            f"{label}: cwd {_clip(resolved)} is outside the permitted roots "
            f"{[str(root) for root in roots]}",
        )
    if not resolved.is_dir():
        _refuse("cwd_missing", f"{label}: cwd {_clip(resolved)} does not exist or is a file")
    if path_alias is not None:
        return Path(_alias_path(path_alias, resolved, "cwd", label))
    return resolved


def _child_environment(
    spec: CommandSpec,
    env: Mapping[str, str],
    env_sources: Sequence[str],
    label: str,
) -> dict[str, str]:
    """Build the child's environment from scratch, or refuse.

    Only three things reach the child: the spec's allowlisted names taken from the
    parent, the spec's own ``extra_env`` values, and caller values for names the
    spec allowlists. Nothing else is inherited. A name the spec pins in
    ``extra_env`` cannot be supplied again, so a call site cannot overwrite a value
    the spec chose.
    """
    for name in (*spec.env_allowlist, *spec.extra_env):
        if not isinstance(name, str) or not _ENV_NAME.match(name):
            _refuse("env_not_allowed", f"{label}: {_clip(name, 40)} is not a plain variable name")
    child: dict[str, str] = {}
    for name in spec.env_allowlist:
        value = os.environ.get(name)
        if value is not None:
            child[name] = value
    for name, value in spec.extra_env.items():
        _check_env_value(name, value, label)
        child[name] = value
    labels = [env_sources] if isinstance(env_sources, (str, bytes)) else list(env_sources)
    for index, (name, value) in enumerate(env.items()):
        source = _source_label(labels, index, len(env))
        if not isinstance(name, str) or not _ENV_NAME.match(name):
            _refuse(
                "env_not_allowed",
                f"{label}: {_clip(name, 40)} from {source} is not a variable name",
            )
        if name in spec.extra_env:
            _refuse(
                "env_not_allowed",
                f"{label}: {name} is pinned by the spec and cannot be supplied again (from {source})",
            )
        if name not in spec.env_allowlist:
            _refuse(
                "env_not_allowed",
                f"{label}: {name} (from {source}) is not in the spec's env allowlist "
                f"{sorted(spec.env_allowlist)}",
            )
        _check_env_value(name, value, label)
        child[name] = value
    return child


def _check_env_value(name: str, value: object, label: str) -> None:
    """Refuse an environment value a child process could not receive intact.

    Only NUL is refused: it cannot survive the platform's environment block. A
    carriage return or line feed is allowed here (a PEM key needs them), which is
    safe because this module never writes an environment value into a message.
    """
    if not isinstance(value, str):
        _refuse(
            "env_not_allowed",
            f"{label}: {name} must be a string, got {type(value).__name__}",
        )
    if "\x00" in value:
        _refuse("env_not_allowed", f"{label}: {name} contains a NUL byte")


# --------------------------------------------------------------------------- #
# spawn, bounded capture, stop


def _execute(resolved: ResolvedCommand, label: str) -> CompletedCommand:
    """Start the one child, bound its output, and stop it on the timeout.

    Either stream passing its cap stops the child immediately (the whole tree), so
    a flood costs bounded memory and bounded CPU instead of a full timeout of
    reading. A run that hit a cap or the deadline is never reported as clean.
    """
    started = time.monotonic()
    try:
        process = _spawn(resolved)
    except OSError as exc:
        # Every check passed and the child still did not start: the path exists but is
        # not a runnable image (a stale or corrupt install), or the OS refuses for a
        # reason of its own. That is an *outcome* of this call, not an exception leaving
        # it -- this repository treats "could not run" as data everywhere else, and a raw
        # traceback out of the policy would hide which command was being run.
        return CompletedCommand(
            returncode=-1,
            stdout="",
            stderr="",
            duration_s=time.monotonic() - started,
            timed_out=False,
            truncated=False,
            spawn_error=_clip(f"{type(exc).__name__}: {exc}"),
        )
    stdout = _BoundedCapture(resolved.max_output_bytes)
    stderr = _BoundedCapture(resolved.max_output_bytes)
    readers = [
        threading.Thread(
            target=_pump, args=(process.stdout, stdout), name=f"{label}-stdout", daemon=True
        ),
        threading.Thread(
            target=_pump, args=(process.stderr, stderr), name=f"{label}-stderr", daemon=True
        ),
    ]
    timed_out = False
    try:
        for reader in readers:
            reader.start()
        deadline = started + resolved.timeout_s
        while not _wait_for_exit(process, _POLL_S):
            if stdout.truncated or stderr.truncated:
                _kill_process_tree(process)
                _wait_for_exit(process, _KILL_GRACE_S)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                _kill_process_tree(process)
                _wait_for_exit(process, _KILL_GRACE_S)
                break
        for reader in readers:
            reader.join(timeout=_DRAIN_GRACE_S)
        drained = not any(reader.is_alive() for reader in readers)
    finally:
        if process.poll() is None:
            _kill_process_tree(process)
            _wait_for_exit(process, _KILL_GRACE_S)
    return CompletedCommand(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=stdout.text(),
        stderr=stderr.text(),
        duration_s=time.monotonic() - started,
        timed_out=timed_out,
        truncated=(
            stdout.truncated or stderr.truncated or stdout.failed or stderr.failed or not drained
        ),
    )


def _spawn(resolved: ResolvedCommand) -> subprocess.Popen[bytes]:
    """Start exactly one child, from values :func:`validate` already checked.

    The only ``Popen`` in this module and the place every process in the product
    should come from. The flags mirror ``subprocess_guard.run_guarded`` on purpose:
    a list argv, ``shell=False``, no shared stdin, no console window on Windows,
    and a new session on POSIX so the timeout kill can reach the whole tree.
    """
    return subprocess.Popen(
        [str(resolved.executable), *resolved.tail],
        cwd=str(resolved.cwd),
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        env=dict(resolved.env),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )


class _BoundedCapture:
    """One child stream, read with a hard byte cap.

    Bytes past the cap are dropped and remembered; so is a read error, because in
    both cases the capture cannot be called complete. The run loop stops the child
    as soon as a capture is incomplete, so this never has to keep reading -- or
    keep memory -- for a child that floods.
    """

    __slots__ = ("_cap", "_kept", "_parts", "failed", "truncated")

    def __init__(self, cap: int) -> None:
        self._cap = cap
        self._kept = 0
        self._parts: list[bytes] = []
        self.truncated = False
        self.failed = False

    def absorb(self, chunk: bytes) -> None:
        room = self._cap - self._kept
        if room > 0:
            kept = chunk[:room]
            self._parts.append(kept)
            self._kept += len(kept)
        if len(chunk) > room:
            self.truncated = True

    def text(self) -> str:
        return b"".join(self._parts).decode("utf-8", "replace")


def _pump(stream: Any, capture: _BoundedCapture) -> None:
    """Read one pipe into its bounded capture until EOF; never raise."""
    if stream is None:  # pragma: no cover - both pipes are always created
        return
    try:
        while True:
            chunk = stream.read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            capture.absorb(chunk)
    except OSError, ValueError:
        # The pipe was closed under us (a kill, a crash). Whatever was read stands
        # and the result is marked incomplete, so this never looks like a full read.
        capture.failed = True
    finally:
        with contextlib.suppress(OSError, ValueError):
            stream.close()


def _wait_for_exit(process: subprocess.Popen[bytes], seconds: float) -> bool:
    """True when the child is reaped within ``seconds`` (``returncode`` is then set)."""
    try:
        process.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        return False
    return True


def _kill_process_tree(process: subprocess.Popen[Any]) -> None:
    """Kill the child and its descendants with the guard's own tree kill.

    ``subprocess_guard._kill_tree`` is the product's single implementation of this
    (``taskkill /F /T`` on Windows, ``killpg`` elsewhere) and expects the child to
    be a process-group leader, which ``_spawn`` arranges. It is module-private
    today; wave 3 should promote it to a public helper rather than let a second
    copy appear here.
    """
    subprocess_guard._kill_tree(process)


# --------------------------------------------------------------------------- #
# small shared helpers


def _refuse(code: str, detail: str) -> NoReturn:
    """Refuse a command. ``detail`` is built from labels and positions, never values."""
    raise CommandRefused(code, detail)


def _tokens(values: object, what: str = "argv") -> list[str]:
    """Turn a sequence into a list, refusing the single-string shape outright.

    A string is itself a sequence of strings, which is exactly how a command line
    written as text slips past a length check; it is refused by name instead.
    """
    if isinstance(values, (str, bytes)):
        _refuse("shell_string", f"{what} is one string, not a sequence of argument tokens")
    try:
        return list(values)  # type: ignore[arg-type]
    except TypeError:
        _refuse(
            "argument_not_allowed",
            f"{what} must be a sequence of argument tokens, got {type(values).__name__}",
        )


def _check_value_shape(value: object, where: str) -> None:
    """Refuse a value token that is not a plain, single-line, non-flag token."""
    if not isinstance(value, str):
        _refuse("argument_not_allowed", f"{where} is {type(value).__name__}; values must be str")
    if not value:
        _refuse("argument_not_allowed", f"{where} is empty")
    if len(value) > _MAX_VALUE_CHARS:
        _refuse(
            "argument_not_allowed",
            f"{where} is {len(value)} characters; the limit is {_MAX_VALUE_CHARS}",
        )
    refused = sorted(set(value) & _VALUE_REFUSED)
    if refused:
        shown = ", ".join(_clip(char) for char in refused)
        _refuse("argument_not_allowed", f"{where} contains {shown}, which is refused in a value")
    if value.startswith("-"):
        _refuse(
            "argument_not_allowed",
            f"{where} begins with '-'; a value must never be readable as a flag",
        )


def _source_label(sources: Sequence[str], index: int, count: int) -> str:
    """Name where an environment value came from, so a refusal need not quote it."""
    if len(sources) == 1:
        return _clip(sources[0], 40)
    if len(sources) == count and index < len(sources):
        return _clip(sources[index], 40)
    return "the caller"


def _label(spec: CommandSpec) -> str:
    """A short, safe rendering of the spec's name for use in messages."""
    name = spec.name if isinstance(spec.name, str) else ""
    cleaned = "".join(char if (char.isalnum() or char in "._-") else "_" for char in name.strip())
    return cleaned[:64] or "unnamed-command"


def _clip(text: object, limit: int = 160) -> str:
    """A short, single-line, printable rendering for a refusal message.

    Control characters are escaped so a message can never contain a newline that
    forges a log entry, and the result is bounded so a huge token cannot fill a
    log. Used for tokens, paths and labels -- never for an environment value.
    """
    shown = "".join(char if char.isprintable() else f"\\x{ord(char):02x}" for char in str(text))
    return shown if len(shown) <= limit else shown[:limit] + "..."
