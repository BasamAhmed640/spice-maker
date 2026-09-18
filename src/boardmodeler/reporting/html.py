"""Findings-first HTML report (§15 step 9).

Self-contained: no JavaScript, no external assets, no fonts to fetch. Waveforms are
drawn as inline SVG polylines downsampled to the plot width, and every violation
marker carries the requirement id that produced it, so a reader can go from a
spike on the screen to the requirement and the measured value behind it.

Order matters and is part of the contract: **findings first**, then results with
their measured values, then coverage (including what is *not* covered), then the
evidence levels and limitations, and only then the reproduction commands.
"""

from __future__ import annotations

import html
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Protocol

import numpy as np

from boardmodeler.domain.enums import Status
from boardmodeler.domain.records import Finding, ModelCapability, Requirement, TestResult

__all__ = ["ReportInputs", "ViolationMarker", "WaveformTrace", "render_report", "write_report"]

_SEVERITY_ORDER = {
    Status.FAIL: 0,
    Status.BLOCKED: 1,
    Status.UNKNOWN: 2,
    Status.NOT_APPLICABLE: 3,
    Status.PASS: 4,
}
_STATUS_CLASS = {
    Status.FAIL: "fail",
    Status.BLOCKED: "blocked",
    Status.UNKNOWN: "unknown",
    Status.NOT_APPLICABLE: "na",
    Status.PASS: "pass",
}


class _TraceSource(Protocol):  # pragma: no cover - documentation of the expected shape
    def column(self, name: str) -> np.ndarray: ...
    def time_column(self) -> np.ndarray | None: ...


@dataclass(frozen=True)
class ViolationMarker:
    """A point on the waveform where a requirement was violated or hit a limit."""

    requirement_id: str
    t_s: float
    label: str = ""
    status: Status = Status.FAIL


@dataclass(frozen=True)
class WaveformTrace:
    """One plotted signal."""

    name: str
    time_s: np.ndarray
    values: np.ndarray
    markers: tuple[ViolationMarker, ...] = ()


@dataclass
class ReportInputs:
    """Everything the report renders."""

    project_id: str
    project_name: str = ""
    status: Status = Status.UNKNOWN
    findings: Sequence[Finding] = ()
    results: Sequence[TestResult] = ()
    requirements: Sequence[Requirement] = ()
    traces: Sequence[WaveformTrace] = ()
    coverage: Mapping[str, object] = field(default_factory=dict)
    capability: ModelCapability | None = None
    limitations: Sequence[str] = ()
    qualifications: Sequence[str] = ()
    reproduction: Sequence[str] = ()
    generated_utc: str = ""
    tool_version: str = ""
    simulator: str = ""
    extra_sections: Mapping[str, str] = field(default_factory=dict)

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results:
            counts[result.status.value] = counts.get(result.status.value, 0) + 1
        return counts


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _sorted_findings(findings: Sequence[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda finding: (
            _SEVERITY_ORDER.get(finding.status, 5),
            finding.code,
            finding.refdes or "",
        ),
    )


def _svg_for(trace: WaveformTrace, *, width: int = 720, height: int = 220) -> str:
    time_axis = np.asarray(trace.time_s, dtype=float)
    values = np.asarray(trace.values, dtype=float)
    if time_axis.size < 2 or values.size != time_axis.size:
        return f'<p class="muted">signal {_escape(trace.name)} has no samples</p>'

    # Downsample to the plot width, keeping the extremes of every bucket so a
    # narrow spike cannot disappear from the picture.
    buckets = max(1, min(width, time_axis.size))
    edges = np.linspace(0, time_axis.size, buckets + 1).astype(int)
    xs: list[float] = []
    ys: list[float] = []
    for start, stop in pairwise(edges):
        if stop <= start:
            continue
        window_t = time_axis[start:stop]
        window_v = values[start:stop]
        xs.extend([float(window_t[0]), float(window_t[-1])])
        ys.extend([float(window_v.min()), float(window_v.max())])

    t_min, t_max = float(time_axis[0]), float(time_axis[-1])
    v_min, v_max = float(np.min(values)), float(np.max(values))
    if t_max <= t_min:
        t_max = t_min + 1e-12
    if v_max <= v_min:
        v_max = v_min + 1e-9

    def px(t: float) -> float:
        return (t - t_min) / (t_max - t_min) * (width - 2) + 1

    def py(v: float) -> float:
        return height - 1 - (v - v_min) / (v_max - v_min) * (height - 2)

    points = " ".join(f"{px(t):.2f},{py(v):.2f}" for t, v in zip(xs, ys, strict=True))
    markers = "".join(
        f'<line x1="{px(marker.t_s):.2f}" y1="0" x2="{px(marker.t_s):.2f}" y2="{height}" '
        f'class="marker {_STATUS_CLASS.get(marker.status, "unknown")}"/>'
        f"<title>{_escape(marker.requirement_id)} at {marker.t_s:g} s {_escape(marker.label)}</title>"
        for marker in trace.markers
        if t_min <= marker.t_s <= t_max
    )
    return (
        f'<figure class="wave">'
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'role="img" aria-label="waveform of {_escape(trace.name)}">'
        f'<rect x="0" y="0" width="{width}" height="{height}" class="plotbg"/>'
        f"{markers}"
        f'<polyline class="trace" points="{points}"/>'
        f"</svg>"
        f"<figcaption>{_escape(trace.name)} — {v_min:.4g} … {v_max:.4g} "
        f"over {t_min:g} … {t_max:g} s"
        f"{' (' + str(len(trace.markers)) + ' marker(s))' if trace.markers else ''}</figcaption>"
        f"</figure>"
    )


def _findings_table(findings: Sequence[Finding]) -> str:
    if not findings:
        return '<p class="muted">No findings were recorded.</p>'
    rows = []
    for finding in _sorted_findings(findings):
        nets = ", ".join(finding.nets) if finding.nets else ""
        detail = ", ".join(f"{key}={value}" for key, value in sorted(finding.detail.items()))
        rows.append(
            "<tr>"
            f'<td class="status {_STATUS_CLASS.get(finding.status, "unknown")}">{finding.status.value}</td>'
            f"<td><code>{_escape(finding.code)}</code></td>"
            f"<td>{_escape(finding.refdes or '')}</td>"
            f"<td>{_escape(nets)}</td>"
            f"<td>{_escape(finding.message)}</td>"
            f'<td class="muted">{_escape(detail)}</td>'
            "</tr>"
        )
    return (
        "<table><thead><tr><th>status</th><th>code</th><th>refdes</th><th>net(s)</th>"
        "<th>message</th><th>detail</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _results_table(results: Sequence[TestResult]) -> str:
    rows = []
    for result in sorted(results, key=lambda r: (_SEVERITY_ORDER.get(r.status, 5), r.test_id)):
        measured = "; ".join(
            f"{key}={value:.6g}" if isinstance(value, float) else f"{key}={value}"
            for key, value in sorted(result.measured.items())
        )
        rows.append(
            "<tr>"
            f'<td class="status {_STATUS_CLASS.get(result.status, "unknown")}">{result.status.value}</td>'
            f"<td><code>{_escape(result.test_id)}</code></td>"
            f"<td>{_escape(', '.join(result.requirement_ids))}</td>"
            f"<td>{_escape(result.expected)}</td>"
            f"<td>{_escape(measured)}</td>"
            f'<td class="muted">{_escape(result.detail)}</td>'
            "</tr>"
        )
    if not rows:
        return '<p class="muted">No tests were executed.</p>'
    return (
        "<table><thead><tr><th>status</th><th>test</th><th>requirements</th><th>expected</th>"
        "<th>measured</th><th>detail</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _coverage_section(coverage: Mapping[str, object]) -> str:
    if not coverage:
        return '<p class="muted">No coverage data was recorded.</p>'
    parts = ["<dl>"]
    for key in sorted(coverage):
        value = coverage[key]
        if isinstance(value, list):
            continue
        parts.append(f"<dt>{_escape(key)}</dt><dd>{_escape(value)}</dd>")
    parts.append("</dl>")
    for key in ("not_dynamically_covered", "non_verdict_requirements", "capability_gated"):
        entries = coverage.get(key)
        if not isinstance(entries, list) or not entries:
            continue
        parts.append(f"<h3>{_escape(key.replace('_', ' '))}</h3>")
        parts.append("<table><tbody>")
        for entry in entries:
            if isinstance(entry, dict):
                cells = "".join(
                    f"<td>{_escape(k)}={_escape(v)}</td>" for k, v in sorted(entry.items())
                )
                parts.append(f"<tr>{cells}</tr>")
        parts.append("</tbody></table>")
    return "\n".join(parts)


def _definitions(title: str, values: Sequence[str]) -> str:
    if not values:
        return f"<h2>{_escape(title)}</h2><p class='muted'>None recorded.</p>"
    items = "".join(f"<li>{_escape(value)}</li>" for value in values)
    return f"<h2>{_escape(title)}</h2><ul>{items}</ul>"


def _capability_section(capability: ModelCapability | None) -> str:
    if capability is None:
        return "<h2>Model capability</h2><p class='muted'>No capability record was produced.</p>"
    rows = "".join(
        f"<tr><td><code>{_escape(key)}</code></td><td>{_escape(state)}</td></tr>"
        for key, state in capability.behaviors.items()
    )
    return (
        "<h2>Model capability</h2>"
        f"<p>model <code>{_escape(capability.model_id)}</code>, kind "
        f"<code>{_escape(capability.kind.value)}</code>, evidence level "
        f"<code>{_escape(capability.evidence_level.value)}</code>, artifact sha256 "
        f"<code>{_escape(capability.source_model_hash)}</code></p>"
        "<table><thead><tr><th>behaviour</th><th>probe verdict</th></tr></thead><tbody>"
        f"{rows}</tbody></table>"
    )


_CSS = """
:root { color-scheme: light dark; }
body { font-family: system-ui, "Segoe UI", sans-serif; margin: 2rem auto; max-width: 1100px;
       line-height: 1.45; padding: 0 1rem; }
h1 { margin-bottom: .2rem; }
h2 { margin-top: 2rem; border-bottom: 1px solid currentColor; padding-bottom: .2rem; }
table { border-collapse: collapse; width: 100%; margin: .5rem 0 1rem; font-size: .92rem; }
th, td { border: 1px solid rgba(128,128,128,.45); padding: .3rem .5rem; text-align: left;
         vertical-align: top; }
th { background: rgba(128,128,128,.18); }
code { font-family: ui-monospace, Consolas, monospace; font-size: .9em; }
.muted { opacity: .75; }
.status { font-weight: 600; }
.status.pass { color: #157347; } .status.fail { color: #c0392b; }
.status.blocked { color: #9c27b0; } .status.unknown { color: #b8860b; }
.status.na { color: #607d8b; }
figure.wave { margin: 1rem 0; }
.plotbg { fill: rgba(128,128,128,.08); stroke: rgba(128,128,128,.35); }
polyline.trace { fill: none; stroke: #268bd2; stroke-width: 1.4; }
line.marker { stroke-width: 1.2; stroke-dasharray: 4 3; }
line.marker.fail { stroke: #c0392b; } line.marker.unknown { stroke: #b8860b; }
.banner { padding: .6rem .8rem; border-radius: .4rem; border: 1px solid currentColor; }
ul { margin: .3rem 0 1rem 1.2rem; }
"""


def render_report(inputs: ReportInputs) -> str:
    """Render the report as one self-contained HTML document."""
    counts = inputs.status_counts()
    summary = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    body: list[str] = []
    body.append("<!DOCTYPE html>")
    body.append('<html lang="en"><head><meta charset="utf-8">')
    body.append(
        f"<title>{_escape(inputs.project_name or inputs.project_id)} — BoardModeler report</title>"
    )
    body.append(f"<style>{_CSS}</style></head><body>")
    body.append(f"<h1>{_escape(inputs.project_name or inputs.project_id)}</h1>")
    body.append(
        f'<p class="banner status {_STATUS_CLASS.get(inputs.status, "unknown")}">'
        f"overall status: {inputs.status.value}"
        + (f" — {_escape(summary)}" if summary else "")
        + "</p>"
    )
    body.append(
        f'<p class="muted">project <code>{_escape(inputs.project_id)}</code>'
        + (f", simulator {_escape(inputs.simulator)}" if inputs.simulator else "")
        + (f", generated {_escape(inputs.generated_utc)}" if inputs.generated_utc else "")
        + (f", BoardModeler {_escape(inputs.tool_version)}" if inputs.tool_version else "")
        + "</p>"
    )

    body.append("<h2>Findings</h2>")
    body.append(_findings_table(inputs.findings))
    body.append("<h2>Results</h2>")
    body.append(_results_table(inputs.results))

    if inputs.traces:
        body.append("<h2>Waveforms</h2>")
        body.append(
            '<p class="muted">Dashed vertical markers are the points where a requirement was '
            "violated or reached its limit; hover a marker for its requirement id.</p>"
        )
        body.extend(_svg_for(trace) for trace in inputs.traces)

    body.append("<h2>Coverage</h2>")
    body.append(_coverage_section(inputs.coverage))
    body.append(_capability_section(inputs.capability))
    body.append(_definitions("Limitations and exclusions", inputs.limitations))
    body.append(_definitions("Qualifications", list(inputs.qualifications)))

    for title, section in inputs.extra_sections.items():
        body.append(f"<h2>{_escape(title)}</h2>{section}")

    body.append("<h2>Reproduction</h2>")
    if inputs.reproduction:
        body.append("<pre>" + _escape("\n".join(inputs.reproduction)) + "</pre>")
    else:
        body.append('<p class="muted">No reproduction commands were recorded.</p>')

    body.append("</body></html>")
    return "\n".join(body)


def write_report(path: str | Path, inputs: ReportInputs) -> Path:
    """Write the rendered report and return its path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_report(inputs), encoding="utf-8")
    return target


def traces_from_raw(
    raw: object,
    signals: Sequence[str],
    markers: Mapping[str, Sequence[ViolationMarker]] | None = None,
) -> list[WaveformTrace]:
    """Build plot traces from a ``RawFile`` (kept here so callers stay simple)."""
    source: _TraceSource = raw  # type: ignore[assignment]
    time_axis = source.time_column()
    if time_axis is None:
        return []
    markers = dict(markers or {})
    traces: list[WaveformTrace] = []
    for name in signals:
        try:
            values = source.column(name)
        except KeyError:
            continue
        traces.append(
            WaveformTrace(
                name=name,
                time_s=time_axis,
                values=values,
                markers=tuple(markers.get(name, ())),
            )
        )
    return traces


def load_results(path: str | Path) -> list[TestResult]:
    """Read ``results.json`` produced by the CLI or the export."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data["results"] if isinstance(data, dict) else data
    return [TestResult.model_validate(item) for item in items]
