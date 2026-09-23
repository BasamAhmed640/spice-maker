"""Qt client for the worker protocol (INTERFACES §2/§4).

``WorkerClient`` spawns ``python -m boardmodeler.pipeline.worker`` with a list
argv (``shell=False``) and re-emits every protocol event as a Qt signal. The GUI
never computes a verdict: it renders the events this client forwards.

Signals carry the decoded event dictionary unchanged; ``exited(int)`` fires once
the child is gone and ``outcome`` then holds everything the child said (exit
code, terminal ``result``/``error`` events, stderr, protocol violations).

The child is this program's own interpreter, so it needs more of the parent
environment than a third-party tool would -- but the boundary is still explicit.
Before the child exists, :func:`boardmodeler.security.execution.validate` pins the
interpreter, the argv, the working directory and the environment; the environment
is a named allowlist (the OS basics, ``LTSPICE_EXE``, every ``BOARDMODELER_*``
variable and the selected provider's own documented key variables), never
``os.environ`` wholesale. A build also has a hard deadline (8 hours by default)
and a bounded protocol stream: the pumps below stop a child that floods, and the
deadline kills the tree of one that stops progressing. This module keeps its own
process loop because the protocol is *streamed* -- the window shows stages as they
happen -- which is why it is still named in the spawn-site allowlist in
``tests/security/test_execution.py``.
"""

from __future__ import annotations

import codecs
import contextlib
import enum
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from boardmodeler.security import execution
from boardmodeler.storage import app_root

__all__ = [
    "BUILD_DEADLINE_S",
    "EVENT_SIGNALS",
    "WORKER_MAX_OUTPUT_BYTES",
    "WorkerClient",
    "WorkerOutcome",
    "jsonable_request",
    "provider_env_names",
    "terminate_process_tree",
]

#: The worker entry point. A literal written here, never data: the spec's tail is a
#: template, and this is one of its fixed tokens.
_WORKER_MODULE = "boardmodeler.pipeline.worker"

#: A build is not a query: it extracts, generates, simulates and reviews for minutes.
#: The deadline is a backstop against the build that is alive but no longer
#: progressing, so it is generous by design (8 hours) and always present.
BUILD_DEADLINE_S = 28_800.0

#: The pumps keep every protocol line in memory, so the stream is bounded. A real build
#: streams stage lines -- kilobytes -- and the cap is far above that; it exists so a
#: runaway child cannot grow the UI process without limit.
WORKER_MAX_OUTPUT_BYTES = 64 << 20

#: How much of a pipe one read takes. Small enough that a line is handed over as soon as
#: it arrives (the window shows stages while the build runs), large enough that a busy
#: stream is not read one byte at a time.
_READ_CHUNK_BYTES = 1 << 16

#: Client-side error codes, in the worker protocol's own shape (``event``/``code``/
#: ``detail``), so the window renders them like any other error event.
DEADLINE_EXCEEDED = "deadline_exceeded"
OUTPUT_TOO_LARGE = "output_too_large"

EVENT_SIGNALS: dict[str, str] = {
    "stage": "stage",
    "progress": "progress",
    "findings": "findings",
    "review": "review",
    "waveform": "waveform",
    "result": "result",
    "error": "error",
}
"""Protocol event kind -> ``WorkerClient`` signal name."""

_TERMINAL_EVENTS = ("result", "error")

#: Environment names the worker child may inherit. The worker is this program's own
#: interpreter, so it needs more than the simulator's list -- but it is still a list,
#: and the names that are *not* on it are the point: credentials the operator happens to
#: export for other tools (``AWS_SECRET_ACCESS_KEY``, ``GITHUB_TOKEN``, ...) are not
#: this program's business and must not reach a child whose output an agent-driven
#: pipeline consumes. ``PYTHON*`` are the interpreter's own documented variables: the
#: worker must start exactly as it does today. ``SPICE_MAKER_ROOT`` decides which copy
#: the child believes it belongs to, so dropping it would silently split the app in two.
_WORKER_ENV_BASE: tuple[str, ...] = (
    *execution.WINDOWS_BASE_ENV,
    "PATH",
    "PATHEXT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
    "SPICE_MAKER_ROOT",
    # The simulator's documented automation override: the pipeline resolves LTspice with
    # it, and the test suite exports it instead of writing a config file.
    "LTSPICE_EXE",
    # The documented config-file override (``boardmodeler.config``).
    "BOARDMODELER_CONFIG",
)


def provider_env_names(provider_id: str | None = None) -> tuple[str, ...]:
    """The documented environment names of the selected agent provider.

    Read from the provider catalog and the credential module, never hardcoded: this
    build ships several providers, each documents its own variable, and a list written
    out here would silently stop matching the catalog the first time a provider is
    added or renamed. Two names per provider: the ``BOARDMODELER_<CREDENTIAL>_API_KEY``
    fallback the credential layer documents, and the vendor's own alias for people who
    already export it. ``provider_id`` that this build does not accept falls back to the
    build's default entry -- the same resolution the rest of the application uses.
    """
    from boardmodeler.agent_providers import by_id, default_provider
    from boardmodeler.security.credentials import env_var_name

    provider = by_id(provider_id) or default_provider()
    names = [env_var_name(provider.credential), *provider.env_aliases]
    return tuple(dict.fromkeys(names))


def _selected_provider_id() -> str | None:
    """The provider the user selected, or ``None`` for this build's default.

    A config that cannot be read is not a reason to fail the *environment* decision:
    the default provider's names are allowed, and the malformed config surfaces where
    it is actually consumed (the worker, ``doctor``) with its own error.
    """
    try:
        from boardmodeler.config import load_config

        return load_config().agent_provider
    except Exception:  # pragma: no cover - a broken config is reported by its consumers
        return None


def _worker_env_allowlist() -> tuple[str, ...]:
    """Everything the worker child may inherit, by name, and nothing else.

    Three groups: the OS/interpreter basics above, the selected provider's documented
    key variables, and every ``BOARDMODELER_*`` variable currently set -- that prefix is
    this application's own namespace (``BOARDMODELER_CONFIG``, per-provider key
    fallbacks), so sweeping it is deliberate and bounded, unlike sweeping the
    environment.
    """
    names = [*_WORKER_ENV_BASE, *provider_env_names(_selected_provider_id())]
    names.extend(sorted(name for name in os.environ if name.startswith("BOARDMODELER_")))
    return tuple(dict.fromkeys(names))


@dataclass
class WorkerOutcome:
    """Everything observed about one worker run."""

    exit_code: int | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    stages: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    cancelled: bool = False
    malformed_lines: list[str] = field(default_factory=list)
    stderr: str = ""
    request_path: Path | None = None

    @property
    def ok(self) -> bool:
        """True when the worker exited 0 (statuses inside are data, not success)."""
        return self.exit_code == 0

    @property
    def status(self) -> str | None:
        return str(self.result["status"]) if self.result and "status" in self.result else None

    @property
    def summary(self) -> dict[str, int]:
        """Stage/test counts from the result, ignoring anything that is not a count.

        The payload arrives as JSON from another process, so a count that is not a number
        is a protocol defect in *data*. Dropping it keeps the window usable instead of
        raising inside a property the UI reads while it repaints.
        """
        if not self.result:
            return {}
        raw = self.result.get("summary", {})
        if not isinstance(raw, Mapping):
            return {}
        counts: dict[str, int] = {}
        for key, value in raw.items():
            try:
                counts[str(key)] = int(value)
            except TypeError, ValueError:
                continue
        return counts

    @property
    def results(self) -> list[dict[str, Any]]:
        if not self.result:
            return []
        raw = self.result.get("results", [])
        return [dict(item) for item in raw] if isinstance(raw, list) else []

    @property
    def completed(self) -> bool:
        return self.exit_code == 0 and (self.result is not None or self.error is not None)


def _jsonable(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return _jsonable(value.value)
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    enum_value = getattr(value, "value", None)
    if enum_value is not None and not isinstance(value, Mapping | list | tuple | set):
        return _jsonable(enum_value)
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(item) for item in value]
    return str(value)


def jsonable_request(request: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Normalise a request object (dataclass/pydantic/mapping) to JSON types."""
    if isinstance(request, Mapping):
        return {str(k): _jsonable(v) for k, v in request.items()}
    return dict(_jsonable(request))


def terminate_process_tree(pid: int, *, timeout_s: float = 5.0, reason: str = "") -> list[int]:
    """Terminate ``pid`` and every descendant; returns the PIDs targeted.

    Uses ``psutil`` when it is importable (deterministic in tests) and falls back to
    the execution policy's ``taskkill`` on Windows / process-group kill elsewhere.
    ``reason`` names what asked for the kill -- a cancel, the build deadline, an output
    flood -- and travels into the policy's spec name, so a kill that could not be
    performed says which deadline or cancel wanted it.
    """
    if pid <= 0:
        return []
    try:
        import psutil
    except Exception:  # pragma: no cover - psutil is a dev/optional dependency
        psutil = None  # type: ignore[assignment]

    if psutil is not None:
        try:
            parent = psutil.Process(pid)
        except psutil.Error:
            return []
        targets = []
        try:
            targets = parent.children(recursive=True)
        except psutil.Error:
            targets = []
        all_targets = [*targets, parent]
        for proc in all_targets:
            try:
                proc.terminate()
            except psutil.Error:
                continue
        _, alive = psutil.wait_procs(all_targets, timeout=timeout_s)
        for proc in alive:
            try:
                proc.kill()
            except psutil.Error:  # pragma: no cover - process already gone
                continue
        return [proc.pid for proc in all_targets]

    if os.name == "nt":  # pragma: no cover - exercised only without psutil
        if not _taskkill_tree(pid, timeout_s=timeout_s, reason=reason):
            # ``taskkill`` is the tool, not the only way to end this process: a stripped
            # image without it, or an environment it cannot start in, must not turn a
            # cancel into a no-op. Terminating the root directly loses the descendants,
            # which is worse than the tree kill and much better than nothing.
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)
        return [pid]
    try:  # pragma: no cover - LTspice/BoardModeler target Windows
        os.killpg(os.getpgid(pid), 9)
    except OSError:
        try:
            os.kill(pid, 9)
        except OSError:
            return []
    return [pid]


def _taskkill_tree(pid: int, *, timeout_s: float, reason: str) -> bool:
    """Run the guard-allowlisted ``taskkill`` through the execution policy.

    The bare name the old fallback spawned is gone: the policy refuses a relative
    executable because PATH would then choose the program, so the path is resolved from
    this machine's system folder and the argv is a literal template whose only value is
    the PID. A refusal is not raised at the caller -- this runs on the cancel/deadline
    path, where the process is already being torn down and a secondary exception would
    replace the reason the user needs with a worse one -- but it is *reported*: the
    caller falls back to terminating the root process directly, because a cancel that
    silently does nothing is worse than a cancel that loses the descendants.
    """
    scratch = Path(tempfile.gettempdir())
    try:
        kill_timeout = float(timeout_s)
    except TypeError, ValueError:
        kill_timeout = 0.0
    if not kill_timeout > 0:
        # The policy refuses a non-positive timeout, and this path is already tearing a
        # build down: a bad timeout becomes the default rather than a second failure.
        kill_timeout = 5.0
    tool = execution.system_executable("taskkill")
    spec = execution.CommandSpec(
        name=f"terminate-process-tree ({reason or 'cancelled'})",
        executable=tool,
        argv_tail=("/F", "/T", "/PID", execution.ANY_VALUE),
        timeout_s=kill_timeout,
        max_output_bytes=64 * 1024,
        # taskkill needs the OS basics to start at all: with an empty environment it exits
        # 1 with "the specified module could not be found" and kills nothing.
        env_allowlist=execution.WINDOWS_BASE_ENV,
        # The guard's allowlist carries the bare name it spawns through PATH; the pinned
        # path has the extension, so it is permitted explicitly here.
        extra_allowed_executables=(tool.name,),
    )
    try:
        return execution.run(spec, argv=(str(int(pid)),), cwd=scratch, root=scratch).ok
    except execution.CommandRefused:  # pragma: no cover - a stripped Windows image
        return False


class WorkerClient(QObject):
    """Runs one worker child process at a time and forwards its events."""

    stage = Signal(dict)
    progress = Signal(dict)
    findings = Signal(dict)
    review = Signal(dict)
    waveform = Signal(dict)
    result = Signal(dict)
    error = Signal(dict)
    resumed = Signal(dict)
    started = Signal(dict)
    stderr_text = Signal(str)
    exited = Signal(int)

    def __init__(
        self,
        *,
        python: Path | str | None = None,
        project_dir: Path | str | None = None,
        cancel_timeout_s: float = 5.0,
        controller_module: str | None = None,
        max_output_bytes: int = WORKER_MAX_OUTPUT_BYTES,
        parent: QObject | None = None,
    ) -> None:
        """``max_output_bytes`` is the stream cap the pumps enforce; the spec carries the
        same number, so the policy and the loop that owns the child agree on it.
        """
        super().__init__(parent)
        self._python = Path(python) if python is not None else Path(sys.executable)
        self._project_dir = Path(project_dir) if project_dir is not None else None
        try:
            self._cancel_timeout_s = float(cancel_timeout_s)
        except (TypeError, ValueError) as exc:
            # A kill timeout that is not a number would silently become a kill that
            # never happens; refuse it here, where the caller can see the mistake.
            raise ValueError(
                f"cancel_timeout_s must be a number of seconds, got {cancel_timeout_s!r}"
            ) from exc
        self._controller_module = controller_module
        self._proc: subprocess.Popen[str] | None = None
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._outcome = WorkerOutcome()
        self._last_request: dict[str, Any] | None = None
        self._last_project: Path | None = None
        self._workdir: Path | None = None
        self._finished = threading.Event()
        # The cap the pumps enforce; ``start()`` replaces it with the spec's value, which
        # is the one the policy checked. It exists here so no pump can ever run unbounded,
        # and it is checked here because the policy's own check happens later (a spec with
        # a bad cap would be refused, but the caller should learn that at construction).
        if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int):
            raise ValueError(f"max_output_bytes must be a byte count, got {max_output_bytes!r}")
        if max_output_bytes <= 0:
            raise ValueError(f"max_output_bytes must be positive, got {max_output_bytes!r}")
        self._max_output_bytes = max_output_bytes

    # ------------------------------------------------------------------ state

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def outcome(self) -> WorkerOutcome:
        """Observations so far; complete once :attr:`exited` has fired."""
        with self._lock:
            return self._outcome

    @property
    def last_request(self) -> dict[str, Any] | None:
        return dict(self._last_request) if self._last_request is not None else None

    @property
    def request_path(self) -> Path | None:
        return self._outcome.request_path

    # ------------------------------------------------------------------ start

    def start(
        self,
        request: Mapping[str, Any] | Any,
        *,
        project_dir: Path | str | None = None,
        deadline_s: float | None = None,
    ) -> Path:
        """Spawn the worker for ``request``; returns the written request path.

        The child is resolved by the execution policy before it exists: the spec pins
        this interpreter (its basename is permitted explicitly, its directory is the
        only directory it may come from), the request file and the project directory are
        pinned values, the environment is the explicit allowlist above, and
        ``deadline_s`` -- 8 hours unless the caller passes one -- is the hard deadline
        the watchdog enforces. A spawn that the policy refuses raises
        :class:`boardmodeler.security.execution.CommandRefused` with its stable code and
        reason, instead of an ``OSError`` from a failed spawn.
        """
        if self.is_running():
            raise RuntimeError("a worker run is already in progress")

        payload = jsonable_request(request)
        project = Path(project_dir) if project_dir is not None else self._project_dir
        if project is None:
            raw = payload.get("project_dir")
            project = Path(str(raw)) if raw else Path.cwd()
        # Resolved once, here: a relative spelling would otherwise mean "relative to
        # whatever directory the parent happens to stand in", and the worker is given
        # this same absolute path, so its own relative-path handling cannot drift from
        # the cwd it is started with.
        project = project.expanduser().resolve()
        if deadline_s is None:
            deadline = BUILD_DEADLINE_S
        else:
            try:
                deadline = float(deadline_s)
            except (TypeError, ValueError) as exc:
                # A deadline that is not a number must not become "no deadline": say so
                # here, where the caller can see which argument was wrong.
                raise ValueError(
                    f"deadline_s must be a number of seconds, got {deadline_s!r}"
                ) from exc

        workdir = Path(tempfile.mkdtemp(prefix="boardmodeler-ui-"))
        request_path = workdir / f"request-{uuid.uuid4().hex[:8]}.json"
        request_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        self._last_request = payload
        self._last_project = project
        self._workdir = workdir
        with self._lock:
            self._outcome = WorkerOutcome(request_path=request_path)
        self._finished.clear()

        python = Path(self._python)
        # One template and one tail, grown together: the template is what the policy
        # checks the tail against, so the two can never disagree about a position.
        template = [
            "-m",
            _WORKER_MODULE,
            "--request",
            execution.PATH_VALUE,
            "--project",
            execution.PATH_VALUE,
        ]
        tail = ["-m", _WORKER_MODULE, "--request", str(request_path), "--project", str(project)]
        if self._controller_module:
            template += ["--controller-module", execution.ANY_VALUE]
            tail += ["--controller-module", str(self._controller_module)]
        spec = execution.CommandSpec(
            name="worker-build",
            executable=python,
            argv_tail=tuple(template),
            timeout_s=deadline,
            max_output_bytes=self._max_output_bytes,
            env_allowlist=_worker_env_allowlist(),
            extra_allowed_executables=(python.name,),
            allowed_dirs=(python.parent,),
        )
        resolved = execution.validate(
            execution.CommandCall(spec=spec, argv=tuple(tail)),
            cwd=project,
            root=app_root(),
            # The project folder is the user's own choice and the request file lives in a
            # private scratch directory; naming both keeps the containment check on
            # instead of switching it off for the whole call.
            extra_roots=(workdir, project),
        )
        argv = [str(resolved.executable), *resolved.tail]
        self._proc = subprocess.Popen(
            argv,
            cwd=str(resolved.cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=False,
            env=resolved.env,
        )
        self._max_output_bytes = resolved.max_output_bytes
        self.started.emit(
            {"argv": argv, "request_path": str(request_path), "project": str(project)}
        )

        self._threads = [
            threading.Thread(target=self._pump_stdout, name="worker-stdout", daemon=True),
            threading.Thread(target=self._pump_stderr, name="worker-stderr", daemon=True),
            # The deadline is a watcher thread, not a pipe read: a build that is alive
            # but silent would otherwise never be stopped by anything.
            threading.Thread(
                target=self._watch_deadline,
                args=(self._proc, deadline),
                name="worker-deadline",
                daemon=True,
            ),
        ]
        for thread in self._threads:
            thread.start()
        return request_path

    def resume(self) -> bool:
        """Re-run the last request (the protocol allows resuming by re-running)."""
        if self.is_running() or self._last_request is None:
            return False
        payload = dict(self._last_request)
        self.resumed.emit(payload)
        self.start(payload, project_dir=self._last_project)
        return True

    # ------------------------------------------------------------------ cancel

    def cancel(self) -> bool:
        """Kill the worker's whole process tree; ``False`` when nothing runs."""
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return False
        with self._lock:
            self._outcome.cancelled = True
        terminate_process_tree(proc.pid, timeout_s=self._cancel_timeout_s)
        return True

    def wait(self, timeout_s: float | None = None) -> WorkerOutcome:
        """Block until the worker has exited (tests and shutdown paths)."""
        self._finished.wait(timeout_s)
        for thread in self._threads:
            thread.join(timeout=1.0)
        return self.outcome

    # ------------------------------------------------------------------ pumps

    def _pump_stream(
        self,
        proc: subprocess.Popen[str],
        stream: Any,
        name: str,
        on_line: Callable[[str], None],
    ) -> None:
        """Read one pipe in chunks and hand over complete lines, with a hard bound.

        ``for line in proc.stdout`` waits for a newline *or EOF*, so a child that writes a
        huge line with no newline in it is invisible -- and its bytes pile up inside the
        text layer -- until it exits. Reading the raw pipe in chunks makes every line
        arrive as soon as its bytes do (which is what the window needs) and makes the cap
        the only thing that can grow. Decoding is incremental because a multi-byte
        character can straddle two chunks.
        """
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        pending = ""
        seen = 0
        while True:
            try:
                chunk = stream.buffer.read1(_READ_CHUNK_BYTES)
            except OSError, ValueError:  # pragma: no cover - the pipe closed under us
                break
            if not chunk:
                break
            seen += len(chunk)
            if seen > self._max_output_bytes:
                self._stop_flooded_stream(proc, name, seen)
                break
            pending += decoder.decode(chunk)
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                on_line(line)
        pending += decoder.decode(b"", final=True)
        if pending:
            on_line(pending)

    def _pump_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:  # pragma: no cover - start() sets both
            return
        self._pump_stream(proc, proc.stdout, "stdout", self._handle_protocol_line)
        code = proc.wait()
        self._finalise(code)

    def _pump_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:  # pragma: no cover - start() sets both
            return
        self._pump_stream(proc, proc.stderr, "stderr", self._handle_stderr_line)

    def _handle_protocol_line(self, line: str) -> None:
        """One stdout line: a JSON event, or a recorded protocol violation."""
        text = line.strip()
        if not text:
            return
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            with self._lock:
                self._outcome.malformed_lines.append(text)
            self.stderr_text.emit(f"protocol violation (not JSON): {text}")
            return
        if not isinstance(event, dict) or "event" not in event:
            with self._lock:
                self._outcome.malformed_lines.append(text)
            self.stderr_text.emit(f"protocol violation (no event field): {text}")
            return
        self._dispatch(event)

    def _handle_stderr_line(self, line: str) -> None:
        """One stderr line: diagnostics, kept whole in the outcome."""
        text = line.rstrip("\r")
        with self._lock:
            self._outcome.stderr = (
                (self._outcome.stderr + text + "\n") if text else self._outcome.stderr
            )
        if text:
            self.stderr_text.emit(text)

    # ------------------------------------------------------------------ bounds

    def _watch_deadline(self, proc: subprocess.Popen[str], deadline_s: float) -> None:
        """Kill the build when it outlives its deadline.

        A backstop, not a normal path: a real build takes minutes and streams as it goes,
        and the deadline exists for the build that is alive but no longer progressing (a
        simulator run that never returns, a controller blocked on a socket). The detail
        names the deadline so a report can quote one number, and the kill goes through
        the policy's tree kill so the interpreter and everything it started go together.
        """
        if self._finished.wait(deadline_s):
            return
        event: dict[str, Any] = {
            "event": "error",
            "code": DEADLINE_EXCEEDED,
            "detail": (
                f"the worker did not finish within its {deadline_s:g} s build deadline; "
                "its process tree was killed"
            ),
            "deadline_s": deadline_s,
        }
        self._record_error(event)
        terminate_process_tree(proc.pid, timeout_s=self._cancel_timeout_s, reason=event["detail"])

    def _stop_flooded_stream(self, proc: subprocess.Popen[str], stream: str, seen: int) -> None:
        """Stop a child that floods a stream, and say so.

        The pumps keep every line they read, so an unbounded stream would grow this
        process's memory without limit. The spec's cap is enforced here, in the loop that
        owns the child; a build that has written more protocol text than any build
        produces is not going to become well-behaved, so its whole tree is killed and the
        partial capture is reported as a failure rather than as data.
        """
        event: dict[str, Any] = {
            "event": "error",
            "code": OUTPUT_TOO_LARGE,
            "detail": (
                f"the worker wrote more than {self._max_output_bytes} bytes to {stream} "
                f"(at least {seen}); its process tree was killed"
            ),
        }
        self._record_error(event)
        terminate_process_tree(proc.pid, timeout_s=self._cancel_timeout_s, reason=event["detail"])

    def _record_error(self, event: dict[str, Any]) -> None:
        """Record a client-side error event (deadline, flood) and emit it once.

        ``WorkerOutcome.error`` keeps the *first* error, so a build that already reported
        its own failure is not overwritten by the reason it was stopped; the client-side
        event is still emitted, because the window has to show why the process went away.
        """
        with self._lock:
            if self._outcome.error is None:
                self._outcome.error = event
        self.error.emit(event)

    def _dispatch(self, event: dict[str, Any]) -> None:
        kind = str(event.get("event", ""))
        with self._lock:
            self._outcome.events.append(event)
            if kind == "stage":
                self._outcome.stages.append(event)
            elif kind == "result":
                self._outcome.result = event
            elif kind == "error":
                self._outcome.error = event
        signal = getattr(self, EVENT_SIGNALS.get(kind, ""), None)
        if signal is not None:
            signal.emit(event)
        else:
            self.stderr_text.emit(f"protocol violation (unknown event kind {kind!r})")

    def _finalise(self, exit_code: int) -> None:
        with self._lock:
            self._outcome.exit_code = exit_code
            cancelled = self._outcome.cancelled or (
                self._outcome.error is not None
                and str(self._outcome.error.get("code")) == "cancelled"
            )
            self._outcome.cancelled = cancelled
        self._finished.set()
        self.exited.emit(exit_code if isinstance(exit_code, int) else -1)
