"""Probe a model family and write its capability record.

```powershell
uv run python tools/probe_models.py --vendor          # ported TI TPS54320 model
uv run python tools/probe_models.py --behavior startup --vendor   # one behaviour
```

Writes ``fixtures/regulator/tps54320/capability_vendor.json`` (committed): the
``ModelCapability`` record plus the per-probe ``TestResult`` list. The probe
verdicts come from real LTspice runs — nothing here is hand-written.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from boardmodeler.domain.enums import EvidenceLevel, ModelKind
from boardmodeler.models.capability import ModelProbeSpec, probe_model
from boardmodeler.models.library import subckt_name, subckt_ports
from boardmodeler.pipeline.runner import RunContext
from boardmodeler.simulation.ltspice import locate

FIXTURE = Path("fixtures") / "regulator" / "tps54320"
VENDOR_MODEL = FIXTURE / "adapted" / "TPS54320_TRANS_ltspice.lib"


def vendor_spec() -> ModelProbeSpec:
    text = VENDOR_MODEL.read_text(encoding="utf-8")
    subckt = subckt_name(text) or "TPS54320_TRANS"
    ports = subckt_ports(text)
    # Every package pad gets an explicit role: duplicated power pads tie to the
    # same net, the exposed pad to ground, and the control supply to the input
    # rail (as the EVM application does).
    port_roles = {
        "BOOT": "boot",
        "COMP": "comp",
        "EN": "en",
        "ETPad": "gnd",
        "GND_1": "gnd",
        "GND_2": "gnd",
        "PH_1": "sw",
        "PH_2": "sw",
        "PVIN_1": "vin",
        "PVIN_2": "vin",
        "PWRGD": "pg",
        "RT_CLK": "rt",
        "SS_TR": "ss",
        "VIN": "vin",
        "VSENSE": "fb",
    }
    unknown = [port for port in port_roles if port not in ports]
    if unknown:
        raise SystemExit(f"vendor model has no ports {unknown}; ports are {ports}")
    return ModelProbeSpec(
        model_id="tps54320_vendor_ltspice",
        kind=ModelKind.VENDOR_PIN_COMPATIBLE,
        path=VENDOR_MODEL,
        subckt=subckt,
        ports=tuple(ports),
        port_roles=port_roles,
        nets={"gnd": "0", "vout": "n_vout", "vin": "n_vin", "sw": "n_sw"},
        nominal_vout=3.3,
        vref=0.8,
        rfbt=10e3,
        rfbb=3.24e3,
        vin=12.0,
        load_ohm=3.3,
        ilim=3.0,
        # Package pads are tied inside `port_roles` (PVIN_2 -> vin, GND_2 -> gnd,
        # PH_2 -> sw, ETPad -> gnd, VIN -> vin): the datasheet ties the control
        # supply to the input rail in its own application circuit.
        extra_elements=(),
        # The transistor-level vendor model costs ~30 s of wall time per millisecond
        # simulated; the probe windows are all relative, so shortening them keeps
        # the criteria valid.
        probe_time_scale=0.35,
        notes=[
            "ported from the TI unencrypted PSpice transient model (SLVM451A) with "
            "models/adapt.py; see adaptation.json for the enumerated changes",
            "decks must not use .tran ... uic: LTspice has to solve the operating point first",
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", action="store_true", help="probe the ported vendor model")
    parser.add_argument("--behavior", action="append", default=None, help="limit to a behaviour")
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not args.vendor:
        parser.error("nothing to probe: pass --vendor")
    if not VENDOR_MODEL.is_file():
        print(
            f"{VENDOR_MODEL} is missing; run tools/fetch_fixtures.py --allow-network",
            file=sys.stderr,
        )
        return 2

    install = locate()
    if install is None:
        print("LTspice was not found; probing is impossible", file=sys.stderr)
        return 2

    spec = vendor_spec()
    ctx = RunContext(
        project_dir=FIXTURE / "probe_project",
        ltspice=install.path,
        timeout_s=args.timeout,
    )
    workdir = FIXTURE / "probe_project" / "probes"
    report = probe_model(
        spec,
        ctx,
        workdir=workdir,
        behaviors=args.behavior,
        evidence_level=EvidenceLevel.VENDOR_MODEL_COMPARED,
        exclusions=[
            "switch-node ripple and gate-drive detail are not claimed beyond the probe evidence",
            "thermal dependence is not modelled",
        ],
    )

    print(f"{'behaviour':24} {'probe':14} detail")
    for behavior, outcome in report.outcomes.items():
        print(f"{behavior:24} {outcome.status:14} {outcome.detail[:110]}")

    payload = {
        "model": json.loads(report.capability.model_dump_json()),
        "results": [json.loads(result.model_dump_json()) for result in report.results],
        "probe_source": "tools/probe_models.py (real LTspice runs)",
    }
    out = args.out or (FIXTURE / "capability_vendor.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"capability -> {out}")
    if args.json:
        print(json.dumps(payload, indent=2))
    supported = report.supported
    print(f"supported: {supported}")
    print(
        f"gate for startup-dependent requirements: {len(report.gate({'REQ_X': 'startup'}))} blocked"
    )
    return 0 if "startup" in supported else 1


if __name__ == "__main__":
    raise SystemExit(main())
