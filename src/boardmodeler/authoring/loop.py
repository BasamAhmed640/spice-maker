"""The bounded author loop: the agent writes, the harness judges.

Honesty rule implemented here: nothing the authoring agent says is trusted. The
loop freezes ``spec/characteristics.json`` before the first turn, re-reads and
re-hashes it after *every* turn (any change aborts immediately with
``spec_tampered``, so an agent can neither relax a target nor edit the evidence),
and records ``PASS`` only when the harness — real LTspice runs, observed
``.raw``/``.log`` artifacts — reports passing probe outcomes for the files that
were actually written. A backend that cannot run yields ``BLOCKED`` with its own
reason; a missing model file, a cancelled build, or an exhausted iteration cap
yields ``UNKNOWN`` with the observed reason. Nothing here ever invents a run.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from boardmodeler.authoring.backends import AuthorBackend, AuthorRequest, AuthorResult
from boardmodeler.authoring.harness import HarnessReport, run_harness
from boardmodeler.authoring.spec import Characteristic, SpecSet
from boardmodeler.domain.enums import Status
from boardmodeler.domain.hashing import sha256_file

__all__ = [
    "AUTHOR_MAX_TURNS",
    "BuildOutcome",
    "BuildRequest",
    "build_model",
    "build_prompt",
    "model_file",
    "prepare_workdir",
    "required_ports_for",
    "spec_file",
]

SPEC_DIRNAME = "spec"
MODEL_DIRNAME = "model"
HARNESS_DIRNAME = "harness"
SPEC_FILENAME = "characteristics.json"
_PROMPT_FILENAME = "prompt.md"

AUTHOR_MAX_TURNS = 12
"""Internal turn budget handed to one agent invocation (the harness loop cap is separate)."""

_SPEC_README = """\
# Frozen specification

`characteristics.json` is the harness's copy of the datasheet characteristics the
model is judged against. It is frozen for the whole build:

- the harness re-reads and re-hashes this file after every authoring turn;
- if the file changes in any way the build stops immediately with
  `spec_tampered` and nothing is simulated;
- the harness owns the limits and tolerances. Relaxing a target here is not
  possible by construction.

Write the model to `../model/<subckt>.lib` (and `../model/<subckt>.asy`).
"""


def spec_file(workdir: Path) -> Path:
    """The frozen characteristic file the harness re-hashes after every turn."""
    return Path(workdir) / SPEC_DIRNAME / SPEC_FILENAME


def model_file(workdir: Path, subckt: str) -> Path:
    """The only model file the harness reads."""
    return Path(workdir) / MODEL_DIRNAME / f"{subckt}.lib"


def required_ports_for(spec: SpecSet) -> tuple[str, ...]:
    """The subcircuit ports the harness decks bind, in a deterministic order.

    The probe registry owns the order (registry order, first-seen). A registry
    that is unavailable, or a characteristic bound to a probe it does not have,
    degrades to an empty tuple: the prompt then asks for a self-consistent
    symbol instead of inventing an order, and the harness reports the binding
    defect itself when it runs.
    """
    try:
        from boardmodeler.authoring.probes import required_ports
    except ImportError:  # pragma: no cover - the registry ships with the harness
        return ()
    try:
        return tuple(required_ports(spec.covered()))
    except ValueError:  # an unknown probe binding is reported by the harness
        return ()


def _limits_text(characteristic: Characteristic) -> str:
    parts: list[str] = []
    for label, value in (
        ("min", characteristic.min_value),
        ("typ", characteristic.typ_value),
        ("max", characteristic.max_value),
    ):
        if value is not None:
            parts.append(f"{label} {value:g} {characteristic.unit}".strip())
    if characteristic.target is not None:
        parts.append(f"target {characteristic.target:g} {characteristic.unit}".strip())
    return ", ".join(parts) if parts else "no numeric limit recorded"


def _citation_text(characteristic: Characteristic) -> str:
    where = (
        f"pdf page {characteristic.source_page} (0-based)"
        if characteristic.source_page is not None
        else "page not recorded"
    )
    excerpt = " ".join(characteristic.excerpt.split())
    return f'{where}: "{excerpt}"' if excerpt else f"{where}: (no excerpt recorded)"


def _describe(characteristic: Characteristic) -> list[str]:
    probe = characteristic.probe or "none"
    lines = [
        f"[{characteristic.char_id}] probe `{probe}` — class {characteristic.req_class}",
        f"  requirement: {characteristic.statement}",
        f"  limits: {_limits_text(characteristic)}",
        f"  citation: {_citation_text(characteristic)}",
    ]
    if characteristic.probe_params:
        params = ", ".join(
            f"{key}={value:g}" for key, value in sorted(characteristic.probe_params.items())
        )
        lines.append(f"  probe parameters (set by the harness, not by you): {params}")
    return lines


def build_prompt(spec: SpecSet, subckt: str, harness_summary: str = "") -> str:
    """The authored instructions for one authoring session.

    The prompt states the required subcircuit name and port order, every covered
    characteristic with its limits and datasheet citation, and the rules: the
    harness owns the limits, ``spec/`` is frozen, and only a real LTspice run
    can produce a PASS. ``harness_summary`` is the harness's own feedback text
    from the previous turn.
    """
    ports = required_ports_for(spec)
    covered = spec.covered()
    uncovered = spec.uncovered()
    lines: list[str] = [
        f"# Author an LTspice model for {spec.part} ({subckt})",
        "",
        "You are the authoring agent for a reduced behavioural LTspice model of the part",
        f"{spec.part!r} (document {spec.doc_id!r}). A deterministic harness runs after every",
        "turn of yours: it simulates the files you wrote with real LTspice and compares the",
        "waveforms against the datasheet characteristics below. The harness decides PASS and",
        "FAIL — you never do, and nothing you report is taken as evidence.",
        "",
        f"## Deliverables (exactly two files, under {MODEL_DIRNAME}/)",
        f"1. `{MODEL_DIRNAME}/{subckt}.lib` — a single self-contained `.subckt {subckt} ...` block:",
        "   no `.include`, no absolute paths, no other external files.",
        f"2. `{MODEL_DIRNAME}/{subckt}.asy` — an LTspice symbol for it, with `PINATTR SpiceOrder`",
        "   entries matching your `.subckt` port order one-to-one.",
        "Anything else you write is ignored by the harness.",
        "",
        f"## The `{subckt}` ports you MUST declare",
    ]
    if ports:
        lines.extend(f"  - {port}" for port in ports)
        lines.append(
            "Those are the ports the harness binds by name; declare every one of them. Extra"
        )
        lines.append(
            "ports are allowed (the harness gives them a DC path). Declare the ports in the"
        )
        lines.append(
            "order your model wants: the `.subckt` port list is what counts, and the symbol's"
        )
        lines.append("`PINATTR SpiceOrder` entries must match it one-to-one.")
    else:
        lines.append(
            "  (the probe registry did not report a port list here — declare every port your"
        )
        lines.append("  model needs and keep the symbol's SpiceOrder consistent with the .subckt)")
    lines.extend(
        [
            "",
            "## How the harness judges",
            "- It runs real LTspice simulations in batch mode. A PASS requires a completed run",
            "  with readable output that answers the question for that characteristic.",
            "- A missing file, a convergence failure, an incomplete `.tran`, or a waveform that",
            "  cannot answer the question is UNKNOWN with the observed reason — never PASS.",
            "- The harness owns the limits, the tolerances and the measurement window. Do not",
            f"  attempt to change them: `{SPEC_DIRNAME}/` is frozen and any change to",
            f"  `{SPEC_DIRNAME}/{SPEC_FILENAME}` aborts the build immediately with `spec_tampered`.",
            "",
            f"## Characteristics the harness tests ({len(covered)})",
        ]
    )
    if covered:
        for characteristic in covered:
            lines.extend(_describe(characteristic))
            lines.append("")
    else:
        lines.append("  (none: no characteristic could be bound to a deterministic probe)")
        lines.append("")
    lines.append(f"## Characteristics that cannot be tested here ({len(uncovered)})")
    if uncovered:
        for characteristic in uncovered:
            reason = characteristic.not_testable_reason or "no deterministic probe available"
            lines.append(f"- [{characteristic.char_id}] {reason}")
            lines.append(f"  requirement: {characteristic.statement}")
            lines.append(f"  citation: {_citation_text(characteristic)}")
        lines.append(
            "These are reported as not testable with that reason. They must not be presented"
        )
        lines.append("as passing anywhere in your output.")
    else:
        lines.append("  (none)")
    lines.extend(
        [
            "",
            "## Testing it yourself",
            "The harness runs automatically at the end of every turn and its feedback is appended",
            "to this prompt. If the installed CLI offers it, you can also run the same harness on",
            "the files written so far with:",
            "",
            "    uv run boardmodeler model test --out <this working directory>",
            "",
            "## Rules",
            f"1. Write only `{MODEL_DIRNAME}/{subckt}.lib` and `{MODEL_DIRNAME}/{subckt}.asy`.",
            f"2. Never edit `{SPEC_DIRNAME}/`; the build aborts if it changes.",
            "3. Do not relax, reinterpret or delete a target, a limit or a requirement.",
            "4. Keep the model self-contained: the harness includes your file by absolute path.",
            "5. Report honestly in your notes: an untested behaviour stays untested.",
        ]
    )
    if harness_summary.strip():
        lines.extend(
            [
                "",
                "## Harness feedback so far",
                harness_summary.strip(),
                "",
                "Fix the measured failures above, then stop; the harness runs again automatically.",
            ]
        )
    return "\n".join(lines) + "\n"


def prepare_workdir(
    *, spec: SpecSet, subckt: str, workdir: Path, prompt: str | None = None
) -> None:
    """Lay out the agent sandbox: the frozen spec, the prompt, and the model directory.

    ``prompt`` defaults to :func:`build_prompt` so callers never duplicate the
    instructions the agent is judged against.
    """
    workdir = Path(workdir)
    spec_dir = workdir / SPEC_DIRNAME
    spec_dir.mkdir(parents=True, exist_ok=True)
    (workdir / MODEL_DIRNAME).mkdir(parents=True, exist_ok=True)
    text = build_prompt(spec, subckt) if prompt is None else prompt
    spec_dir.joinpath(SPEC_FILENAME).write_text(spec.to_json(), encoding="utf-8")
    spec_dir.joinpath("README.md").write_text(_SPEC_README, encoding="utf-8")
    workdir.joinpath(_PROMPT_FILENAME).write_text(text, encoding="utf-8")


@dataclass(frozen=True)
class BuildRequest:
    """Everything one build needs; the spec and the harness stay authoritative."""

    part: str
    subckt: str
    spec: SpecSet
    workdir: Path
    ltspice: Path
    backend: AuthorBackend
    max_iterations: int = 4
    timeout_s: float = 120.0

    def __post_init__(self) -> None:
        if not self.part or not self.subckt:
            raise ValueError("part and subckt must both be non-empty")
        if self.max_iterations < 1:
            raise ValueError(f"max_iterations must be >= 1, got {self.max_iterations}")
        if self.timeout_s <= 0:
            raise ValueError(f"timeout_s must be > 0, got {self.timeout_s}")


@dataclass(frozen=True)
class BuildOutcome:
    """The build's verdict: the harness report plus what was tried, in order."""

    status: str
    iterations: int
    report: HarnessReport
    history: tuple[str, ...]
    detail: str

    def to_json(self) -> str:
        try:
            report_payload: object = json.loads(self.report.to_json())
        except TypeError, ValueError:
            report_payload = self.report.to_json()
        payload = {
            "status": str(self.status),
            "iterations": int(self.iterations),
            "detail": self.detail,
            "history": list(self.history),
            "report": report_payload,
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> BuildOutcome:
        data = json.loads(text)
        report_data = data["report"]
        if isinstance(report_data, str):
            report = HarnessReport.from_json(report_data)
        else:
            report = HarnessReport.from_json(json.dumps(report_data, indent=2))
        return cls(
            status=str(data["status"]),
            iterations=int(data["iterations"]),
            report=report,
            history=tuple(str(entry) for entry in data.get("history", ())),
            detail=str(data["detail"]),
        )


def _load_frozen_spec(workdir: Path, spec: SpecSet) -> tuple[SpecSet | None, str, str | None]:
    """``(spec to judge with, its digest, problem)`` — the on-disk copy is the authority.

    A work directory that has not been frozen yet is frozen here, from the
    caller's spec, and the copy is verified to round-trip: the harness must be
    able to re-read exactly the characteristics the caller supplied, otherwise
    the build stops instead of judging against a different specification.
    """
    path = spec_file(workdir)
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(spec.to_json(), encoding="utf-8")
        frozen = _read_spec(path)
        if frozen is None:
            return None, "", f"spec_tampered: {path} could not be re-read after it was frozen"
        if frozen.digest() != spec.digest():
            return (
                None,
                frozen.digest(),
                f"spec_tampered: {path} does not round-trip through SpecSet JSON (digest mismatch)",
            )
        return frozen, frozen.digest(), None
    frozen = _read_spec(path)
    if frozen is None:
        return (
            None,
            "",
            f"spec_tampered: {path} is not a readable SpecSet",
        )
    digest = frozen.digest()
    if digest != spec.digest():
        return (
            None,
            digest,
            f"spec_tampered: {path} does not match the supplied specification (digest mismatch)",
        )
    return frozen, digest, None


def _read_spec(path: Path) -> SpecSet | None:
    try:
        return SpecSet.from_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _spec_tamper(workdir: Path, frozen: SpecSet) -> str | None:
    """Re-hash the frozen spec; a change is terminal and the harness never runs."""
    path = spec_file(workdir)
    if not path.is_file():
        return f"spec_tampered: {path} was removed after the work directory was prepared"
    try:
        current = SpecSet.from_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"spec_tampered: {path} is no longer a readable SpecSet ({type(exc).__name__})"
    if current.digest() != frozen.digest():
        return f"spec_tampered: {path} changed after the work directory was prepared"
    return None


def _report_without_runs(part: str, spec_digest: str, model_path: Path) -> HarnessReport:
    """A report with no outcomes: nothing was simulated, so nothing can pass."""
    try:
        digest = sha256_file(model_path)
    except OSError:
        digest = ""
    return HarnessReport(part=part, model_sha256=digest, spec_digest=spec_digest, outcomes=())


def _failing(report: HarnessReport) -> tuple[object, ...]:
    """The probes that stopped the build: the harness's ``failing()``, else any non-PASS."""
    failing = tuple(report.failing())
    if failing:
        return failing
    return tuple(outcome for outcome in report.outcomes if str(outcome.status) != Status.PASS.value)


def _names(outcomes: Iterable[object]) -> str:
    return ", ".join(f"{outcome.probe_id}={outcome.status}" for outcome in outcomes) or "none"


def _counts_text(report: HarnessReport) -> str:
    """Non-zero status counts in the enum's stable order (a zero count is noise)."""
    return (
        "{" + ", ".join(f"{key}: {value}" for key, value in report.counts().items() if value) + "}"
    )


def _feedback_text(report: HarnessReport) -> str:
    """The harness's own feedback, with a named fallback so a turn is never blind.

    ``HarnessReport.feedback()`` is empty when every bound characteristic passed
    and may also be empty for reports that cannot pass (a probe that never
    produced a FAIL block). The authoring agent still needs to know what is
    unresolved, so the probes are named explicitly in that case.
    """
    text = report.feedback().strip()
    if text:
        return text
    unresolved = _failing(report)
    if unresolved:
        return f"the harness did not pass; unresolved probes: {_names(unresolved)}"
    return "the harness did not pass and reported no outcome"


def _ltspice_executable(value: object) -> Path:
    """The executable path from a ``Path`` or from ``ltspice.locate()``'s install record."""
    return Path(getattr(value, "path", value))


def _outcome(
    status: Status,
    iterations: int,
    report: HarnessReport,
    history: list[str],
    detail: str,
) -> BuildOutcome:
    return BuildOutcome(
        status=status.value,
        iterations=iterations,
        report=report,
        history=tuple(history),
        detail=detail,
    )


def _author(request: BuildRequest, prompt: str, cancel: threading.Event | None) -> AuthorResult:
    """One backend turn; a raising backend becomes a recorded failure, not a crash."""
    author_request = AuthorRequest(
        prompt=prompt,
        workdir=Path(request.workdir),
        model_dir=Path(request.workdir) / MODEL_DIRNAME,
        max_turns=AUTHOR_MAX_TURNS,
    )
    try:
        return request.backend.author(author_request, cancel)
    except Exception as exc:
        return AuthorResult(
            ok=False,
            detail=f"backend_error: {type(exc).__name__}: {exc}",
            usage={},
            stdout_tail="",
            session_id=None,
        )


def build_model(request: BuildRequest, cancel: threading.Event | None = None) -> BuildOutcome:
    """Run the bounded author loop and return the harness's verdict.

    Bounded by ``request.max_iterations``. The spec is re-hashed after every
    turn, the model file must exist, and the harness is the only thing that can
    produce PASS. Exhausting the cap is UNKNOWN with the still-failing probes,
    never a pass by attrition.
    """
    workdir = Path(request.workdir)
    path = model_file(workdir, request.subckt)
    (workdir / MODEL_DIRNAME).mkdir(parents=True, exist_ok=True)
    history: list[str] = []

    frozen, digest, problem = _load_frozen_spec(workdir, request.spec)
    if frozen is None:
        report = _report_without_runs(request.part, digest, path)
        return _outcome(Status.UNKNOWN, 0, report, history, problem or "spec_tampered")

    usable, reason = request.backend.availability()
    if not usable:
        report = _report_without_runs(request.part, digest, path)
        return _outcome(Status.BLOCKED, 0, report, history, reason)

    harness_dir = workdir / HARNESS_DIRNAME
    report = _report_without_runs(request.part, digest, path)
    prompt = build_prompt(frozen, request.subckt)
    feedback_log: list[str] = []
    for turn in range(1, request.max_iterations + 1):
        if cancel is not None and cancel.is_set():
            history.append(f"turn {turn}: cancelled before the agent ran")
            return _outcome(
                Status.UNKNOWN, turn - 1, report, history, "cancelled: the build was cancelled"
            )
        authored = _author(request, prompt, cancel)
        if authored.detail.startswith("cancelled"):
            history.append(f"turn {turn}: {authored.detail}")
            return _outcome(Status.UNKNOWN, turn, report, history, authored.detail)
        tamper = _spec_tamper(workdir, frozen)
        if tamper is not None:
            history.append(f"turn {turn}: {authored.detail}; {tamper}")
            return _outcome(Status.UNKNOWN, turn, report, history, tamper)
        if not path.is_file():
            history.append(f"turn {turn}: {authored.detail}; model_file_missing ({path})")
            missing = f"model_file_missing: {path} does not exist after turn {turn}"
            return _outcome(
                Status.UNKNOWN,
                turn,
                report,
                history,
                missing if authored.ok else f"{authored.detail}; {missing}",
            )
        try:
            report = run_harness(
                model_lib=path,
                subckt=request.subckt,
                spec=frozen,
                workdir=harness_dir,
                ltspice=_ltspice_executable(request.ltspice),
                timeout_s=request.timeout_s,
                cancel=cancel,
            )
        except Exception as exc:
            history.append(
                f"turn {turn}: {authored.detail}; harness_error: {type(exc).__name__}: {exc}"
            )
            return _outcome(
                Status.UNKNOWN,
                turn,
                report,
                history,
                f"harness_error: {type(exc).__name__}: {exc}",
            )
        history.append(
            f"turn {turn}: {authored.detail}; harness {_counts_text(report)} "
            f"{_names(_failing(report))}"
        )
        if report.outcomes and report.passed():
            return _outcome(
                Status.PASS,
                turn,
                report,
                history,
                f"harness PASS after {turn} turn(s): every covered characteristic passed",
            )
        if turn == request.max_iterations:
            break
        feedback_log.append(_feedback_text(report))
        prompt = build_prompt(frozen, request.subckt, "\n\n".join(feedback_log))

    unresolved = _failing(report)
    detail = (
        f"max_iterations={request.max_iterations} exhausted; still failing: {_names(unresolved)}"
        if unresolved
        else f"max_iterations={request.max_iterations} exhausted without a passing harness report"
    )
    return _outcome(Status.UNKNOWN, request.max_iterations, report, history, detail)
