"""Judge a model .lib against a build's frozen spec with the product harness (read-only)."""

import json
import sys
import time
from collections import Counter
from pathlib import Path

from boardmodeler.authoring.harness import run_harness
from boardmodeler.authoring.spec import load_tps54320_spec

lib, spec_dir, part, work = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
spec = load_tps54320_spec(
    spec_dir / "requirements.json", spec_dir / "bindings.json", part=part, subckt=part
)
ltspice = Path(r"C:\Users\basam\AppData\Local\Programs\ADI\LTspice\LTspice.exe")
started = time.monotonic()
report = run_harness(
    model_lib=lib, subckt=part, spec=spec, workdir=work, ltspice=ltspice, timeout_s=120.0
)
elapsed = time.monotonic() - started
rows = []
for outcome in report.outcomes:
    for char_id in outcome.char_ids:
        rows.append((char_id, outcome.status, outcome.judged, outcome.unknown_reason or outcome.detail))
counts = Counter(status for _, status, _, _ in rows)
print(f"harness {elapsed:.1f} s; rows {dict(counts)}")
for char_id, status, judged, why in rows:
    print(f"{status:8} {char_id[:34]:34} {str(judged)[:34]:34} {str(why)[:110]}")
(work / "summary.json").write_text(
    json.dumps({"elapsed_s": elapsed, "counts": counts, "rows": rows}, indent=1), encoding="utf-8"
)
