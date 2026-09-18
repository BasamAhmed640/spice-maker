"""Fetch the Phase 2 fixtures: TPS54320 datasheet + TI model packages (opt-in).

Run from the repository root:

```powershell
uv run python tools/fetch_fixtures.py --allow-network            # download + port
uv run python tools/fetch_fixtures.py --check                    # verify what is on disk
```

Design rules (plan A6/A7):

* Downloads are **local use only** and land under
  ``fixtures/regulator/<part>/originals/`` which is git-ignored. The committed
  artifact is ``provenance.json`` (URLs, HTTP status, sha256, retrieval date,
  licence note, ``redistribution_allowed: false``) and ``adaptation.json`` (the
  enumerated port changes) — never the vendor bytes themselves.
* Nothing is fetched without ``--allow-network``.
* A failed download is recorded as a failure with the observed error; the tool
  never substitutes a different file silently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from boardmodeler.models.adapt import port_pspice_to_ltspice

PART = "tps54320"
FIXTURE_ROOT = Path("fixtures") / "regulator" / PART
ORIGINALS = FIXTURE_ROOT / "originals"
ADAPTED = FIXTURE_ROOT / "adapted"
PROVENANCE = FIXTURE_ROOT / "provenance.json"
ADAPTATION = FIXTURE_ROOT / "adaptation.json"

USER_AGENT = "BoardModeler/0.1 (+fixture acquisition; local use only)"

DEFAULT_SOURCES: tuple[dict[str, str], ...] = (
    {
        "name": "tps54320_datasheet.pdf",
        "url": "https://www.ti.com/lit/ds/symlink/tps54320.pdf",
        "kind": "datasheet",
        "doc_id": "SLVS982C",
        "note": "Texas Instruments TPS54320 datasheet; downloaded for local reference only.",
    },
    {
        "name": "tps54320_pspice_transient.zip",
        "url": "https://www.ti.com/lit/zip/SLVM451",
        "kind": "vendor_model_package",
        "doc_id": "SLVM451A",
        "note": (
            "TPS54320 Unencrypted PSpice Transient Model Package (Rev. A). TI model terms: "
            "provided 'as is' for use with TI parts; not redistributed by BoardModeler."
        ),
    },
    {
        "name": "tps54320_pspice_average.zip",
        "url": "https://www.ti.com/lit/zip/SLVM896",
        "kind": "vendor_model_package",
        "doc_id": "SLVM896A",
        "note": "TPS54320 Unencrypted PSpice Average Model Package (Rev. A); local use only.",
    },
)

#: The transient model file inside the SLVM451A package.
MODEL_MEMBER = "TPS54320_PSPICE_TRANS/TPS54320_TRANS.lib"
MODEL_MODEL_ID = "tps54320_trans_ltspice"


@dataclass
class FetchRecord:
    """One attempted download and what actually happened."""

    name: str
    url: str
    kind: str
    doc_id: str
    status: int | None = None
    bytes: int | None = None
    sha256: str | None = None
    error: str | None = None
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.sha256 is not None


@dataclass
class Provenance:
    """Committed record of what was fetched, when, and under which terms."""

    part: str
    manufacturer: str
    retrieved_utc: str = ""
    tool: str = "tools/fetch_fixtures.py"
    redistribution_allowed: bool = False
    records: list[FetchRecord] = field(default_factory=list)
    adaptation: dict[str, object] | None = None
    notes: list[str] = field(default_factory=list)


def _fetch(url: str, *, timeout: float = 90.0) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def download(sources: tuple[dict[str, str], ...], *, timeout: float) -> list[FetchRecord]:
    ORIGINALS.mkdir(parents=True, exist_ok=True)
    records: list[FetchRecord] = []
    for source in sources:
        record = FetchRecord(
            name=source["name"],
            url=source["url"],
            kind=source["kind"],
            doc_id=source["doc_id"],
            note=source["note"],
        )
        try:
            status, data = _fetch(source["url"], timeout=timeout)
            record.status = status
            record.bytes = len(data)
            record.sha256 = hashlib.sha256(data).hexdigest()
            (ORIGINALS / source["name"]).write_bytes(data)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            record.error = f"{type(exc).__name__}: {exc}"
        records.append(record)
    return records


def check() -> list[FetchRecord]:
    """Verify what is already on disk against the committed provenance."""
    if not PROVENANCE.is_file():
        return []
    data = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    out: list[FetchRecord] = []
    for item in data.get("records", []):
        record = FetchRecord(**item)
        path = ORIGINALS / record.name
        if record.sha256 is None:
            record.error = record.error or "no sha256 recorded"
        elif not path.is_file():
            record.error = f"missing on disk: {path}"
        else:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != record.sha256:
                record.error = (
                    f"sha256 mismatch: on disk {actual[:16]}... vs recorded {record.sha256[:16]}..."
                )
        out.append(record)
    return out


def extract_and_port() -> tuple[dict[str, object] | None, list[str]]:
    """Extract the transient model and port it, returning the adaptation report."""
    package = ORIGINALS / "tps54320_pspice_transient.zip"
    if not package.is_file():
        return None, [f"{package} is not present; run with --allow-network first"]
    logs: list[str] = []
    try:
        with zipfile.ZipFile(package) as archive:
            members = archive.namelist()
            if MODEL_MEMBER not in members:
                return None, [f"{MODEL_MEMBER} not in the package; members are {members}"]
            raw = archive.read(MODEL_MEMBER)
    except (zipfile.BadZipFile, OSError) as exc:
        return None, [f"cannot read {package}: {type(exc).__name__}: {exc}"]

    model_dir = ORIGINALS
    original_path = model_dir / "TPS54320_TRANS.lib"
    original_path.write_bytes(raw)
    logs.append(f"extracted {MODEL_MEMBER} ({len(raw)} bytes) to {original_path}")

    text = raw.decode("utf-8", errors="replace")
    if "<Binary File>" in text:
        return None, [*logs, "the model is encrypted; no source-level port is possible"]

    result = port_pspice_to_ltspice(text)
    ADAPTED.mkdir(parents=True, exist_ok=True)
    adapted_path = ADAPTED / "TPS54320_TRANS_ltspice.lib"
    adapted_path.write_text(result.text, encoding="utf-8")
    report_path = ADAPTED / "TPS54320_TRANS_ltspice.adaptation.md"
    report_path.write_text(result.report.as_markdown(), encoding="utf-8")
    logs.append(
        f"ported {original_path.name} -> {adapted_path} with {len(result.report.changes)} change(s)"
    )
    logs.extend(f"warning: {w}" for w in result.warnings)
    payload = json.loads(result.report.model_dump_json())
    payload["source_file"] = original_path.name
    payload["adapted_file"] = str(adapted_path).replace("\\", "/")
    payload["model_id"] = MODEL_MODEL_ID
    return payload, logs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true", help="permit downloads")
    parser.add_argument("--check", action="store_true", help="verify what is already on disk")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.check:
        records = check()
        if not records:
            print("no committed provenance yet; run with --allow-network")
            return 1
        for record in records:
            state = "ok" if record.error is None else f"FAIL: {record.error}"
            print(f"{record.name:36} {state}")
        return 0 if all(r.error is None for r in records) else 1

    if not args.allow_network:
        print(
            "refusing to fetch without --allow-network (downloads are local-use only and are "
            "recorded in provenance.json)",
            file=sys.stderr,
        )
        return 2

    provenance = Provenance(
        part="TPS54320",
        manufacturer="Texas Instruments",
        retrieved_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        notes=[
            "Vendor documents and model packages are stored under originals/ (git-ignored) and are "
            "never redistributed by BoardModeler.",
            "The unencrypted PSpice transient model is adapted mechanically; every change is listed "
            "in adaptation.json.",
        ],
    )
    provenance.records = download(DEFAULT_SOURCES, timeout=args.timeout)
    adaptation, logs = extract_and_port()
    provenance.adaptation = adaptation

    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    PROVENANCE.write_text(
        json.dumps(
            {
                "part": provenance.part,
                "manufacturer": provenance.manufacturer,
                "retrieved_utc": provenance.retrieved_utc,
                "tool": provenance.tool,
                "redistribution_allowed": provenance.redistribution_allowed,
                "notes": provenance.notes,
                "records": [asdict(record) for record in provenance.records],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if adaptation is not None:
        ADAPTATION.write_text(json.dumps(adaptation, indent=2), encoding="utf-8")

    for record in provenance.records:
        if record.ok and record.sha256:
            state = f"HTTP {record.status}, {record.bytes} bytes, sha256 {record.sha256[:16]}..."
        else:
            state = f"FAILED: {record.error}"
        print(f"{record.name:36} {state}")
    for line in logs:
        print(f"  {line}")
    print(f"provenance -> {PROVENANCE}")
    if adaptation is not None:
        print(f"adaptation -> {ADAPTATION}")
    if args.json:
        print(json.dumps({"provenance": str(PROVENANCE), "adaptation": adaptation}, indent=2))
    return 0 if all(r.ok for r in provenance.records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
