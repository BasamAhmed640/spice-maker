"""Coverage matrix: what the support decision says for a spread of parts, and what real builds cost.

Run from the repository root with the project environment:

    uv run python tools/coverage_matrix.py --out docs/evidence/2026-09-28-engine-steps [--builds]

The decision rows need no simulator, no network and no provider. Rows typed in below use a part
number and a typical datasheet title (typed here, not read from a file), so they show how the gate
treats that wording, not what any particular datasheet says. Rows from local files read only the
title and the head of the first page of a PDF, and rows from frozen specs read the saved rows.
With --builds the two supported parts are built by the behavioural route in real LTspice, with the
agent backend and sockets disabled, and every stage is timed. Nothing here calls a provider.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from boardmodeler.authoring.spec import SpecSet
from boardmodeler.documents.pdf import read_pdf
from boardmodeler.models.support import ROUTES, SupportDecision, decide_support

NL = chr(10)

BLOCKED_CLASSES = (
    ("STM32F407VGT6", ""),
    ("ATmega328P", ""),
    ("PIC16F877A", ""),
    ("MSP430G2553", ""),
    ("RP2040", ""),
    ("ESP32-WROOM-32", ""),
    ("nRF52840", ""),
    ("ATSAMD21G18A-AU", ""),
    ("LPC1768", ""),
    ("TMS320F28335", ""),
    ("XC7A35T", ""),
    ("iCE40UP5K", ""),
    ("EP4CE6E22C8N", ""),
    ("LFE5U-25F", ""),
    ("10M08SAU169C8G", ""),
    ("ATF1502AS", "High Performance EE CPLD"),
    ("XC9572XL", ""),
    ("EPM240T100C5", ""),
    ("IMX6ULL", ""),
    ("BCM2711", ""),
    ("AM3358", "Sitara ARM Cortex-A8 processor"),
    ("RK3399", "Hexa-core application processor"),
    ("XC7Z020", "Zynq-7000 SoC"),
    ("ZX-1", "32-bit Arm Cortex-M4 microcontroller"),
    ("QX-9", "Field programmable gate array"),
)

UNIDENTIFIED = (
    ("QX-77A", ""),
    ("ZORBLAX", "Datasheet"),
    ("PN-0042", "Product specification rev C"),
)

# (part number, typical datasheet title): ordinary families the engine has no implementation for
ORDINARY_WITHOUT_IMPLEMENTATION = (
    ("AMS1117-3.3", "1A Low Dropout Voltage Regulator"),
    ("TPS7A47", "Low-Noise Low-Dropout Linear Regulator"),
    ("LM393", "Dual Differential Comparator"),
    ("TLV3201", "Single Channel Comparator"),
    ("MAX485", "RS-485 Transceiver"),
    ("SN65HVD72", "3.3-V RS-485 Transceiver"),
    ("TXB0104", "4-Bit Bidirectional Voltage-Level Translator"),
    ("SN74LVC1G00", "Single 2-Input Positive-NAND Gate"),
    ("TS5A3159", "Analog Switch"),
    ("TL431", "Adjustable Precision Shunt Regulator"),
    ("TPS3808", "Voltage Supervisor with Programmable Delay"),
    ("ICL7660", "Charge Pump Voltage Converter"),
    ("TPS22918", "Load Switch"),
    ("INA180", "Current-Sense Amplifier"),
    ("ISO7741", "Digital Isolator"),
    ("UCC27524", "Dual Gate Driver"),
    ("ADS1115", "16-Bit Analog-to-Digital Converter"),
    ("MCP4725", "12-Bit Digital-to-Analog Converter"),
    ("24LC256", "256K I2C EEPROM"),
    ("SIT8008", "Low Power MEMS Oscillator"),
    ("IRLZ44N", "Power MOSFET"),
    ("SMBJ5.0A", "TVS Diode"),
    ("1N4148", "Small Signal Switching Diode"),
    ("LT3080", "Adjustable Single Resistor Low Dropout Regulator"),
    ("LM2596", "Step-Down Switching Regulator"),
)

# local files: (file name, part number, folder relative to the home directory)
LOCAL_PDFS = (
    ("lm358_datasheet.pdf", "LM358", "src/.smsnap/general/inputs"),
    ("tps54332_datasheet.pdf", "TPS54332", "src/.smsnap/general/inputs"),
    ("tps54331_datasheet.pdf", "TPS54331", "src/.smsnap/r2/inputs"),
    ("datasheetchargepump.pdf", "XD7660", "Downloads"),
    ("C13755.pdf", "LM5116", "Downloads"),
    ("ESP32_WROOM_DEVKIT_V1.pdf", "ESP32-WROOM", "Desktop/PCB_PROJECTS"),
)

# frozen specs kept locally (the models folder is git-ignored)
FROZEN_SPECS = (
    ("LM358", "models/L1-lm358/spec/characteristics.json"),
    ("TPS54332DDA", "models/T1-tps54332/spec/characteristics.json"),
    ("TPS54320", "fixtures/regulator/tps54320/characteristics.json"),
)


@dataclass(frozen=True)
class Row:
    group: str
    part: str
    input: str
    decision: SupportDecision
    milliseconds: float


def _decide(part: str, **kwargs: object) -> tuple[SupportDecision, float]:
    started = time.perf_counter()
    decision = decide_support(part, **kwargs)  # type: ignore[arg-type]
    return decision, (time.perf_counter() - started) * 1000


def _routes_text(decision: SupportDecision) -> str:
    return " ".join(f"{route}={'yes' if decision.allows(route) else 'no'}" for route in ROUTES)


def typed_rows() -> list[Row]:
    rows: list[Row] = []
    for group, cases in (
        ("blocked class", BLOCKED_CLASSES),
        ("nothing identifies it", UNIDENTIFIED),
        ("ordinary family, no implementation", ORDINARY_WITHOUT_IMPLEMENTATION),
    ):
        for part, title in cases:
            decision, ms = _decide(part, title=title)
            label = f"number + typed title {title!r}" if title else "number only"
            rows.append(Row(group, part, label, decision, ms))
    return rows


def local_pdf_rows(home: Path) -> list[Row]:
    rows: list[Row] = []
    for name, part, folder in LOCAL_PDFS:
        path = home / folder / name
        if not path.is_file():
            continue
        document = read_pdf(path, max_pages=1)
        title = document.title or path.stem
        head = " ".join(document.pages[0].text.split())[:1500] if document.pages else ""
        decision, ms = _decide(part, title=title, head=head)
        rows.append(Row("local datasheet file", part, f"{name}: title + first page", decision, ms))
    return rows


def frozen_spec_rows(root: Path) -> list[Row]:
    rows: list[Row] = []
    for part, relative in FROZEN_SPECS:
        path = root / relative
        if not path.is_file():
            continue
        spec = SpecSet.from_json(path.read_text(encoding="utf-8"))
        decision, ms = _decide(part, title="", spec=spec)
        text = f"frozen rows ({len(spec.characteristics)} rows, {len(spec.covered())} bound)"
        rows.append(Row("frozen rows", part, text, decision, ms))
    return rows


def table(rows: list[Row]) -> str:
    head = "| Group | Part | Evidence given | State | Family | Implementation | Routes | ms |"
    lines = [head, "| --- | --- | --- | --- | --- | --- | --- | ---: |"]
    for row in rows:
        d = row.decision
        cells = (
            row.group,
            row.part,
            row.input,
            d.state,
            d.family or "-",
            d.implementation or "-",
            _routes_text(d),
            f"{row.milliseconds:.1f}",
        )
        lines.append("| " + " | ".join(cells) + " |")
    return NL.join(lines) + NL


def tally(rows: list[Row]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        out.setdefault(row.group, {}).setdefault(row.decision.state, 0)
        out[row.group][row.decision.state] += 1
    return out


PIN_KINDS = {
    "OUT1": ("output", "push_pull", "Output of amplifier 1"),
    "IN1M": ("input", "input_only", "Inverting input of amplifier 1"),
    "IN1P": ("input", "input_only", "Non-inverting input of amplifier 1"),
    "VEE": ("ground", "power", "Ground or negative supply"),
    "IN2P": ("input", "input_only", "Non-inverting input of amplifier 2"),
    "IN2M": ("input", "input_only", "Inverting input of amplifier 2"),
    "OUT2": ("output", "push_pull", "Output of amplifier 2"),
    "VCC": ("power", "power", "Positive supply"),
}


def _full_lm358_requirements(root: Path, target: Path) -> Path:
    """The frozen rows with the pin table written out in full (the file keeps names only)."""
    raw = json.loads((root / "models/L1-lm358/spec/requirements.json").read_text(encoding="utf-8"))
    raw["pin_map"] = [
        {
            "part_id": "LM358",
            "physical_pin": pin["physical_pin"],
            "name": pin["name"],
            "function": PIN_KINDS[pin["name"]][2],
            "polarity": "not_applicable",
            "direction": PIN_KINDS[pin["name"]][0],
            "output_topology": PIN_KINDS[pin["name"]][1],
            "connection_requirement": "required",
            "mapped_symbol_pin": pin["name"],
        }
        for pin in raw["pin_map"]
    ]
    target.write_text(json.dumps(raw), encoding="utf-8")
    return target


KEPT_FILES = (
    "run-timing.json",
    "support-decision.json",
    "model-design.json",
)


def real_builds(root: Path, datasheets: Path, work: Path, keep: Path) -> list[dict[str, object]]:
    """Build the supported parts by the behavioural route: real LTspice, no agent, no sockets."""
    from boardmodeler.pipeline import make_model as engine
    from boardmodeler.pipeline.make_model import MakeModelRequest, make_model
    from boardmodeler.simulation.ltspice import LtspiceInstall

    exe = Path(os.environ.get("LTSPICE_EXE", "") or "")
    if not exe.is_file():
        return [{"skipped": "set LTSPICE_EXE to the LTspice executable to run the real builds"}]

    def no_backend(request: object) -> object:
        raise AssertionError("the behavioural route must not construct an agent backend")

    def no_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("the behavioural route must not touch the network")

    engine.build_backend = no_backend  # type: ignore[assignment]
    engine.locate = lambda explicit=None: LtspiceInstall(exe, "coverage")  # type: ignore[assignment]
    socket.socket.connect = no_socket  # type: ignore[assignment]
    jobs = (
        ("TPS54332DDA", "tps54332_datasheet.pdf", root / "models/T1-tps54332/spec", None),
        ("LM358", "lm358_datasheet.pdf", root / "models/L1-lm358/spec", "lm358"),
    )
    records: list[dict[str, object]] = []
    for part, pdf, spec_dir, special in jobs:
        sheet = datasheets / pdf
        if not sheet.is_file() or not (spec_dir / "requirements.json").is_file():
            records.append({"part": part, "skipped": "frozen rows or datasheet not present"})
            continue
        requirements = spec_dir / "requirements.json"
        if special == "lm358":
            requirements = _full_lm358_requirements(root, work / "lm358-requirements.json")
        out = work / part
        started = time.monotonic()
        result = make_model(
            MakeModelRequest(
                part=part,
                subckt=part,
                datasheet=sheet,
                out_dir=out,
                backend_name="api",
                requirements_json=requirements,
                bindings_json=spec_dir / "bindings.json",
                timeout_s=120.0,
                engine="behavioral",
            )
        )
        wall = time.monotonic() - started
        kept = keep / part
        kept.mkdir(parents=True, exist_ok=True)
        for name in (*KEPT_FILES, f"{part}.lib"):
            if (out / name).is_file():
                shutil.copy2(out / name, kept / name)
        timing = json.loads((out / engine.TIMING_NAME).read_text(encoding="utf-8"))
        design = json.loads((out / engine.DESIGN_RECORD_NAME).read_text(encoding="utf-8"))
        records.append(
            {
                "part": part,
                "status": result.status,
                "counts": dict(result.counts),
                "wall_seconds": round(wall, 1),
                "stage_seconds": timing["stage_seconds"],
                "author_turns": timing["author_turns"],
                "provider_calls": 0,
                "route": timing["route"],
                "association": design["association"],
                "delivered_library_sha256": design["delivered_library_sha256"],
            }
        )
    return records


def builds_table(records: list[dict[str, object]]) -> str:
    head = (
        "| Part | Status | Rows | Wall s | read | extract | bind | gate | author | save "
        "| Agent turns | Provider calls |"
    )
    lines = [
        head,
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for rec in records:
        if "skipped" in rec:
            lines.append(f"| {rec.get('part', '-')} | skipped: {rec['skipped']} |" + " |" * 10)
            continue
        counts = rec["counts"]
        stages = rec["stage_seconds"]
        rows = " ".join(f"{k} {v}" for k, v in counts.items() if v)  # type: ignore[union-attr]
        cells = [str(rec["part"]), str(rec["status"]), rows, f"{rec['wall_seconds']}"]
        cells += [
            f"{stages.get(s, 0):.2f}" for s in ("read", "extract", "bind", "gate", "author", "save")
        ]  # type: ignore[union-attr]
        cells += [str(rec["author_turns"]), str(rec["provider_calls"])]
        lines.append("| " + " | ".join(cells) + " |")
    return NL.join(lines) + NL


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split(NL)[0])
    parser.add_argument(
        "--out", type=Path, required=True, help="folder for COVERAGE.md and coverage.json"
    )
    parser.add_argument(
        "--builds", action="store_true", help="also run the real behavioural builds"
    )
    parser.add_argument("--datasheets", type=Path, default=None, help="folder holding the TI PDFs")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    home = Path.home()
    datasheets = args.datasheets or home / "src/.smsnap/general/inputs"
    args.out.mkdir(parents=True, exist_ok=True)

    rows = typed_rows() + local_pdf_rows(home) + frozen_spec_rows(root)
    builds: list[dict[str, object]] = []
    if args.builds:
        work = Path(tempfile.mkdtemp(prefix="coverage-builds-"))
        builds = real_builds(root, datasheets, work, args.out / "builds")

    summary = tally(rows)
    slowest = max(row.milliseconds for row in rows)
    parts = [
        "# Coverage matrix",
        "",
        "What the support decision says for a spread of parts. Support means a positively matched"
        " behavioural implementation, every essential input cited and every essential behaviour"
        " independently tested; it is not a pass. Nothing below called a provider or the network.",
        "",
        f"{len(rows)} decisions, slowest {slowest:.1f} ms.",
        "",
        "## Tally by group and state",
        "",
    ]
    for group, states in summary.items():
        parts.append(
            f"- {group}: " + ", ".join(f"{state} {n}" for state, n in sorted(states.items()))
        )
    parts += ["", "## Decisions", "", table(rows)]
    if builds:
        parts += [
            "## Real behavioural builds (LTspice, no agent, no sockets)",
            "",
            builds_table(builds),
        ]
    (args.out / "COVERAGE.md").write_text(NL.join(parts) + NL, encoding="utf-8", newline="\n")
    payload = {
        "decisions": [
            {
                "group": row.group,
                "part": row.part,
                "evidence": row.input,
                "state": row.decision.state,
                "family": row.decision.family,
                "implementation": row.decision.implementation,
                "identified_from": row.decision.identified_from,
                "routes": {r: row.decision.allows(r) for r in ROUTES},
                "milliseconds": round(row.milliseconds, 2),
            }
            for row in rows
        ],
        "builds": builds,
    }
    (args.out / "coverage.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + NL, encoding="utf-8", newline="\n"
    )
    print(f"wrote {args.out / 'COVERAGE.md'} and coverage.json: {len(rows)} decisions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
