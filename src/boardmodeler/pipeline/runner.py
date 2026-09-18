"""Per-case deck execution (Phase 1 step 10).

Each case runs in its own ``runs/<run_id>/`` directory. The runner never decides
whether a result is good or bad — it produces :class:`RunArtifacts` (deck, hashes,
log, waveform, diagnostics, usability) and the engine judges it.

Blocked cases are first-class: when LTspice is unavailable, or the deck cannot be
written, the run is reported :data:`Status.BLOCKED` with the reason instead of
being skipped.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from boardmodeler.domain.hashing import sha256_file, sha256_text
from boardmodeler.domain.ids import run_id as make_run_id
from boardmodeler.domain.records import TestCase
from boardmodeler.simulation.limits import RunUsability, check_run_usable
from boardmodeler.simulation.log import LogSummary, parse_log
from boardmodeler.simulation.ltspice import BatchResult, LtspiceLockTimeout, run_batch
from boardmodeler.simulation.measures import RunDiagnostics, diagnose
from boardmodeler.simulation.raw import RawFile, RawFormatError, read_raw

__all__ = ["RunArtifacts", "RunContext", "build_deck_from_template", "run_case", "run_deck_tests"]


@dataclass
class RunContext:
    """Everything needed to execute a case for one project."""

    project_dir: Path
    ltspice: Path | None = None
    timeout_s: float = 120.0
    runs_dirname: str = "runs"
    ascii_raw: bool = False
    extra_switches: tuple[str, ...] = ()
    ltspice_lib_dir: Path | None = None
    #: When set, a read failure is reported verbatim instead of raising.
    max_dt_ratio: float = 0.05

    @property
    def runs_dir(self) -> Path:
        return Path(self.project_dir) / self.runs_dirname


@dataclass(frozen=True)
class RunArtifacts:
    """What a single case execution left behind."""

    run_id: str
    test_id: str
    scenario_id: str
    run_dir: Path
    deck_path: Path | None
    deck_text: str
    deck_sha256: str
    batch: BatchResult | None
    log: LogSummary
    raw: RawFile | None
    raw_error: str | None
    diagnostics: RunDiagnostics
    usability: RunUsability
    raw_sha256: str | None = None
    log_sha256: str | None = None
    blocked_reason: str | None = None
    detail: str = ""
    duration_s: float = 0.0
    cancelled: bool = False
    modifications: list[str] = field(default_factory=list)

    @property
    def has_waveform(self) -> bool:
        return self.raw is not None

    def waveform_ref(self, signal: str | None = None) -> str | None:
        """Relative reference to the waveform (optionally one signal)."""
        if self.raw is None or self.raw.path is None:
            return None
        try:
            rel = self.raw.path.relative_to(self.run_dir.parent.parent)
        except ValueError:
            rel = self.raw.path
        text = str(rel).replace("\\", "/")
        return f"{text}:{signal}" if signal else text

    def log_ref(self) -> str | None:
        if self.log.path is None:
            return None
        try:
            rel = self.log.path.relative_to(self.run_dir.parent.parent)
        except ValueError:
            rel = self.log.path
        return str(rel).replace("\\", "/")


def build_deck_from_template(case: TestCase, ctx: RunContext):
    """Default deck builder: copy the case's template into the run directory."""

    def _build(run_dir: Path) -> Path:
        template = Path(case.deck_template)
        if not template.is_absolute():
            template = Path(ctx.project_dir) / template
        if not template.is_file():
            raise FileNotFoundError(f"deck template not found for {case.test_id}: {template}")
        target = run_dir / "deck.cir"
        target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        return target

    return _build


def run_case(
    ctx: RunContext,
    case: TestCase,
    *,
    build_deck: Callable[[Path], Path] | None = None,
    run_identifier: str | None = None,
    cancel: threading.Event | None = None,
    modifications: Sequence[str] = (),
) -> RunArtifacts:
    """Execute one case and return its artifacts (never a verdict)."""
    started = time.monotonic()
    identifier = run_identifier or make_run_id(case.test_id)
    run_dir = ctx.runs_dir / identifier
    run_dir.mkdir(parents=True, exist_ok=True)

    builder = build_deck or build_deck_from_template(case, ctx)
    if cancel is not None and cancel.is_set():
        return _blocked_artifacts(
            identifier,
            case,
            run_dir,
            "cancelled",
            "cancellation was requested before the run started",
        )
    try:
        deck_path = builder(run_dir)
    except Exception as exc:
        return _blocked_artifacts(
            identifier,
            case,
            run_dir,
            "deck_not_built",
            f"the deck for {case.test_id} could not be built: {type(exc).__name__}: {exc}",
        )

    deck_text = deck_path.read_text(encoding="utf-8")
    deck_hash = sha256_text(deck_text)

    if ctx.ltspice is None:
        return _blocked_artifacts(
            identifier,
            case,
            run_dir,
            "simulator_unavailable",
            "no LTspice executable is configured or installed, so no simulation was attempted",
            deck_path=deck_path,
            deck_text=deck_text,
            deck_sha256=deck_hash,
            duration_s=time.monotonic() - started,
        )

    try:
        batch = run_batch(
            ctx.ltspice,
            deck_path,
            run_dir,
            timeout_s=ctx.timeout_s,
            extra_switches=ctx.extra_switches,
            ascii_raw=ctx.ascii_raw,
            cancel=cancel,
            ltspice_lib_dir=ctx.ltspice_lib_dir,
        )
    except LtspiceLockTimeout as exc:
        return _blocked_artifacts(
            identifier,
            case,
            run_dir,
            "simulator_busy",
            str(exc),
            deck_path=deck_path,
            deck_text=deck_text,
            deck_sha256=deck_hash,
            duration_s=time.monotonic() - started,
        )

    log = parse_log(batch.log_path) if batch.log_path else LogSummary(path=None)
    raw: RawFile | None = None
    raw_error: str | None = None
    if batch.raw_path is not None:
        try:
            raw = read_raw(batch.raw_path)
        except RawFormatError as exc:
            raw_error = str(exc)
        except OSError as exc:
            raw_error = f"{type(exc).__name__}: {exc}"

    diagnostics = diagnose(log=log, raw=raw, raw_error=raw_error)
    usability = check_run_usable(diagnostics, raw)

    blocked_reason = None
    if batch.cancelled:
        blocked_reason = "run_cancelled"
    elif not usability.usable and usability.blocked_reason:
        blocked_reason = usability.blocked_reason

    return RunArtifacts(
        run_id=identifier,
        test_id=case.test_id,
        scenario_id=case.scenario_id,
        run_dir=run_dir,
        deck_path=deck_path,
        deck_text=deck_text,
        deck_sha256=deck_hash,
        batch=batch,
        log=log,
        raw=raw,
        raw_error=raw_error,
        diagnostics=diagnostics,
        usability=usability,
        raw_sha256=sha256_file(batch.raw_path)
        if batch.raw_path and batch.raw_path.is_file()
        else None,
        log_sha256=sha256_file(batch.log_path)
        if batch.log_path and batch.log_path.is_file()
        else None,
        blocked_reason=blocked_reason,
        detail=batch.observed(),
        duration_s=time.monotonic() - started,
        cancelled=batch.cancelled,
        modifications=list(modifications),
    )


def _blocked_artifacts(
    identifier: str,
    case: TestCase,
    run_dir: Path,
    reason: str,
    detail: str,
    *,
    deck_path: Path | None = None,
    deck_text: str = "",
    deck_sha256: str = "",
    duration_s: float = 0.0,
) -> RunArtifacts:
    return RunArtifacts(
        run_id=identifier,
        test_id=case.test_id,
        scenario_id=case.scenario_id,
        run_dir=run_dir,
        deck_path=deck_path,
        deck_text=deck_text,
        deck_sha256=deck_sha256 or sha256_text(deck_text),
        batch=None,
        log=LogSummary(path=None),
        raw=None,
        raw_error=None,
        diagnostics=RunDiagnostics(),
        usability=RunUsability(usable=False, blocked_reason=reason, detail=detail),
        blocked_reason=reason,
        detail=detail,
        duration_s=duration_s,
    )


def run_deck_tests(
    ctx: RunContext,
    cases: Iterable[TestCase],
    cancel: threading.Event | None = None,
    *,
    deck_builder: Callable[[TestCase, Path], Path] | None = None,
    run_id_prefix: str | None = None,
) -> list[RunArtifacts]:
    """Run every case, each in its own ``runs/<run_id>/`` directory.

    Cancellation is checked between cases and passed into the batch runner, so a
    cancelled run is reported as BLOCKED rather than silently skipped.
    """
    results: list[RunArtifacts] = []
    for case in cases:
        if cancel is not None and cancel.is_set():
            break
        builder = None
        if deck_builder is not None:
            builder = lambda run_dir, _case=case: deck_builder(_case, run_dir)  # noqa: E731
        identifier = f"{run_id_prefix}_{case.test_id}" if run_id_prefix else None
        results.append(
            run_case(ctx, case, build_deck=builder, run_identifier=identifier, cancel=cancel)
        )
    return results
