"""End-to-end tests of the resolved LTspice batch invocation (Phase 0 step 6).

These tests are the evidence for the invocation recorded in
``simulation/ltspice.py`` and ``docs/DECISIONS.md`` (D-006): they fail if the
argv order, the output discovery, or the ``-b``/``-netlist`` semantics change.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from boardmodeler.simulation.log import parse_log
from boardmodeler.simulation.ltspice import (
    SMOKE_EXPECTED_V,
    SMOKE_TOLERANCE_PCT,
    netlist_step,
    run_batch,
    smoke_test,
)
from boardmodeler.simulation.raw import read_raw

pytestmark = pytest.mark.ltspice


def test_smoke_test_passes_with_observed_values(ltspice_exe: Path, tmp_path: Path) -> None:
    result = smoke_test(ltspice_exe, tmp_path / "smoke")
    assert result.status == "pass", result.detail
    assert result.measured_v is not None
    assert abs(result.measured_v - SMOKE_EXPECTED_V) / SMOKE_EXPECTED_V * 100 < SMOKE_TOLERANCE_PCT
    # An artifact hash is required before any run may be called a pass.
    assert result.raw_sha256 and len(result.raw_sha256) == 64
    assert result.log_sha256 and len(result.log_sha256) == 64
    assert result.log_meas_v is not None
    assert abs(result.log_meas_v - result.measured_v) < 2e-3
    assert result.reader_layout == "double+float32"
    assert result.header_encoding == "utf-16-le"
    assert result.version == "26.0.0"
    assert result.as_dict()["smoke_test"] == "pass"


def test_smoke_test_reports_failure_for_missing_executable(tmp_path: Path) -> None:
    """A forced-bad LTSPICE_EXE must produce fail + non-empty detail, not a crash."""
    result = smoke_test(tmp_path / "does_not_exist.exe", tmp_path / "smoke")
    assert result.status == "fail"
    assert result.detail.strip()
    assert result.measured_v is None


def test_run_batch_argv_and_artifacts(ltspice_exe: Path, tmp_path: Path) -> None:
    deck = tmp_path / "rc.cir"
    deck.write_text(
        "* rc\nV1 in 0 PULSE(0 1 0 1n 1n 1 2)\nR1 in out 1k\nC1 out 0 1u\n.tran 1m\n.end\n",
        encoding="utf-8",
    )
    result = run_batch(ltspice_exe, deck, tmp_path, timeout_s=60)
    assert result.exit_code == 0, result.observed()
    assert not result.timed_out
    assert result.raw_path is not None and result.raw_path.is_file()
    assert result.log_path is not None and result.log_path.is_file()
    # Resolved invocation: -b immediately after the executable, deck last.
    assert result.argv[0].endswith("LTspice.exe")
    assert result.argv[1] == "-b"
    assert result.argv[-1] == str(deck.resolve())
    assert "-I" not in " ".join(result.argv)

    raw = read_raw(result.raw_path)
    time_axis = raw.time_column()
    assert time_axis is not None
    measured = float(np.interp(1e-3, time_axis, raw.column("V(out)")))
    assert abs(measured - 0.6321) < 0.002


def test_run_batch_reports_failure_for_bad_deck(ltspice_exe: Path, tmp_path: Path) -> None:
    """A deck that cannot run must never look like a run that did."""
    deck = tmp_path / "bad.cir"
    deck.write_text(
        "* missing subcircuit\nX1 a 0 NOT_A_REAL_SUBCKT\nV1 a 0 1\n.tran 1m\n.end\n",
        encoding="utf-8",
    )
    result = run_batch(ltspice_exe, deck, tmp_path, timeout_s=60)
    assert result.exit_code != 0
    assert not result.ok
    assert result.log_path is not None
    summary = parse_log(result.log_path)
    assert not summary.completed
    assert summary.errors, summary.text
    assert any("NOT_A_REAL_SUBCKT" in e or "sub-circuit" in e.lower() for e in summary.errors)


def test_stale_log_does_not_make_the_watchdog_kill_a_fresh_run(
    ltspice_exe: Path, tmp_path: Path
) -> None:
    """A previous run's log must not be read as this run's completion marker.

    The watchdog treats "Total elapsed time" in the log as "the simulation
    finished, the process should exit now". A stale log from an earlier run in the
    same directory satisfies that test, so the loader has to purge it first —
    otherwise a slow run is killed five seconds in and reported as failed.
    """
    deck = tmp_path / "slow.cir"
    # ~1.5 s of simulated time with a 2 us step: slow enough that the grace period
    # would expire well before the run finishes if a stale marker were honoured.
    deck.write_text(
        "* slow deck\nV1 in 0 PULSE(0 1 0 1n 1n 1 2)\nR1 in out 1k\nC1 out 0 1u\n"
        ".tran 0 1.5 0 2u\n.end\n",
        encoding="utf-8",
    )
    stale_log = tmp_path / "slow.log"
    stale_log.write_text("Total elapsed time: 0.001 seconds.\n", encoding="utf-8")
    stale_raw = tmp_path / "slow.raw"
    stale_raw.write_bytes(b"stale")

    result = run_batch(ltspice_exe, deck, tmp_path, timeout_s=120)
    assert not result.terminated_after_marker, result.observed()
    assert not result.timed_out
    assert result.exit_code == 0, result.observed()
    assert result.raw_path is not None
    assert result.raw_path.read_bytes() != b"stale"
    assert result.log_path is not None
    assert "Total elapsed time" in result.log_path.read_text(encoding="utf-8", errors="replace")


def test_run_batch_missing_deck_raises(ltspice_exe: Path, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_batch(ltspice_exe, tmp_path / "nope.cir", tmp_path, timeout_s=10)


def test_batch_simulates_asc_directly(ltspice_exe: Path, tmp_path: Path) -> None:
    """Resolved: `-b <deck>.asc` simulates directly; no `-netlist` pre-step needed."""
    work = tmp_path / "asc"
    work.mkdir()
    (work / "divider.asc").write_text(
        """Version 4
SHEET 1 880 680
WIRE 112 192 208 192
WIRE 112 272 208 272
FLAG 112 272 0
FLAG 208 272 0
SYMBOL voltage 112 176 R0
SYMATTR InstName V1
SYMATTR Value 1
SYMBOL res 192 176 R0
SYMATTR InstName R1
SYMATTR Value 1k
TEXT 112 384 Left 2 !.tran 1m
""",
        encoding="utf-8",
    )
    result = run_batch(ltspice_exe, work / "divider.asc", work, timeout_s=60)
    assert result.exit_code == 0, result.observed()
    assert result.raw_path is not None and result.raw_path.is_file()
    raw = read_raw(result.raw_path)
    assert raw.has("V(N001)") or any(v.startswith("V(") for v in raw.variables)


def test_netlist_step_emits_expected_netlist(ltspice_exe: Path, tmp_path: Path) -> None:
    work = tmp_path / "net"
    work.mkdir()
    (work / "divider.asc").write_text(
        """Version 4
SHEET 1 880 680
WIRE 112 192 208 192
WIRE 112 272 208 272
FLAG 112 272 0
FLAG 208 272 0
SYMBOL voltage 112 176 R0
SYMATTR InstName V1
SYMATTR Value 1
SYMBOL res 192 176 R0
SYMATTR InstName R1
SYMATTR Value 1k
TEXT 112 384 Left 2 !.tran 1m
""",
        encoding="utf-8",
    )
    result = netlist_step(ltspice_exe, work / "divider.asc", timeout_s=60)
    assert result.exit_code == 0, result.observed()
    assert result.net_path is not None and result.net_path.is_file()
    text = result.net_path.read_text(encoding="utf-8", errors="replace")
    assert "V1" in text and "R1" in text
    assert ".tran 1m" in text
    assert "V1 N001 0 1" in text, text
    assert "R1 N001 0 1k" in text, text


def test_local_symbol_and_model_resolve_without_search_path(
    ltspice_exe: Path, tmp_path: Path
) -> None:
    """Generated symbols/libs beside the schematic resolve without the unsafe -I."""
    work = tmp_path / "local"
    work.mkdir()
    (work / "bmtest.asy").write_text(
        """Version 4
SymbolType CELL
RECTANGLE Normal -48 -32 48 32
SYMATTR Prefix X
SYMATTR Value BM_TEST
SYMATTR SpiceModel bm_test.lib
SYMATTR Value2 BM_TEST
PIN -64 0 LEFT 8
PINATTR PinName A
PINATTR SpiceOrder 1
PIN 64 0 RIGHT 8
PINATTR PinName B
PINATTR SpiceOrder 2
""",
        encoding="utf-8",
    )
    (work / "bm_test.lib").write_text(
        ".subckt BM_TEST a b\nR1 a b 2k\n.ends BM_TEST\n", encoding="utf-8"
    )
    (work / "usebm.asc").write_text(
        """Version 4
SHEET 1 880 680
WIRE 128 128 128 192
FLAG 128 192 0
SYMBOL bmtest 192 128 R0
SYMATTR InstName X1
SYMBOL voltage 128 96 R0
SYMATTR InstName V1
SYMATTR Value 1
WIRE 128 112 128 128
WIRE 256 128 304 128
FLAG 304 128 in
TEXT 128 288 Left 2 !.tran 1m
""",
        encoding="utf-8",
    )
    result = netlist_step(ltspice_exe, work / "usebm.asc", timeout_s=60)
    assert result.exit_code == 0, result.observed()
    assert result.net_path is not None
    text = result.net_path.read_text(encoding="utf-8", errors="replace")
    assert "BM_TEST" in text
    assert ".lib bm_test.lib" in text, text


def test_op_raw_is_written_and_readable(ltspice_exe: Path, tmp_path: Path) -> None:
    deck = tmp_path / "op.cir"
    deck.write_text(
        "* op\nV1 in 0 10\nR1 in out 1k\nR2 out 0 1k\n.tran 1m\n.end\n", encoding="utf-8"
    )
    result = run_batch(ltspice_exe, deck, tmp_path, timeout_s=60)
    assert result.exit_code == 0, result.observed()
    assert result.op_raw_path is not None and result.op_raw_path.is_file()
    op = read_raw(result.op_raw_path)
    assert op.plotname == "Operating Point"
    assert op.npoints == 1
    # Operating-point plots have no time axis: callers must not assume one.
    assert op.time_column() is None
    values = {name: float(op.column(name)[0]) for name in op.variables}
    assert values.get("V(out)") == pytest.approx(5.0, abs=1e-6)
    assert values.get("V(in)") == pytest.approx(10.0, abs=1e-9)


def test_missing_install_diagnostics_shape(ltspice_exe: Path, tmp_path: Path) -> None:
    """smoke_test output serializes for `boardmodeler doctor --json`."""
    payload = json.dumps(smoke_test(ltspice_exe, tmp_path / "json").as_dict())
    data = json.loads(payload)
    for key in ("smoke_test", "smoke_detail", "measured_v", "expected_v", "exit_code"):
        assert key in data


# --- simulator lock: an OS-held lock, not a file whose existence means "busy" ---
#
# A lock released only by a `finally` is not released when the process is killed,
# and the leftover file then blocks every later run while nothing holds it. These
# tests exercise both halves of the real contract: a live holder excludes another
# process, and a *killed* holder does not.

_HOLD_LOCK = """
import sys, time
from boardmodeler.simulation.ltspice import _acquire_lock

path = _acquire_lock(10.0)
print("locked", flush=True)
time.sleep(120)
"""


def _spawn_lock_holder() -> subprocess.Popen[str]:
    """A child process that takes the lock and reports when it holds it."""
    import os
    import sys

    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    child = subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert child.stdout is not None
    line = child.stdout.readline().strip()
    assert line == "locked", f"child failed to take the lock: {line!r} {child.stderr}"
    return child


def test_a_live_holder_excludes_another_process() -> None:
    from boardmodeler.simulation.ltspice import LtspiceLockTimeout, _acquire_lock

    child = _spawn_lock_holder()
    try:
        with pytest.raises(LtspiceLockTimeout):
            _acquire_lock(0.5)
    finally:
        child.kill()
        child.wait(timeout=30)


def test_a_killed_holder_does_not_block_the_next_run() -> None:
    """Regression: a dead holder's lock must not stall the next invocation."""
    import time

    from boardmodeler.simulation.ltspice import _acquire_lock, _release_lock

    child = _spawn_lock_holder()
    child.kill()
    child.wait(timeout=30)

    started = time.monotonic()
    lock = _acquire_lock(20.0)
    waited = time.monotonic() - started
    try:
        assert waited < 5.0, (
            f"took {waited:.1f}s to acquire the lock after its holder was killed; "
            "the lock outlived the process that held it"
        )
    finally:
        _release_lock(lock)
