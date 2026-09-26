r"""Run the cited TPS54332 PowerPAD card check and synthetic LTspice diagnostic.

All four paths are explicit. The model library must be a fresh generated file
under this checkout's ignored ``runs/`` directory. The two decks differ only
by the external PCB tie. Their 1 nA injection and alarm are test instruments,
not a device operating specification. The alarm is inside the model; the deck
only stimulates and observes it. No AI or network service is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

os.environ["BOARDMODELER_NO_NETWORK"] = "1"

from boardmodeler.domain.enums import Status
from boardmodeler.schematic.netlist import build_netmap
from boardmodeler.schematic.neutral import (
    ComponentRow,
    ConnectionRow,
    NeutralProject,
    to_circuit,
    write_neutral_project,
)
from boardmodeler.simulation.ltspice import BatchResult, run_batch
from boardmodeler.simulation.raw import read_raw
from boardmodeler.verification.required_connections import (
    RequiredConnectionRule,
    StaticConnectionResult,
    check_required_connection,
    evaluate_pad_alarm_waveform,
    load_required_connection_rule,
    render_pad_diagnostic_deck,
)

REPO = Path(__file__).resolve().parents[1]
RUNS = (REPO / "runs").resolve()
PART = "TPS54332DDA"
PCB_TIE = "Rpcb pad 0 1m\n"
FROZEN_REQUIREMENTS_SHA256 = "3884fd76976e071cd2ea81d5db64cb1bde17aecbaccc8f87399e7448646cc060"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ltspice-exe", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument(
        "--model-library",
        type=Path,
        required=True,
        help="fresh generated TPS54332 model library in this repository's runs/ directory",
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="new or empty directory under runs/"
    )
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument("--keep-raw", action="store_true")
    return parser.parse_args()


def _inputs(args: argparse.Namespace) -> tuple[Path, Path, Path, Path]:
    exe = args.ltspice_exe.resolve(strict=True)
    requirements = args.requirements.resolve(strict=True)
    model = args.model_library.resolve(strict=True)
    out = args.out.resolve()
    if not all(path.is_file() for path in (exe, requirements, model)):
        raise ValueError("LTspice, frozen requirements, and fresh model must be existing files")
    if not model.is_relative_to(RUNS):
        raise ValueError("fresh model library must be generated under this repository's runs/")
    if out == RUNS or not out.is_relative_to(RUNS):
        raise ValueError("output must be a new directory inside this repository's ignored runs/")
    if len(str(out)) > 130:
        raise ValueError("choose a shorter output path for LTspice on Windows")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError("output directory must be new or empty to exclude stale artifacts")
    if not math.isfinite(args.timeout_s) or args.timeout_s <= 0:
        raise ValueError("timeout must be positive and finite")
    return exe, requirements, model, out


def _card(rule: RequiredConnectionRule, pad_net: str) -> NeutralProject:
    return NeutralProject(
        components=[
            ComponentRow("U1", manufacturer="Texas Instruments", part_number=rule.part_number)
        ],
        connections=[
            ConnectionRow("U1", rule.ground_physical_pin, "DGND"),
            ConnectionRow("U1", rule.pad_physical_pin, pad_net),
        ],
        model_assignments={"U1": rule.subckt},
        nets=["DGND"],
    )


def _static_result(card: NeutralProject, rule: RequiredConnectionRule) -> StaticConnectionResult:
    physical_order = tuple(str(number) for number in range(1, len(rule.ports) + 1))
    netmap = build_netmap(to_circuit(card, symbols={"U1": physical_order}))
    return check_required_connection(card, rule, netmap=netmap)


def _static_json(result: StaticConnectionResult) -> dict[str, Any]:
    return {
        "status": result.status.value,
        "inspected_refdes": list(result.inspected_refdes),
        "requirement_id": result.requirement_id,
        "findings": [finding.model_dump(mode="json") for finding in result.findings],
    }


def _validate_static(
    clean: StaticConnectionResult, fault: StaticConnectionResult, rule: RequiredConnectionRule
) -> None:
    if clean.status != Status.PASS or clean.inspected_refdes != ("U1",) or clean.findings:
        raise RuntimeError("clean U1 card produced findings or did not pass the cited pin check")
    cited_faults = [
        finding
        for finding in fault.findings
        if finding.code == "RC001_required_pin_connection"
        and finding.refdes == "U1"
        and finding.detail.get("reason") == "pad_pin_disconnected"
        and finding.detail.get("ground_physical_pin") == "7"
        and finding.detail.get("pad_physical_pin") == "9"
        and finding.detail.get("ground_net") == "DGND"
        and finding.detail.get("pad_net") == "NC_09"
        and finding.detail.get("requirement_id") == rule.req_id
    ]
    if fault.status != Status.FAIL or fault.inspected_refdes != ("U1",) or len(cited_faults) != 1:
        raise RuntimeError("open-pad U1 card did not produce the exact cited pin/net finding")


def _artifact_hashes(out: Path) -> dict[str, str]:
    return {
        path.relative_to(out).as_posix(): _sha256(path)
        for path in sorted(out.rglob("*"))
        if path.is_file() and path.name != "result.json"
    }


def _artifact(out: Path, path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    resolved = path.resolve()
    if not resolved.is_relative_to(out):
        raise RuntimeError(f"simulator artifact is outside the output directory: {resolved}")
    if not resolved.is_file() or resolved.stat().st_size == 0:
        raise RuntimeError(f"simulator artifact is absent or empty: {resolved}")
    return {
        "path": resolved.relative_to(out).as_posix(),
        "bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _run_case(
    exe: Path, deck: Path, out: Path, timeout_s: float, rule: RequiredConnectionRule
) -> tuple[dict[str, Any], Path]:
    batch: BatchResult = run_batch(exe, deck, deck.parent, timeout_s=timeout_s)
    record: dict[str, Any] = {
        "exit_code": batch.exit_code,
        "timed_out": batch.timed_out,
        "cancelled": batch.cancelled,
        "wall_s": batch.wall_s,
        "stderr": batch.stderr[-2048:],
        "raw": _artifact(out, batch.raw_path),
        "log": _artifact(out, batch.log_path),
    }
    if not batch.ok or record["raw"] is None or record["log"] is None:
        raise RuntimeError(
            f"LTspice did not produce successful raw/log artifacts: {batch.observed()}"
        )
    raw_path = batch.raw_path
    assert raw_path is not None  # established by the artifact check above
    observation = evaluate_pad_alarm_waveform(read_raw(raw_path), rule)
    record["observation"] = asdict(observation)
    if observation.status != "MEASURED":
        raise RuntimeError(f"pad alarm waveform was not measurable: {observation.reason}")
    return record, raw_path


def _save(out: Path, result: dict[str, Any]) -> None:
    (out / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    args = _arguments()
    exe, requirements, model, out = _inputs(args)
    out.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "schema_version": 1,
        "part": PART,
        "status": "INCOMPLETE",
        "synthetic_diagnostic": True,
        "verdict_scope": "synthetic_powerpad_connection_diagnostic_only",
        "full_card_a_suite": "NOT_ASSESSED",
        "inputs": {
            "ltspice_exe": str(exe),
            "ltspice_exe_sha256": _sha256(exe),
            "requirements": str(requirements),
            "requirements_sha256": _sha256(requirements),
            "frozen_requirements_sha256": FROZEN_REQUIREMENTS_SHA256,
            "model_library": str(model),
            "model_library_sha256": _sha256(model),
            "script_sha256": _sha256(Path(__file__)),
        },
        "cards": {},
        "runs": {},
    }
    raw_paths: list[Path] = []
    try:
        rule = load_required_connection_rule(requirements)
        if (
            rule.part_number != PART
            or rule.ground_physical_pin != "7"
            or rule.pad_physical_pin != "9"
            or rule.source_sha256 != FROZEN_REQUIREMENTS_SHA256
        ):
            raise ValueError(
                "frozen B001 source hash or TPS54332DDA GND 7/PowerPAD 9 identity differs"
            )
        result["citation"] = asdict(rule)

        cards = {"clean": _card(rule, "DGND"), "fault": _card(rule, "NC_09")}
        checks = {name: _static_result(card, rule) for name, card in cards.items()}
        for name, card in cards.items():
            write_neutral_project(out / name / "card", card)
            result["cards"][name] = _static_json(checks[name])
        _validate_static(checks["clean"], checks["fault"], rule)

        clean_deck = render_pad_diagnostic_deck(rule, model, clean=True)
        fault_deck = render_pad_diagnostic_deck(rule, model, clean=False)
        if clean_deck.count(PCB_TIE) != 1 or clean_deck.replace(PCB_TIE, "", 1) != fault_deck:
            raise RuntimeError("diagnostic decks differ by more than the external PCB tie")
        internal_alarm = f"V(xu1:chk_{rule.pad_name.lower()})"
        if f".save V(pad) {internal_alarm}" not in clean_deck or "Bpad_alarm" in clean_deck:
            raise RuntimeError("diagnostic deck does not observe the model-internal pad alarm")
        for name, text in (("clean", clean_deck), ("fault", fault_deck)):
            deck = out / name / f"pad_{name}.cir"
            deck.write_text(text, encoding="utf-8", newline="\n")

        for name, expected_alarm in (("clean", False), ("fault", True)):
            deck = out / name / f"pad_{name}.cir"
            record, raw_path = _run_case(exe, deck, out, args.timeout_s, rule)
            result["runs"][name] = record
            raw_paths.append(raw_path)
            if record["observation"]["alarm_active"] is not expected_alarm:
                raise RuntimeError(f"{name} pad alarm polarity is wrong")

        if _sha256(requirements) != result["inputs"]["requirements_sha256"]:
            raise RuntimeError("frozen requirement source changed during the diagnostic")
        if _sha256(model) != result["inputs"]["model_library_sha256"]:
            raise RuntimeError("fresh model library changed during the diagnostic")
        result["artifact_hashes"] = _artifact_hashes(out)
        result["status"] = "DETECTED"
        result["raw_retained"] = args.keep_raw
        if not args.keep_raw:
            for raw_path in raw_paths:
                raw_path.unlink()
        _save(out, result)
    except Exception as exc:
        result["status"] = "FAILED"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["raw_retained"] = True
        result["artifact_hashes"] = _artifact_hashes(out)
        _save(out, result)
        raise
    print(json.dumps({"status": result["status"], "result": str(out / "result.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
