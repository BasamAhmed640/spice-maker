"""Execution policy: one process-creation point, with proof it refuses first.

The tests are organised the way the module is: refusals that must happen before a
process exists, the environment the child does and does not see, the timeout and
output bounds enforced against real children, and the meta-test that keeps every
other module from starting a process behind this module's back.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from boardmodeler.security.execution import (
    ANY_VALUE,
    PATH_VALUE,
    WINDOWS_BASE_ENV,
    CommandCall,
    CommandRefused,
    CommandSpec,
    CompletedCommand,
    ResolvedCommand,
    bounded,
    command_line,
    run,
    system_executable,
    validate,
)
from boardmodeler.ui.worker_client import WorkerClient

SECRET = "test-only-token-not-a-real-key"

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "src" / "boardmodeler"

#: Modules that may still name a process-creation call, with the reason that site
#: owns its own process loop instead of going through
#: :func:`boardmodeler.security.execution.run`. Wave 2 deletes an entry once that
#: module's last site goes through the module; an entry here is a reviewed exception,
#: never a licence to add another site. Every entry that owns a loop must call
#: ``execution.validate()`` first -- owning the loop means owning the kill, the deadline
#: and the output bound as well, so the policy has to have run before the child exists.
SPAWN_SITE_ALLOWLIST: dict[str, str] = {
    "security/subprocess_guard.py": (
        "the guard itself: run_guarded is the pre-existing spawner and owns the one "
        "tree kill, which execution.run() calls; wave 3 folds the bounded reader into "
        "it so there is a single spawn path again"
    ),
    "security/execution.py": (
        "the module this test protects: it holds the one Popen and the one tree-kill "
        "call that every other site must come through"
    ),
    "simulation/ltspice.py": (
        "LTspice -b/-netlist must stay inside this module's own loop: the watchdog kills "
        "the tree once the log shows the simulator's completion marker, and it has to "
        "honour a cancel event while streaming -- neither fits a buffered runner. Both "
        "calls execution.validate() first and spawn exactly the resolved "
        "executable/tail/cwd/env; -version and the tree kill go through execution.run()"
    ),
    "ui/worker_client.py": (
        "the worker's stdout is a JSON-lines protocol that must be parsed as it arrives "
        "(the window shows stages while the build runs), so this module owns the loop: "
        "it calls execution.validate() first, enforces the spec's 8-hour deadline and "
        "output cap in its own pumps, and kills through the policy's taskkill spec"
    ),
    "authoring/backends.py": (
        "the model-authoring backends still run their own guarded launch with their own "
        "tree kill; converting them is not part of this change, so the entry stays "
        "until that work lands and removes it"
    ),
}

#: The call names this scan treats as process creation, by module. ``check_*`` and
#: the two ``get*`` helpers are included because they are the same act through a
#: shell string. ``os.exec*`` is included too: replacing the image is still
#: creating a process, and nothing in the product should do it behind this module.
_SUBPROCESS_CALLS = frozenset(
    {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}
)
_OS_CALLS = frozenset(
    {
        "system",
        "popen",
        "posix_spawn",
        "posix_spawnp",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
    }
)

# --------------------------------------------------------------------------- #
# fixtures and helpers


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """The run root: everything a command may touch has to live under it."""
    directory = tmp_path / "root"
    directory.mkdir()
    return directory


def _spec(
    *,
    tail: Sequence[str],
    timeout_s: float = 10.0,
    max_output_bytes: int = 65536,
    executable: Path | None = None,
    **rest: object,
) -> CommandSpec:
    """A spec pinned to this interpreter, whose basename the guard already allows."""
    return CommandSpec(
        name="test-python",
        executable=executable if executable is not None else Path(sys.executable),
        argv_tail=tuple(tail),
        timeout_s=timeout_s,
        max_output_bytes=max_output_bytes,
        **rest,  # type: ignore[arg-type]
    )


def _write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def _wait_until(predicate: Callable[[], bool], timeout_s: float = 15.0) -> bool:
    """Poll ``predicate`` until it holds or the budget expires (repo style)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


PRINT_ENV_SCRIPT = """\
import json
import os

print(json.dumps(dict(os.environ), sort_keys=True))
"""

ECHO_ARG_SCRIPT = """\
import sys

print(sys.argv[1])
"""

HELLO_SCRIPT = """\
import sys

print("out")
print("err", file=sys.stderr)
sys.exit(3)
"""

TREE_SCRIPT = """\
import os
import subprocess
import sys
import time
from pathlib import Path

grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
Path(sys.argv[1]).write_text(f"{os.getpid()} {grandchild.pid}", encoding="utf-8")
time.sleep(120)
"""

FLOOD_SCRIPT = """\
import sys

chunk = "x" * 4096
for _ in range(4096):
    print(chunk, file=sys.stderr if sys.argv[1] == "stderr" else sys.stdout)
"""


# --------------------------------------------------------------------------- #
# the executable is pinned, absolute, and on the guard's allowlist


def test_refuses_an_executable_basename_that_is_not_allowed(root: Path) -> None:
    spec = _spec(tail=(), executable=root / "eviltool.exe")
    with pytest.raises(CommandRefused) as info:
        run(spec, cwd=root, root=root)
    assert info.value.code == "executable_not_allowed"
    assert "eviltool.exe" in info.value.detail


def test_refuses_a_relative_executable(root: Path) -> None:
    spec = _spec(tail=(), executable=Path(Path(sys.executable).name))
    with pytest.raises(CommandRefused) as info:
        run(spec, cwd=root, root=root)
    assert info.value.code == "executable_not_absolute"


@pytest.mark.parametrize("relative", ["tools/python.exe", "tools/sub/python.exe"])
def test_refuses_a_non_absolute_path(root: Path, relative: str) -> None:
    spec = _spec(tail=(), executable=Path(relative))
    with pytest.raises(CommandRefused) as info:
        run(spec, cwd=root, root=root)
    assert info.value.code == "executable_not_absolute"


def test_a_spec_may_permit_an_extra_executable(root: Path) -> None:
    """``extra_allowed_executables`` is what makes the difference, not the path."""
    tool = _write(root, "eviltool.exe", "")
    refused = _spec(tail=(), executable=tool)
    with pytest.raises(CommandRefused) as info:
        run(refused, cwd=root, root=root)
    assert info.value.code == "executable_not_allowed"

    permitted = _spec(tail=(), executable=tool, extra_allowed_executables=("eviltool.exe",))
    with pytest.raises(CommandRefused) as info:
        run(permitted, cwd=root / "gone", root=root)
    # Past the executable check: it now fails on the next thing, the working directory.
    assert info.value.code == "cwd_missing"


def test_refuses_an_executable_that_is_a_unc_path(root: Path) -> None:
    spec = _spec(tail=(), executable=Path(r"\\server\share\python.exe"))
    with pytest.raises(CommandRefused) as info:
        run(spec, cwd=root, root=root)
    assert info.value.code == "path_outside_scope"
    assert "UNC" in info.value.detail


def test_refuses_an_executable_that_does_not_exist(root: Path) -> None:
    """A stale install path is a refusal, not an OSError out of the spawn."""
    spec = _spec(tail=(), executable=root / Path(sys.executable).name)
    with pytest.raises(CommandRefused) as info:
        run(spec, cwd=root, root=root)
    assert info.value.code == "executable_not_found"


def test_an_executable_outside_the_specs_directories_is_refused(root: Path) -> None:
    tools = root / "tools"
    tools.mkdir()
    spec = _spec(tail=(), allowed_dirs=(tools,))
    with pytest.raises(CommandRefused) as info:
        run(spec, cwd=root, root=root)
    assert info.value.code == "path_outside_scope"
    assert "allowed directory" in info.value.detail


# --------------------------------------------------------------------------- #
# argv: never a string, never a NUL, never a metacharacter in a value


def test_refuses_a_string_argv(root: Path) -> None:
    with pytest.raises(CommandRefused) as info:
        run(_spec(tail=("--json",)), argv="--json", cwd=root, root=root)  # type: ignore[arg-type]
    assert info.value.code == "shell_string"


def test_refuses_a_nul_byte_in_a_value(root: Path) -> None:
    script = _write(root, "noop.py", "print('ran')\n")
    with pytest.raises(CommandRefused) as info:
        run(_spec(tail=(PATH_VALUE,)), argv=(f"{script}\x00",), cwd=root, root=root)
    assert info.value.code == "argument_not_allowed"
    assert "\\x00" in info.value.detail


@pytest.mark.parametrize(
    "metacharacter",
    ["&", "|", ">", "<", "^", "%", "!", '"', "'", "`", "$", "(", ")", ";", ",", "*", "?"],
)
def test_refuses_a_shell_metacharacter_in_a_value(root: Path, metacharacter: str) -> None:
    with pytest.raises(CommandRefused) as info:
        run(
            _spec(tail=(ANY_VALUE,)),
            argv=(f"value{metacharacter}tail",),
            cwd=root,
            root=root,
        )
    assert info.value.code == "argument_not_allowed"
    assert "refused in a value" in info.value.detail


@pytest.mark.parametrize("newline", ["\n", "\r"])
def test_refuses_a_newline_in_a_value(root: Path, newline: str) -> None:
    with pytest.raises(CommandRefused) as info:
        run(_spec(tail=(ANY_VALUE,)), argv=(f"a{newline}b",), cwd=root, root=root)
    assert info.value.code == "argument_not_allowed"


def test_refuses_a_value_that_would_be_read_as_a_flag(root: Path) -> None:
    with pytest.raises(CommandRefused) as info:
        run(_spec(tail=(ANY_VALUE,)), argv=("-o",), cwd=root, root=root)
    assert info.value.code == "argument_not_allowed"
    assert "flag" in info.value.detail


def test_a_filename_can_never_land_in_a_flag_position(root: Path) -> None:
    """The deck path is data; the first tail position is the literal ``-o`` flag."""
    deck = root / "deck.cir"
    spec = _spec(tail=("-o", PATH_VALUE))
    with pytest.raises(CommandRefused) as info:
        run(spec, argv=(str(deck), str(root / "out.cir")), cwd=root, root=root)
    assert info.value.code == "argument_not_allowed"
    assert "literal" in info.value.detail


def test_refuses_a_tail_that_does_not_match_the_spec(root: Path) -> None:
    with pytest.raises(CommandRefused) as info:
        run(_spec(tail=("-b", PATH_VALUE)), argv=("-b",), cwd=root, root=root)
    assert info.value.code == "argument_not_allowed"
    assert "expects" in info.value.detail


def test_refuses_an_argv_that_repeats_the_executable(root: Path) -> None:
    spec = _spec(tail=(ANY_VALUE,))
    with pytest.raises(CommandRefused) as info:
        run(spec, argv=(str(spec.executable),), cwd=root, root=root)
    assert info.value.code == "argument_not_allowed"
    assert "after it" in info.value.detail


# --------------------------------------------------------------------------- #
# the working directory


def test_refuses_a_cwd_outside_the_root(root: Path, tmp_path: Path) -> None:
    with pytest.raises(CommandRefused) as info:
        run(_spec(tail=()), cwd=tmp_path, root=root)
    assert info.value.code == "path_outside_scope"


@pytest.mark.parametrize("name", ["missing", "sub/deeper"])
def test_refuses_a_cwd_that_does_not_exist(root: Path, name: str) -> None:
    with pytest.raises(CommandRefused) as info:
        run(_spec(tail=()), cwd=root / name, root=root)
    assert info.value.code == "cwd_missing"


def test_refuses_a_cwd_that_is_a_unc_path(root: Path) -> None:
    with pytest.raises(CommandRefused) as info:
        run(_spec(tail=()), cwd=r"\\server\share\work", root=root)
    assert info.value.code == "path_outside_scope"
    assert "UNC" in info.value.detail


def test_refuses_a_cwd_that_is_a_file(root: Path) -> None:
    file = _write(root, "not-a-directory", "x")
    with pytest.raises(CommandRefused) as info:
        run(_spec(tail=()), cwd=file, root=root)
    assert info.value.code == "cwd_missing"


def test_a_relative_cwd_is_resolved_against_the_root(root: Path) -> None:
    """A relative cwd must not follow the application's own working directory."""
    nested = root / "nested"
    nested.mkdir()
    script = _write(nested, "where.py", "import os\nprint(os.getcwd())\n")
    result = run(
        _spec(tail=(PATH_VALUE,)),
        argv=(str(script),),
        cwd="nested",
        root=root,
    )
    assert result.ok, result.stderr
    assert Path(result.stdout.strip()) == nested.resolve()


# --------------------------------------------------------------------------- #
# timeout and output cap are mandatory


@pytest.mark.parametrize("timeout", [0, -1, -0.5, float("nan"), float("inf")])
def test_refuses_a_timeout_that_is_not_positive_and_finite(root: Path, timeout: float) -> None:
    with pytest.raises(CommandRefused) as info:
        run(_spec(tail=(), timeout_s=timeout), cwd=root, root=root)
    assert info.value.code == "timeout_missing"


def test_refuses_a_missing_timeout(root: Path) -> None:
    with pytest.raises(CommandRefused) as info:
        run(_spec(tail=(), timeout_s=None), cwd=root, root=root)  # type: ignore[arg-type]
    assert info.value.code == "timeout_missing"


def test_timeout_and_output_cap_have_no_default() -> None:
    with pytest.raises(TypeError):
        CommandSpec(  # type: ignore[call-arg]
            name="x", executable=Path(sys.executable), argv_tail=()
        )
    with pytest.raises(TypeError):
        CommandSpec(  # type: ignore[call-arg]
            name="x", executable=Path(sys.executable), argv_tail=(), timeout_s=1.0
        )


@pytest.mark.parametrize("cap", [0, -1])
def test_refuses_a_command_that_would_be_unbounded(root: Path, cap: int) -> None:
    with pytest.raises(CommandRefused) as info:
        run(_spec(tail=(), max_output_bytes=cap), cwd=root, root=root)
    assert info.value.code == "output_too_large"


def test_refuses_a_missing_output_cap(root: Path) -> None:
    with pytest.raises(CommandRefused) as info:
        run(_spec(tail=(), max_output_bytes=None), cwd=root, root=root)  # type: ignore[arg-type]
    assert info.value.code == "output_too_large"


# --------------------------------------------------------------------------- #
# environment: nothing inherited, secrets never quoted


def test_child_sees_only_the_allowlisted_environment(root: Path, monkeypatch) -> None:
    monkeypatch.setenv("BOARDMODELER_ONLY_IN_PARENT", "leaked")
    monkeypatch.setenv("BOARDMODELER_ALLOWED", "kept")
    script = _write(root, "print_env.py", PRINT_ENV_SCRIPT)
    spec = _spec(tail=(PATH_VALUE,), env_allowlist=("BOARDMODELER_ALLOWED",))
    result = run(spec, argv=(str(script),), cwd=root, root=root)
    assert result.ok, result.stderr
    seen = json.loads(result.stdout)
    assert seen == {"BOARDMODELER_ALLOWED": "kept"}


def test_extra_env_reaches_the_child_and_never_an_error_message(root: Path) -> None:
    script = _write(root, "print_env.py", PRINT_ENV_SCRIPT)
    spec = _spec(tail=(PATH_VALUE,), extra_env={"BOARDMODELER_TEST_TOKEN": SECRET})
    result = run(spec, argv=(str(script),), cwd=root, root=root)
    assert result.ok, result.stderr
    assert json.loads(result.stdout)["BOARDMODELER_TEST_TOKEN"] == SECRET

    with pytest.raises(CommandRefused) as info:
        run(spec, argv=(str(script),), cwd=root / "gone", root=root)
    assert SECRET not in str(info.value)
    assert SECRET not in info.value.detail
    assert SECRET not in repr(spec)
    assert SECRET not in repr(CommandCall(spec=spec, argv=(str(script),)))


def test_refuses_an_env_value_the_spec_does_not_allow(root: Path) -> None:
    with pytest.raises(CommandRefused) as info:
        run(
            _spec(tail=()),
            cwd=root,
            root=root,
            env={"BOARDMODELER_SNEAK": "hunter2"},
            env_sources=("settings",),
        )
    assert info.value.code == "env_not_allowed"
    assert "BOARDMODELER_SNEAK" in info.value.detail
    assert "settings" in info.value.detail
    assert "hunter2" not in info.value.detail


def test_refuses_an_env_value_that_would_overwrite_a_pinned_one(root: Path) -> None:
    spec = _spec(
        tail=(),
        env_allowlist=("BOARDMODELER_TEST_TOKEN",),
        extra_env={"BOARDMODELER_TEST_TOKEN": SECRET},
    )
    with pytest.raises(CommandRefused) as info:
        run(spec, cwd=root, root=root, env={"BOARDMODELER_TEST_TOKEN": "other"})
    assert info.value.code == "env_not_allowed"
    assert "pinned" in info.value.detail


def test_an_env_value_that_is_not_a_string_is_refused(root: Path) -> None:
    spec = _spec(tail=(), env_allowlist=("BOARDMODELER_TEST_TOKEN",))
    with pytest.raises(CommandRefused) as info:
        run(spec, cwd=root, root=root, env={"BOARDMODELER_TEST_TOKEN": 5})  # type: ignore[dict-item]
    assert info.value.code == "env_not_allowed"


# --------------------------------------------------------------------------- #
# a real timeout kills the whole tree


def test_timeout_is_reported_and_the_process_tree_is_gone(root: Path) -> None:
    import psutil

    script = _write(root, "tree.py", TREE_SCRIPT)
    pids = root / "pids.txt"
    spec = _spec(tail=(PATH_VALUE, PATH_VALUE), timeout_s=2.0)
    result = run(spec, argv=(str(script), str(pids)), cwd=root, root=root)

    assert result.timed_out is True
    assert result.returncode != 0
    assert result.ok is False
    assert result.duration_s < 60.0
    assert pids.is_file(), "the child never started, so the tree was not exercised"
    child_pid, grandchild_pid = (int(part) for part in pids.read_text(encoding="utf-8").split())

    assert _wait_until(lambda: not psutil.pid_exists(child_pid)), "the child survived the timeout"
    assert _wait_until(lambda: not psutil.pid_exists(grandchild_pid)), (
        "the grandchild survived the timeout; the process tree was not killed"
    )


# --------------------------------------------------------------------------- #
# output is bounded for real


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_output_over_the_cap_is_truncated_and_does_not_hang(root: Path, stream: str) -> None:
    script = _write(root, "flood.py", FLOOD_SCRIPT)
    cap = 8192
    spec = _spec(tail=(PATH_VALUE, ANY_VALUE), max_output_bytes=cap, timeout_s=30.0)
    started = time.monotonic()
    result = run(spec, argv=(str(script), stream), cwd=root, root=root)

    captured = result.stdout if stream == "stdout" else result.stderr
    assert result.truncated is True
    assert len(captured) <= cap
    assert result.ok is False
    assert time.monotonic() - started < 20.0, "the flooded child was not stopped promptly"


def test_output_under_the_cap_is_complete(root: Path) -> None:
    script = _write(root, "small.py", "print('x' * 100)\n")
    result = run(
        _spec(tail=(PATH_VALUE,), max_output_bytes=4096), argv=(str(script),), cwd=root, root=root
    )
    assert result.truncated is False
    assert result.stdout.strip() == "x" * 100


# --------------------------------------------------------------------------- #
# the ordinary path still works


def test_run_returns_the_childs_exit_code_and_both_streams(root: Path) -> None:
    script = _write(root, "hello.py", HELLO_SCRIPT)
    result = run(_spec(tail=(PATH_VALUE,)), argv=(str(script),), cwd=root, root=root)
    assert isinstance(result, CompletedCommand)
    assert result.returncode == 3
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"
    assert result.timed_out is False
    assert result.truncated is False
    assert result.ok is False  # a non-zero exit is not success
    assert result.duration_s >= 0


def test_run_reports_ok_for_a_clean_child(root: Path) -> None:
    script = _write(root, "clean.py", "print('done')\n")
    result = run(_spec(tail=(PATH_VALUE,)), argv=(str(script),), cwd=root, root=root)
    assert result.ok is True
    assert result.stdout.strip() == "done"


def test_a_path_value_is_pinned_to_the_root_before_it_reaches_argv(root: Path) -> None:
    script = _write(root, "echo_arg.py", ECHO_ARG_SCRIPT)
    spec = _spec(tail=(PATH_VALUE, PATH_VALUE))
    result = run(spec, argv=(str(script), "deck.cir"), cwd=root, root=root)
    assert result.ok, result.stderr
    assert result.stdout.strip() == str((root / "deck.cir").resolve())


def test_a_command_call_carries_its_own_argv(root: Path) -> None:
    script = _write(root, "clean.py", "print('done')\n")
    call = CommandCall(spec=_spec(tail=(PATH_VALUE,)), argv=(str(script),))
    result = run(call, cwd=root, root=root)
    assert result.ok is True
    assert result.stdout.strip() == "done"


def test_a_command_call_refuses_a_string_argv() -> None:
    with pytest.raises(CommandRefused) as info:
        CommandCall(spec=_spec(tail=(PATH_VALUE,)), argv=str(Path(sys.executable)))  # type: ignore[arg-type]
    assert info.value.code == "shell_string"


def test_extra_env_is_stored_read_only() -> None:
    spec = _spec(tail=(), extra_env={"BOARDMODELER_TEST_TOKEN": SECRET})
    with pytest.raises(TypeError):
        spec.extra_env["BOARDMODELER_TEST_TOKEN"] = "changed"  # type: ignore[index]


# --------------------------------------------------------------------------- #
# templates and values as helpers


def test_command_line_returns_a_literal_template() -> None:
    assert command_line(("doctor", "--json")) == ["doctor", "--json"]
    assert command_line(("--", "-b", "Run", "-o=2")) == ["--", "-b", "Run", "-o=2"]
    # Windows system tools spell their switches with a slash: the tree kill needs
    # ``taskkill /PID <pid> /T /F``, and a template that cannot express it would leave
    # the one kill this product performs outside the policy.
    assert command_line(("/PID", "/T", "/F")) == ["/PID", "/T", "/F"]


@pytest.mark.parametrize(
    "token",
    [
        "C:/data/deck.cir",
        r"C:\data\deck.cir",
        "a b",
        "--out=%.raw",
        "-f;rm",
        "..",
        "*",
        "--json|cat",
        "/etc/passwd",
    ],
)
def test_command_line_refuses_anything_shaped_like_data(token: str) -> None:
    with pytest.raises(CommandRefused) as info:
        command_line((token,))
    assert info.value.code == "argument_not_allowed"


def test_command_line_refuses_a_bare_string_and_non_tokens() -> None:
    with pytest.raises(CommandRefused) as info:
        command_line("doctor")  # type: ignore[arg-type]
    assert info.value.code == "shell_string"
    with pytest.raises(CommandRefused) as info:
        command_line(("doctor", 7))  # type: ignore[arg-type]
    assert info.value.code == "argument_not_allowed"


def test_bounded_resolves_inside_the_root(root: Path) -> None:
    assert bounded(["sub/deck.cir"], root) == [str((root / "sub" / "deck.cir").resolve())]


def test_bounded_refuses_a_path_outside_the_root(root: Path, tmp_path: Path) -> None:
    with pytest.raises(CommandRefused) as info:
        bounded([str(tmp_path / "outside.cir")], root)
    assert info.value.code == "path_outside_scope"


def test_bounded_refuses_unc_paths(root: Path) -> None:
    with pytest.raises(CommandRefused) as info:
        bounded([r"\\server\share\deck.cir"], root)
    assert info.value.code == "path_outside_scope"
    assert "UNC" in info.value.detail


def test_bounded_refuses_a_bare_string(root: Path) -> None:
    with pytest.raises(CommandRefused) as info:
        bounded("deck.cir", root)  # type: ignore[arg-type]
    assert info.value.code == "shell_string"


@pytest.mark.parametrize("value", ["a&b", "a|b", "a;b", "a\nb", "a\x00b", "-o", "x" * 4097])
def test_bounded_refuses_malformed_values(root: Path, value: str) -> None:
    with pytest.raises(CommandRefused) as info:
        bounded([value], root)
    assert info.value.code == "argument_not_allowed"


def test_bounded_refuses_too_many_values(root: Path) -> None:
    with pytest.raises(CommandRefused) as info:
        bounded(["value"] * 65, root)
    assert info.value.code == "argument_not_allowed"


# --------------------------------------------------------------------------- #
# no refusal may create a process


def test_every_refusal_happens_before_a_process_is_created(
    root: Path, tmp_path: Path, monkeypatch
) -> None:
    """A refused command must never start anything: Popen is replaced by a tripwire."""
    script = _write(root, "noop.py", "print('ran')\n")
    paths: Sequence[str] = (str(script),)
    cases: dict[str, tuple[str, Callable[[], object]]] = {
        "string argv": (
            "shell_string",
            lambda: run(_spec(tail=(PATH_VALUE,)), argv=str(script), cwd=root, root=root),
        ),
        "executable not allowed": (
            "executable_not_allowed",
            lambda: run(
                _spec(tail=(PATH_VALUE,), executable=root / "evil.exe"),
                argv=paths,
                cwd=root,
                root=root,
            ),
        ),
        "executable not absolute": (
            "executable_not_absolute",
            lambda: run(
                _spec(tail=(PATH_VALUE,), executable=Path(Path(sys.executable).name)),
                argv=paths,
                cwd=root,
                root=root,
            ),
        ),
        "nul in a value": (
            "argument_not_allowed",
            lambda: run(_spec(tail=(PATH_VALUE,)), argv=(f"{script}\x00",), cwd=root, root=root),
        ),
        "metacharacter in a value": (
            "argument_not_allowed",
            lambda: run(_spec(tail=(ANY_VALUE,)), argv=(f"{script}&x",), cwd=root, root=root),
        ),
        "tail does not match the spec": (
            "argument_not_allowed",
            lambda: run(_spec(tail=(PATH_VALUE,)), argv=("a", "b"), cwd=root, root=root),
        ),
        "cwd outside the root": (
            "path_outside_scope",
            lambda: run(_spec(tail=(PATH_VALUE,)), argv=paths, cwd=tmp_path, root=root),
        ),
        "cwd missing": (
            "cwd_missing",
            lambda: run(_spec(tail=(PATH_VALUE,)), argv=paths, cwd=root / "missing", root=root),
        ),
        "timeout zero": (
            "timeout_missing",
            lambda: run(_spec(tail=(PATH_VALUE,), timeout_s=0), argv=paths, cwd=root, root=root),
        ),
        "output cap zero": (
            "output_too_large",
            lambda: run(
                _spec(tail=(PATH_VALUE,), max_output_bytes=0), argv=paths, cwd=root, root=root
            ),
        ),
        "env not allowlisted": (
            "env_not_allowed",
            lambda: run(
                _spec(tail=(PATH_VALUE,)),
                argv=paths,
                cwd=root,
                root=root,
                env={"BOARDMODELER_SNEAK": "hunter2"},
            ),
        ),
    }

    for name, (expected, call) in cases.items():
        calls: list[object] = []

        def tripwire(
            *args: object, _name: str = name, _calls: list[object] = calls, **kwargs: object
        ) -> None:
            _calls.append(args)
            raise AssertionError(f"{_name}: a process was created before the refusal")

        monkeypatch.setattr(subprocess, "Popen", tripwire)
        with pytest.raises(CommandRefused) as info:
            call()
        assert calls == [], f"{name}: Popen was reached"
        assert info.value.code == expected, f"{name}: got {info.value.code}"


# --------------------------------------------------------------------------- #
# validate(): the same policy, for a caller that keeps its own process loop
#
# The LTspice launch and the UI worker spawn their own child (a marker watchdog and a
# streamed JSON-lines protocol cannot be served by a buffered runner), so the policy has
# to be usable *without* the process. These tests pin the two properties that makes it
# safe: validate() runs every check run() runs, and it never creates anything itself.


def test_validate_returns_the_checked_command(root: Path) -> None:
    nested = root / "nested"
    nested.mkdir()
    script = _write(root, "clean.py", "print('done')\n")
    spec = _spec(
        tail=(PATH_VALUE,),
        timeout_s=7.0,
        max_output_bytes=4096,
        env_allowlist=("BOARDMODELER_ALLOWED",),
        extra_env={"BOARDMODELER_TEST_TOKEN": SECRET},
    )
    resolved = validate(spec, argv=(str(script),), cwd="nested", root=root)

    assert isinstance(resolved, ResolvedCommand)
    assert resolved.spec is spec
    assert resolved.executable == Path(sys.executable).resolve()
    assert resolved.executable.is_absolute() and resolved.executable.is_file()
    # The value is the *resolved* one, so what the caller spawns is what was checked.
    assert resolved.tail == (str(script.resolve()),)
    assert resolved.cwd == nested.resolve()
    assert resolved.timeout_s == 7.0
    assert resolved.max_output_bytes == 4096
    assert resolved.env == {"BOARDMODELER_TEST_TOKEN": SECRET}
    assert resolved.command == (str(resolved.executable), str(script.resolve()))


def test_validate_never_creates_a_process(root: Path, monkeypatch) -> None:
    """A validate-only call site must not be able to spawn by accident."""
    script = _write(root, "clean.py", "print('done')\n")
    calls: list[object] = []

    def tripwire(*args: object, **kwargs: object) -> None:
        calls.append(args)
        raise AssertionError("validate() created a process")

    monkeypatch.setattr(subprocess, "Popen", tripwire)
    resolved = validate(_spec(tail=(PATH_VALUE,)), argv=(str(script),), cwd=root, root=root)
    assert calls == []
    assert resolved.tail == (str(script.resolve()),)


@pytest.mark.parametrize(
    "case",
    [
        "string argv",
        "executable not allowed",
        "nul in a value",
        "metacharacter in a value",
        "tail does not match the spec",
        "cwd outside the root",
        "timeout zero",
        "output cap zero",
        "env not allowlisted",
    ],
)
def test_run_and_validate_refuse_with_the_same_code(root: Path, tmp_path: Path, case: str) -> None:
    """One implementation of the checks means one answer, whichever entry point is used."""
    script = _write(root, "clean.py", "print('done')\n")
    build: dict[str, Callable[[], object]] = {
        "string argv": lambda: (
            _spec(tail=(PATH_VALUE,)),
            {"argv": str(script), "cwd": root, "root": root},
        ),
        "executable not allowed": lambda: (
            _spec(tail=(), executable=root / "evil.exe"),
            {"cwd": root, "root": root},
        ),
        "nul in a value": lambda: (
            _spec(tail=(PATH_VALUE,)),
            {"argv": (f"{script}\x00",), "cwd": root, "root": root},
        ),
        "metacharacter in a value": lambda: (
            _spec(tail=(ANY_VALUE,)),
            {"argv": ("a&b",), "cwd": root, "root": root},
        ),
        "tail does not match the spec": lambda: (
            _spec(tail=(PATH_VALUE,)),
            {"argv": ("a", "b"), "cwd": root, "root": root},
        ),
        "cwd outside the root": lambda: (
            _spec(tail=()),
            {"cwd": tmp_path, "root": root},
        ),
        "timeout zero": lambda: (
            _spec(tail=(), timeout_s=0),
            {"cwd": root, "root": root},
        ),
        "output cap zero": lambda: (
            _spec(tail=(), max_output_bytes=0),
            {"cwd": root, "root": root},
        ),
        "env not allowlisted": lambda: (
            _spec(tail=()),
            {"cwd": root, "root": root, "env": {"BOARDMODELER_SNEAK": "hunter2"}},
        ),
    }
    spec, kwargs = build[case]()  # type: ignore[misc]

    with pytest.raises(CommandRefused) as from_run:
        run(spec, **kwargs)  # type: ignore[arg-type]
    with pytest.raises(CommandRefused) as from_validate:
        validate(spec, **kwargs)  # type: ignore[arg-type]
    assert from_validate.value.code == from_run.value.code
    assert from_validate.value.detail == from_run.value.detail


# --------------------------------------------------------------------------- #
# extra_roots: naming a directory instead of switching the check off


def test_extra_roots_admit_an_explicitly_named_directory(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "project"
    outside.mkdir()
    deck = _write(outside, "deck.cir", "* deck\n")
    script = _write(root, "echo_arg.py", ECHO_ARG_SCRIPT)
    result = run(
        _spec(tail=(PATH_VALUE, PATH_VALUE)),
        argv=(str(script), str(deck)),
        cwd=root,
        root=root,
        extra_roots=(outside,),
    )
    assert result.ok, result.stderr
    assert result.stdout.strip() == str(deck.resolve())


def test_extra_roots_do_not_turn_the_containment_check_off(root: Path, tmp_path: Path) -> None:
    """A path outside both the root and the named extras is still refused."""
    named = tmp_path / "named"
    named.mkdir()
    elsewhere = _write(tmp_path, "deck.cir", "* deck\n")
    with pytest.raises(CommandRefused) as info:
        run(
            _spec(tail=(PATH_VALUE,)),
            argv=(str(elsewhere),),
            cwd=root,
            root=root,
            extra_roots=(named,),
        )
    assert info.value.code == "path_outside_scope"
    assert "root" in info.value.detail


def test_extra_roots_also_admit_the_working_directory(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "project"
    outside.mkdir()
    script = _write(outside, "where.py", "import os\nprint(os.getcwd())\n")
    result = run(
        _spec(tail=(PATH_VALUE,)),
        argv=(str(script),),
        cwd=outside,
        root=root,
        extra_roots=(outside,),
    )
    assert result.ok, result.stderr
    assert Path(result.stdout.strip()) == outside.resolve()


def test_a_cwd_outside_root_and_extras_is_still_refused(root: Path, tmp_path: Path) -> None:
    named = tmp_path / "named"
    named.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(CommandRefused) as info:
        run(_spec(tail=()), cwd=other, root=root, extra_roots=(named,))
    assert info.value.code == "path_outside_scope"
    assert "permitted roots" in info.value.detail


# --------------------------------------------------------------------------- #
# path_alias: a re-spelling that cannot redirect the child


def _respell(path: Path) -> str:
    """The same location, written differently.

    Case on Windows, a doubled separator elsewhere -- what a short-path alias does is
    exactly this: it changes how the path is written, not what it names.
    """
    if os.name == "nt":
        return str(path).upper()
    return str(path.parent) + os.sep + os.sep + path.name


def test_path_alias_may_respell_a_path_it_resolves_to(root: Path) -> None:
    """The simulator's long-path alias is exactly this: same file, other spelling."""
    deck = root / "deck.cir"
    resolved = validate(
        _spec(tail=(PATH_VALUE,)),
        argv=(str(deck),),
        cwd=root,
        root=root,
        path_alias=_respell,
    )
    assert resolved.tail == (_respell(deck),)
    assert resolved.tail[0] != str(deck), "the alias must really be a different spelling"
    assert Path(resolved.tail[0]).resolve() == deck.resolve()


def test_path_alias_may_not_redirect_the_child(root: Path) -> None:
    other = _write(root, "other.cir", "* other\n")
    with pytest.raises(CommandRefused) as info:
        validate(
            _spec(tail=(PATH_VALUE,)),
            argv=(str(root / "deck.cir"),),
            cwd=root,
            root=root,
            path_alias=lambda path: str(other),
        )
    assert info.value.code == "path_outside_scope"
    assert "re-spell" in info.value.detail


def test_path_alias_must_stay_absolute(root: Path) -> None:
    with pytest.raises(CommandRefused) as info:
        validate(
            _spec(tail=(PATH_VALUE,)),
            argv=(str(root / "deck.cir"),),
            cwd=root,
            root=root,
            path_alias=lambda path: path.name,
        )
    assert info.value.code == "argument_not_allowed"
    assert "not absolute" in info.value.detail


def test_a_failing_alias_propagates_its_own_error(root: Path) -> None:
    """The simulator's ``windows_path_too_long`` OSError must not become a refusal code."""

    def alias(path: Path) -> str:
        raise OSError("windows_path_too_long: this volume has no short path alias")

    with pytest.raises(OSError, match="windows_path_too_long"):
        validate(
            _spec(tail=(PATH_VALUE,)),
            argv=(str(root / "deck.cir"),),
            cwd=root,
            root=root,
            path_alias=alias,
        )


def test_system_executable_names_a_real_allowlisted_tool() -> None:
    """The tree kill's tool is resolved to an absolute path, and the policy accepts it.

    This is the check that matters: the guard's allowlist carries the *bare* name it
    spawns through PATH, so a pinned ``taskkill.exe`` has to be permitted explicitly by
    the spec. Without that, every kill would be refused and the watchdogs would silently
    stop killing anything -- which is exactly what this test caught.
    """
    if os.name != "nt":
        pytest.skip("taskkill is a Windows tool")
    tool = system_executable("taskkill")
    assert tool.is_absolute()
    assert tool.is_file(), tool

    scratch = Path(tempfile.gettempdir())
    spec = CommandSpec(
        name="taskkill-test",
        executable=tool,
        argv_tail=("/PID", ANY_VALUE, "/T", "/F"),
        timeout_s=10.0,
        max_output_bytes=4096,
        env_allowlist=WINDOWS_BASE_ENV,
        extra_allowed_executables=(tool.name,),
    )
    resolved = validate(spec, argv=("/PID", "1", "/T", "/F"), cwd=scratch, root=scratch)
    assert resolved.executable == tool

    # And it really kills: the same spec shape, against a child started here, must end
    # that child. A spec the policy refuses would look identical from the outside -- the
    # watchdog would simply stop killing anything -- which is why this is asserted.
    victim = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        killed = run(spec, argv=("/PID", str(victim.pid), "/T", "/F"), cwd=scratch, root=scratch)
        assert killed.ok, killed.stderr
        assert _wait_until(lambda: victim.poll() is not None, timeout_s=15.0), (
            "the tree kill did not end the child"
        )
    finally:
        if victim.poll() is None:  # pragma: no cover - only on a failed kill
            victim.kill()

    # Without the explicit permission the same command is refused, which is what makes
    # the permission meaningful rather than decorative.
    unpermitted = CommandSpec(
        name="taskkill-test",
        executable=tool,
        argv_tail=("/PID", ANY_VALUE, "/T", "/F"),
        timeout_s=10.0,
        max_output_bytes=4096,
        env_allowlist=WINDOWS_BASE_ENV,
    )
    with pytest.raises(CommandRefused) as info:
        validate(unpermitted, argv=("/PID", "1", "/T", "/F"), cwd=scratch, root=scratch)
    assert info.value.code == "executable_not_allowed"


# --------------------------------------------------------------------------- #
# the worker spawn site: explicit environment, hard deadline, bounded stream

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: A controller the worker can import: it records its own environment and PID (so the
#: tests can see exactly what reached the child), can flood a stream, and can hold.
STUB_CONTROLLER = '''
"""Stub controller for the worker spawn-site tests."""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PipelineRequest:
    project_dir: Path
    hold_s: float = 0.0
    flood_bytes: int = 0


class PipelineController:
    def __init__(self, config: object | None = None) -> None:
        self.config = config

    def run(self, request, progress=None, cancel=None) -> dict:
        Path("child-env.json").write_text(
            json.dumps(dict(os.environ), sort_keys=True), encoding="utf-8"
        )
        Path("child-pid.txt").write_text(str(os.getpid()), encoding="utf-8")
        if request.flood_bytes:
            sys.stderr.write("x" * request.flood_bytes)
            sys.stderr.flush()
        if request.hold_s:
            time.sleep(request.hold_s)
        return {
            "status": "PASS",
            "stages": [],
            "results": [],
            "findings": [],
            "review_items": [],
            "artifacts": [],
            "diagnostics": {},
        }
'''


@pytest.fixture
def worker_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project directory plus an importable stub controller for the worker child."""
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "worker_stub_controller.py").write_text(STUB_CONTROLLER, encoding="utf-8")
    existing = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path) + (os.pathsep + existing if existing else ""))
    return project


def _start_worker(
    client: WorkerClient,
    project: Path,
    payload: Mapping[str, object] | None = None,
    **start: object,
) -> None:
    """Start the worker for ``payload``; ``start`` kwargs belong to the client, not the
    request -- the worker rejects unknown request fields, which is the point of them."""
    client.start(
        {"project_dir": str(project), **(payload or {})},
        project_dir=project,
        **start,  # type: ignore[arg-type]
    )


def test_the_worker_child_cannot_see_unrelated_secrets(qapp, worker_project: Path) -> None:
    """The child is this program's interpreter, not a copy of the operator's shell."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake-aws-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-github-token")
    monkeypatch.setenv("SOME_UNRELATED_VARIABLE", "fake-unrelated")
    monkeypatch.setenv("LTSPICE_EXE", r"C:\fake\LTspice.exe")
    monkeypatch.setenv("BOARDMODELER_TEST_ONLY", "kept")
    try:
        client = WorkerClient(
            project_dir=worker_project, controller_module="worker_stub_controller"
        )
        _start_worker(client, worker_project)
        outcome = client.wait(120)
        assert outcome.exit_code == 0, (outcome.exit_code, outcome.stderr)
        child_env = json.loads((worker_project / "child-env.json").read_text(encoding="utf-8"))
    finally:
        monkeypatch.undo()

    assert "AWS_SECRET_ACCESS_KEY" not in child_env
    assert "GITHUB_TOKEN" not in child_env
    assert "SOME_UNRELATED_VARIABLE" not in child_env
    assert child_env["LTSPICE_EXE"] == r"C:\fake\LTspice.exe"
    assert child_env["BOARDMODELER_TEST_ONLY"] == "kept"
    # ``os.environ`` uppercases every key on Windows, so the child reports the OS
    # variable under its uppercase spelling whichever spelling the spec allowlists.
    assert any(name.upper() == "SYSTEMROOT" for name in child_env)


def test_the_worker_child_gets_only_the_selected_providers_key_variables(
    qapp, worker_project: Path
) -> None:
    """Provider variables are read from the catalog, not hardcoded -- and per provider."""
    from boardmodeler.ui.worker_client import _selected_provider_id, provider_env_names

    selected = provider_env_names(_selected_provider_id())
    candidates = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY")
    other = next(name for name in candidates if name not in selected)

    monkeypatch = pytest.MonkeyPatch()
    for name in selected:
        monkeypatch.setenv(name, "fake-selected-provider-key")
    monkeypatch.setenv(other, "fake-other-provider-key")
    try:
        client = WorkerClient(
            project_dir=worker_project, controller_module="worker_stub_controller"
        )
        _start_worker(client, worker_project)
        outcome = client.wait(120)
        assert outcome.exit_code == 0, (outcome.exit_code, outcome.stderr)
        child_env = json.loads((worker_project / "child-env.json").read_text(encoding="utf-8"))
    finally:
        monkeypatch.undo()

    assert selected, "a build with no provider variables would make this test vacuous"
    for name in selected:
        assert child_env.get(name) == "fake-selected-provider-key", name
    assert other not in child_env


def test_the_build_deadline_kills_the_worker_tree(qapp, worker_project: Path) -> None:
    """A build that outlives its deadline is killed, and the detail names the deadline."""
    import psutil

    client = WorkerClient(project_dir=worker_project, controller_module="worker_stub_controller")
    errors: list[dict] = []
    client.error.connect(errors.append)

    started = time.monotonic()
    _start_worker(client, worker_project, {"hold_s": 120.0}, deadline_s=8.0)
    assert _wait_until(lambda: (worker_project / "child-pid.txt").is_file(), timeout_s=30.0), (
        "the worker child never started, so the deadline was not exercised"
    )
    pid = int((worker_project / "child-pid.txt").read_text(encoding="utf-8"))
    outcome = client.wait(180)
    elapsed = time.monotonic() - started
    qapp.processEvents()

    # The real default is 8 hours: nothing here may wait for it, and nothing may pass
    # this test by killing the child before its deadline either.
    assert 7.0 <= elapsed < 60.0, f"deadline did not fire at 8 s (took {elapsed:.1f}s)"
    assert outcome.error is not None, outcome.stderr
    assert outcome.error["code"] == "deadline_exceeded"
    assert "8" in str(outcome.error["detail"]) and "deadline" in str(outcome.error["detail"])
    assert any(event.get("code") == "deadline_exceeded" for event in errors)
    assert not client.is_running()
    assert _wait_until(lambda: not psutil.pid_exists(pid), timeout_s=20.0), (
        "the worker survived its deadline; the process tree was not killed"
    )


def test_the_worker_stream_is_bounded(qapp, worker_project: Path) -> None:
    """A child that floods a stream is stopped instead of filling this process."""
    import psutil

    client = WorkerClient(
        project_dir=worker_project,
        controller_module="worker_stub_controller",
        max_output_bytes=4096,
    )
    _start_worker(client, worker_project, {"flood_bytes": 400_000, "hold_s": 120.0})
    assert _wait_until(lambda: (worker_project / "child-pid.txt").is_file(), timeout_s=30.0)
    pid = int((worker_project / "child-pid.txt").read_text(encoding="utf-8"))
    outcome = client.wait(120)

    assert outcome.error is not None
    assert outcome.error["code"] == "output_too_large"
    assert "4096" in str(outcome.error["detail"])
    assert not client.is_running()
    assert _wait_until(lambda: not psutil.pid_exists(pid), timeout_s=20.0), (
        "the flooding worker was not killed"
    )


def test_the_worker_spawn_is_refused_before_it_exists(qapp, worker_project: Path) -> None:
    """A missing interpreter is a policy refusal, not an OSError from a spawn."""
    client = WorkerClient(
        python=worker_project / "no-such-python.exe",
        project_dir=worker_project,
        controller_module="worker_stub_controller",
    )
    with pytest.raises(CommandRefused) as info:
        _start_worker(client, worker_project)
    assert info.value.code == "executable_not_found"
    assert not client.is_running()


def test_the_worker_spawn_requires_a_real_deadline(qapp, worker_project: Path) -> None:
    """The deadline is not optional: the policy refuses a spec without one."""
    client = WorkerClient(project_dir=worker_project, controller_module="worker_stub_controller")
    with pytest.raises(CommandRefused) as info:
        _start_worker(client, worker_project, deadline_s=0)
    assert info.value.code == "timeout_missing"


# --------------------------------------------------------------------------- #
# a spawn the OS refuses is an outcome, not an exception


def test_a_spawn_the_os_refuses_is_reported_as_data(root: Path) -> None:
    """A path that exists but is not a runnable image: measured ``WinError 216`` here.

    Every policy check passes (the file exists, the basename is allowlisted) and the OS
    then refuses to start it -- a stale or corrupt install is exactly this. It must come
    back as a ``CompletedCommand`` describing the failure, because a traceback out of a
    policy entry point both hides which command was being run and breaks the contract of
    callers that treat "could not run" as data (``ltspice.version`` is one).
    """
    fake = root / Path(sys.executable).name
    fake.write_text("this is not a runnable image\n", encoding="utf-8")
    result = run(_spec(tail=(), executable=fake), cwd=root, root=root)

    assert isinstance(result, CompletedCommand)
    assert result.spawn_error, result
    assert result.ok is False
    assert result.returncode == -1
    assert result.stdout == "" and result.stderr == ""
    assert result.timed_out is False and result.truncated is False


def test_the_simulator_version_is_none_when_the_image_cannot_run(root: Path) -> None:
    """The regression that found this: a fake ``LTspice.exe`` must not raise.

    ``doctor`` reports the simulator's version for whatever it discovered, so a configured
    path holding something that is not a runnable image used to be swallowed by
    ``except OSError`` and has to stay swallowed now that the launch goes through the
    policy.
    """
    from boardmodeler.simulation.ltspice import version

    fake = _write(root, "LTspice.exe", "this is not a runnable image\n")
    assert version(fake) is None


# --------------------------------------------------------------------------- #
# the meta-test that keeps this the only way in


def _spawn_sites(path: Path) -> list[tuple[int, str]]:
    """Every process-creation call in one module, as ``(line, call)`` pairs.

    Module aliases (``import subprocess as sp``) and direct names
    (``from subprocess import run``) are resolved, so the only way to hide a call
    is to reach it through a name this scan cannot see -- which is what the
    allowlist exists to make visible.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: dict[str, str] = {}
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"subprocess", "os"}:
                    modules[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in {"subprocess", "os"}:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    sites: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            module = modules.get(func.value.id)
            if module == "subprocess" and (
                func.attr in _SUBPROCESS_CALLS or func.attr.startswith("check_")
            ):
                sites.append((node.lineno, f"subprocess.{func.attr}"))
            elif module == "os" and func.attr in _OS_CALLS:
                sites.append((node.lineno, f"os.{func.attr}"))
        elif isinstance(func, ast.Name) and func.id in names:
            sites.append((node.lineno, names[func.id]))
    return sorted(sites)


def test_the_spawn_scan_finds_every_shape_of_process_creation(tmp_path: Path) -> None:
    """The meta-test is only as good as the scan, so the scan is tested too."""
    module = _write(
        tmp_path,
        "synthetic.py",
        "import os\n"
        "import subprocess\n"
        "import subprocess as sp\n"
        "from subprocess import check_output as capture\n"
        'subprocess.run(["tool"])\n'
        'sp.Popen(["tool"])\n'
        'capture(["tool"])\n'
        'os.system("tool")\n'
        'os.popen("tool")\n'
        'os.spawnv(0, "tool", [])\n'
        'os.execv("tool", ["tool"])\n'
        "client.run()\n"
        "annotation: subprocess.Popen[bytes] | None = None\n",
    )
    assert [call for _, call in _spawn_sites(module)] == [
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.check_output",
        "os.system",
        "os.popen",
        "os.spawnv",
        "os.execv",
    ]


def test_process_creation_is_confined_to_the_allowlisted_modules() -> None:
    offenders: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        relative = path.relative_to(PACKAGE_ROOT).as_posix()
        if relative in SPAWN_SITE_ALLOWLIST:
            continue
        for lineno, call in _spawn_sites(path):
            offenders.append(f"{relative}:{lineno}: {call}")

    if offenders:
        # Printed as well as asserted: wave 2 needs the file:line list to work from.
        print("\nprocess-creation call sites outside security/execution.py:")
        for offender in offenders:
            print(f"  {offender}")

    assert not offenders, (
        "these call sites still create processes outside security/execution.py and must be "
        "routed through execution.run() (then delete the module's entry in "
        "SPAWN_SITE_ALLOWLIST, and its comment, in the same change):\n  " + "\n  ".join(offenders)
    )


def test_no_allowlist_entry_is_stale() -> None:
    """An allowlisted module with no spawn site left is a comment that now lies."""
    stale = [
        relative for relative in SPAWN_SITE_ALLOWLIST if not _spawn_sites(PACKAGE_ROOT / relative)
    ]
    assert not stale, f"remove these from SPAWN_SITE_ALLOWLIST: {stale}"
