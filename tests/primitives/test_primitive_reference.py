"""Numeric reference tests for the behavioural primitive library (D8, Phase 1).

Every assertion in this module is a measured number taken from a real LTspice
waveform (``.raw``) or from ``.meas`` output; "the run did not error" is never an
assertion.  Each deck is generated inline, includes the emitted ``.lib`` by
absolute path, is written under ``tmp_path`` and is run through
:func:`boardmodeler.simulation.ltspice.run_batch` with a timeout.

Tolerances are stated next to the measurement they accept, together with the
observed value they were set from, so a regression has to move a number rather
than a boolean.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from boardmodeler.models.primitives import (
    PRIMITIVE_PARAMS,
    PRIMITIVE_PORT_ORDER,
    instance_card,
    port_order_matches,
    primitive_text,
    write_primitive_library,
)
from boardmodeler.simulation.log import LogSummary, parse_log
from boardmodeler.simulation.ltspice import BatchResult, run_batch
from boardmodeler.simulation.raw import RawFile, read_raw

TIMEOUT_S = 90.0
"""Simulator wall-clock limit for every deck in this module (D6)."""


# --------------------------------------------------------------------------- #
# helpers


def _subckt_line(name: str) -> str:
    for line in primitive_text(name).splitlines():
        if line.lower().startswith(".subckt"):
            return line
    raise AssertionError(f"{name} text has no .subckt line")


def _crossings(t: np.ndarray, v: np.ndarray, level: float, edge: str | None = None) -> list[float]:
    """Interpolated times at which ``v`` crosses ``level``.

    ``edge`` selects rising (``"rise"``) or falling (``"fall"``) crossings only.
    """
    out: list[float] = []
    for k in range(1, len(t)):
        before = v[k - 1] - level
        after = v[k] - level
        if (before < 0.0 <= after) or (before > 0.0 >= after):
            rising = after > before
            if edge == "rise" and not rising:
                continue
            if edge == "fall" and rising:
                continue
            frac = before / (before - after)
            out.append(float(t[k - 1] + frac * (t[k] - t[k - 1])))
    return out


def _value_at(t: np.ndarray, v: np.ndarray, when: float) -> float:
    return float(np.interp(when, t, v))


def _attempt(
    tmp_path: Path, exe: Path, lib: Path, name: str, body: str, attempt: int
) -> tuple[BatchResult, LogSummary | None]:
    run_dir = tmp_path / f"{name}-attempt{attempt}"
    run_dir.mkdir(parents=True, exist_ok=True)
    deck = run_dir / f"{name}.cir"
    deck.write_text(f'* {name}\n.include "{lib}"\n{body}', encoding="utf-8", newline="\n")

    result = run_batch(exe, deck, run_dir, timeout_s=TIMEOUT_S)
    summary = (
        parse_log(result.log_path)
        if result.log_path is not None and result.log_path.is_file()
        else None
    )
    return result, summary


def _silent_abort(result: BatchResult, summary: LogSummary | None) -> bool:
    """True when LTspice died without completing *and* without reporting a reason.

    Observed once while another simulation was running: exit code 1, no
    completion marker, no error and no convergence line - i.e. an infrastructure
    abort, not a circuit verdict.  Nothing else is retried.
    """
    if result.timed_out or result.cancelled or result.exit_code == 0:
        return False
    if summary is None:
        return True
    return not summary.completed and not summary.errors and not summary.convergence_issues


def _simulate(
    tmp_path: Path,
    exe: Path,
    lib: Path,
    name: str,
    body: str,
) -> tuple[RawFile, BatchResult]:
    """Write ``body`` as one deck under ``tmp_path``, run it, return the waveform.

    The run must have exited 0, must not have timed out, and must have produced a
    ``.raw`` with a completion marker in the log before any numeric assertion is
    attempted.  A simulator abort that reports nothing (see :func:`_silent_abort`)
    is retried once so a loaded machine cannot fail the suite; a real failure - a
    reported error, a convergence problem or a timeout - fails on the first
    attempt and is never retried.  No numeric assertion is relaxed by the retry:
    the returned waveform always comes from one complete run.
    """
    result, summary = _attempt(tmp_path, exe, lib, name, body, 1)
    if _silent_abort(result, summary):
        warnings.warn(
            f"{name}: LTspice aborted without reporting a reason "
            f"(exit={result.exit_code}, log={'missing' if summary is None else 'incomplete'}); "
            "retrying once",
            stacklevel=2,
        )
        result, summary = _attempt(tmp_path, exe, lib, name, body, 2)

    assert result.exit_code == 0, f"{name}: exit={result.exit_code} stderr={result.stderr!r}"
    assert not result.timed_out, f"{name}: timed out after {result.wall_s:.1f}s"
    assert result.raw_path is not None and result.raw_path.is_file(), (
        f"{name}: no .raw produced; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.log_path is not None and result.log_path.is_file(), f"{name}: no .log produced"
    assert summary is not None
    assert summary.completed, f"{name}: log has no completion marker: {summary.text[-400:]!r}"
    assert not summary.errors, f"{name}: simulator errors {summary.errors}"
    assert not summary.convergence_issues, f"{name}: convergence {summary.convergence_issues}"

    raw = read_raw(result.raw_path)
    assert raw.npoints > 100, f"{name}: only {raw.npoints} points - not a usable waveform"
    return raw, result


@pytest.fixture(scope="module")
def lib(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The emitted library, written under pytest's temp dir (never the repo)."""
    return write_primitive_library(tmp_path_factory.mktemp("primitive_library") / "bm.lib")


# --------------------------------------------------------------------------- #
# BM_SCHMITT


@pytest.mark.ltspice
def test_schmitt_trip_points_and_hysteresis(tmp_path: Path, ltspice_exe: Path, lib: Path) -> None:
    """Trip points must sit at VTH +/- VHYS/2 and the width must match VHYS.

    The input is a -0.5 V -> 2.5 V -> -0.5 V triangle (3 V/ms) so the RC lag
    contributes only slope*TPD = 3 mV to each trip reading.
    """
    vth, vhys, voh, vol, tpd = 1.2, 0.2, 3.3, 0.0, 1e-6
    body = f"""
.tran 0 2.05m 0 20n
Vdd vdd 0 {voh}
Vin in 0 PWL(0 -0.5 1m 2.5 2m -0.5 2.05m -0.5)
X1 in 0 out vdd 0 BM_SCHMITT VTH={vth} VHYS={vhys} VOH={voh} VOL={vol} TPD={tpd}
"""
    raw, _ = _simulate(tmp_path, ltspice_exe, lib, "schmitt", body)
    t = raw.time_column()
    assert t is not None
    v_in = raw.column("V(in)")
    v_out = raw.column("V(out)")
    mid = (voh + vol) / 2

    falling_out = _crossings(t, v_out, mid, "fall")  # input rising -> out goes low
    rising_out = _crossings(t, v_out, mid, "rise")  # input falling -> out goes high
    assert falling_out and rising_out, "no output transition observed"

    trip_rising = _value_at(t, v_in, falling_out[0])
    trip_falling = _value_at(t, v_in, rising_out[-1])
    width = trip_rising - trip_falling

    # measured: 1.3029 V (expect 1.300), 1.0970 V (expect 1.100), width 0.2059 V
    assert abs(trip_rising - (vth + vhys / 2)) <= 0.010
    assert abs(trip_falling - (vth - vhys / 2)) <= 0.010
    assert abs(width - vhys) / vhys <= 0.10, f"hysteresis width {width:.4f} V vs VHYS {vhys}"

    assert float(v_out.max()) >= voh - 0.01, "output never reached VOH"
    assert float(v_out.min()) <= vol + 0.01, "output never reached VOL"
    assert float(v_out.max()) <= voh + 0.01 and float(v_out.min()) >= vol - 0.01

    # TPD: comparator trip to the mid-level crossing of the RC-lagged output.
    input_trip = _crossings(t, v_in, vth + vhys / 2, "rise")[0]
    measured_tpd = falling_out[0] - input_trip
    # measured: 0.968 us for TPD = 1 us
    assert abs(measured_tpd - tpd) <= 0.10 * tpd, f"TPD measured {measured_tpd * 1e6:.3f} us"


# --------------------------------------------------------------------------- #
# BM_DELAY


@pytest.mark.ltspice
def test_delay_matches_td_and_edge_time(tmp_path: Path, ltspice_exe: Path, lib: Path) -> None:
    """TD is the measured 50 %-to-50 % delay; TR is the measured 10 %-90 % edge."""
    td, tr = 5e-6, 200e-9
    body = f"""
.tran 0 30u 0 2n
Vdd vdd 0 3.3
Vin in 0 PULSE(0 3.3 1u 1n 1n 10u 100u)
X1 in out vdd 0 BM_DELAY TD={td} TR={tr}
"""
    raw, _ = _simulate(tmp_path, ltspice_exe, lib, "delay", body)
    t = raw.time_column()
    assert t is not None
    v_in = raw.column("V(in)")
    v_out = raw.column("V(out)")

    driven = _crossings(t, v_in, 1.65, "rise")[0]
    responded = _crossings(t, v_out, 1.65, "rise")[0]
    measured = responded - driven
    # measured: 5.0000 us for TD = 5 us (0.00 %)
    assert abs(measured - td) <= 0.15 * td, f"TD measured {measured * 1e6:.4f} us vs {td * 1e6}"

    lo = _crossings(t, v_out, 0.33, "rise")[0]
    hi = _crossings(t, v_out, 2.97, "rise")[0]
    measured_tr = hi - lo
    # measured: 199.99 ns for TR = 200 ns
    assert abs(measured_tr - tr) <= 0.10 * tr, (
        f"TR measured {measured_tr * 1e9:.1f} ns vs {tr * 1e9}"
    )

    driven_fall = _crossings(t, v_in, 1.65, "fall")[0]
    responded_fall = _crossings(t, v_out, 1.65, "fall")[0]
    measured_fall = responded_fall - driven_fall
    # measured: 5.0004 us on the falling edge too
    assert abs(measured_fall - td) <= 0.15 * td, f"falling TD {measured_fall * 1e6:.4f} us"

    assert float(v_out.max()) >= 3.29 and float(v_out.min()) <= 0.01, "output is not rail to rail"


# --------------------------------------------------------------------------- #
# BM_OD


@pytest.mark.ltspice
def test_open_drain_on_resistance_and_leakage(tmp_path: Path, ltspice_exe: Path, lib: Path) -> None:
    """Low state carries RON_LOW (I/V); the open state leaks ILEAK out of the pin."""
    ron, ileak = 10.0, 2e-6
    body = f"""
.tran 0 200u 0 100n
* low state: a 1 mA drive ramps in from zero so the switch state resolves closed
I1 0 p1 PWL(0 0 50u 1m 200u 1m)
X1 p1 0 BM_OD RON_LOW={ron} ILEAK={ileak}
* low state held by a stiff source: still RON_LOW, the device must not release
V3 p3 0 0.3
X3 p3 0 BM_OD RON_LOW={ron} ILEAK={ileak}
* open state: the pin is held at 3.3 V and only the leakage may flow
V2 p2 0 3.3
X2 p2 0 BM_OD RON_LOW={ron} ILEAK={ileak}
"""
    raw, _ = _simulate(tmp_path, ltspice_exe, lib, "open_drain", body)
    t = raw.time_column()
    assert t is not None

    pin_low = float(raw.column("V(p1)")[-1])
    drive = abs(float(raw.column("I(I1)")[-1]))
    measured_ron = pin_low / drive
    # measured: 10.000 mV at 1.000 mA -> 10.0000 ohm
    assert abs(measured_ron - ron) / ron <= 0.05, f"RON_LOW measured {measured_ron:.4f} ohm"
    assert pin_low > 0.0, "low state is not a resistive pull-down"

    # measured: 0.300 V across 10 ohm -> 30.000 mA (held by a stiff source)
    held_current = abs(float(raw.column("I(V3)")[-1]))
    held_ron = 0.3 / held_current
    assert abs(held_ron - ron) / ron <= 0.05, f"held low state measured {held_ron:.4f} ohm"

    off_pin = float(raw.column("V(p2)")[-1])
    off_signed = float(raw.column("I(V2)")[-1])
    off_current = abs(off_signed)
    # measured: 2.000007 uA sunk out of the pin, and the pin is not clamped while open
    assert abs(off_current - ileak) / ileak <= 0.05, f"ILEAK measured {off_current:.4e} A"
    assert off_signed < 0.0, "the open device must sink ILEAK out of the pin, not source it"
    assert off_current < 0.05 * drive, "device still conducts strongly in the open state"
    assert off_pin > 3.0, f"open state is not high impedance (pin at {off_pin:.3f} V)"


# --------------------------------------------------------------------------- #
# BM_PUSHPULL


@pytest.mark.ltspice
def test_pushpull_levels_and_output_resistance(
    tmp_path: Path, ltspice_exe: Path, lib: Path
) -> None:
    """Output stays inside [VOL, VOH] and droops by I*ROUT under load."""
    voh, vol, rout, rload = 3.3, 0.0, 100.0, 1000.0
    body = f"""
.tran 0 200u 0 100n
Vdd vdd 0 3.3
Vin_hi in_hi 0 3.3
X1 in_hi out_hi vdd 0 BM_PUSHPULL VOH={voh} VOL={vol} ROUT={rout}
Vin_lo in_lo 0 0
X2 in_lo out_lo vdd 0 BM_PUSHPULL VOH={voh} VOL={vol} ROUT={rout}
Vin_ld in_ld 0 3.3
Rl out_ld 0 {rload}
X3 in_ld out_ld vdd 0 BM_PUSHPULL VOH={voh} VOL={vol} ROUT={rout}
"""
    raw, _ = _simulate(tmp_path, ltspice_exe, lib, "pushpull", body)
    high = float(raw.column("V(out_hi)")[-1])
    low = float(raw.column("V(out_lo)")[-1])
    loaded = float(raw.column("V(out_ld)")[-1])

    # measured: 3.300 V, 0.000 V, 3.000 V
    assert vol <= low <= voh and vol <= high <= voh, f"levels outside [{vol}, {voh}]"
    assert vol <= loaded <= voh
    assert high >= voh - 0.01, f"high state {high:.4f} V does not reach VOH"
    assert low <= vol + 0.05, f"low state {low:.4f} V does not reach VOL"

    load_current = loaded / rload
    droop = high - loaded
    expected = load_current * rout
    # measured: droop 0.300 V vs I*ROUT = 3.000 mA * 100 ohm = 0.300 V
    assert abs(droop - expected) <= 0.10 * expected, (
        f"droop {droop:.4f} V vs I*ROUT {expected:.4f} V"
    )


# --------------------------------------------------------------------------- #
# BM_SUPPLY_IO


@pytest.mark.ltspice
def test_supply_io_zero_drive_below_vil_and_clamped_high(
    tmp_path: Path, ltspice_exe: Path, lib: Path
) -> None:
    """No drive while V(vdd) < VIL_MAX; high level is vdd minus a drop; ICLAMP holds."""
    vil, vih, iclamp = 0.8, 2.0, 5e-3
    body = f"""
.tran 0 200u 0 100n
* vdd at half of VIL_MAX with the input high: the output must not be driven
Vdd1 vdd1 0 {vil / 2}
Vin1 in1 0 3.3
Rk1 out1 vdd1 10k
X1 in1 out1 vdd1 0 BM_SUPPLY_IO VIL_MAX={vil} VIH_MIN={vih} ICLAMP={iclamp}
* powered, input high
Vdd2 vdd2 0 3.3
Vin2 in2 0 3.3
Rk2 out2 vdd2 10k
X2 in2 out2 vdd2 0 BM_SUPPLY_IO VIL_MAX={vil} VIH_MIN={vih} ICLAMP={iclamp}
* powered, input low
Vdd3 vdd3 0 3.3
Vin3 in3 0 0
Rk3 out3 vdd3 10k
X3 in3 out3 vdd3 0 BM_SUPPLY_IO VIL_MAX={vil} VIH_MIN={vih} ICLAMP={iclamp}
* powered, input high, output shorted: the drive current must clamp at ICLAMP
Vdd4 vdd4 0 3.3
Vin4 in4 0 3.3
Vsc out4 0 0
X4 in4 out4 vdd4 0 BM_SUPPLY_IO VIL_MAX={vil} VIH_MIN={vih} ICLAMP={iclamp}
"""
    raw, _ = _simulate(tmp_path, ltspice_exe, lib, "supply_io", body)
    unpowered = float(raw.column("V(out1)")[-1])
    powered_high = float(raw.column("V(out2)")[-1])
    powered_low = float(raw.column("V(out3)")[-1])
    short_current = abs(float(raw.column("I(Vsc)")[-1]))

    # measured: 1.59 mV with vdd = 0.4 V (VIL_MAX/2): zero drive
    assert unpowered <= 0.05 * vil, f"unpowered output {unpowered:.5f} V is not ~0"
    # measured: 3.1008 V with vdd = 3.3 V -> vdd minus the 0.2 V drop
    assert powered_high <= 3.3 and powered_high >= 3.3 - 0.5, f"high level {powered_high:.4f} V"
    assert powered_high <= 3.3 - 0.05, "high level is not clamped below vdd"
    # measured: 13.1 mV low level (40 ohm pull-down against a 10k keeper)
    assert powered_low <= 0.05 * 3.3, f"low level {powered_low:.4f} V"
    # measured: 5.000000 mA into a short
    assert abs(short_current - iclamp) / iclamp <= 0.10, f"ICLAMP measured {short_current:.4e} A"


# --------------------------------------------------------------------------- #
# BM_CONDUCTION


@pytest.mark.ltspice
def test_conduction_on_resistance_and_reverse_blocking(
    tmp_path: Path, ltspice_exe: Path, lib: Path
) -> None:
    """RC_ON sets the conducting resistance; RC_OFF = 1 Tohm blocks both directions."""
    rc_on, rc_off = 100.0, 1e12
    body = f"""
.tran 0 200u 0 1u
* ctrl high, 1 V forward
V1 an1 0 1
Vsense1 an1 a1 0
Vc1 ctrl1 0 3.3
X1 a1 b1 ctrl1 BM_CONDUCTION RC_ON={rc_on} RC_OFF={rc_off}
Vb1 b1 0 0
* ctrl low, 1 V forward
V2 an2 0 1
Vsense2 an2 a2 0
Vc2 ctrl2 0 0
X2 a2 b2 ctrl2 BM_CONDUCTION RC_ON={rc_on} RC_OFF={rc_off}
Vb2 b2 0 0
* ctrl low, 1 V reverse (b above a)
V3 an3 0 -1
Vsense3 an3 a3 0
Vc3 ctrl3 0 0
X3 a3 b3 ctrl3 BM_CONDUCTION RC_ON={rc_on} RC_OFF={rc_off}
Vb3 b3 0 0
"""
    raw, _ = _simulate(tmp_path, ltspice_exe, lib, "conduction", body)
    t = raw.time_column()
    assert t is not None

    forward_current = abs(float(raw.column("I(Vsense1)")[-1]))
    across_on = float(raw.column("V(a1)")[-1]) - float(raw.column("V(b1)")[-1])
    measured_ron = across_on / forward_current
    # measured: 100.000002 ohm for RC_ON = 100 ohm
    assert abs(measured_ron - rc_on) / rc_on <= 0.05, f"RC_ON measured {measured_ron:.4f} ohm"

    off_forward = abs(float(raw.column("I(Vsense2)")[-1]))
    off_reverse = abs(float(raw.column("I(Vsense3)")[-1]))
    # measured: 1e-12 A in both blocked directions
    assert off_reverse < 1e-9, f"reverse leakage {off_reverse:.4e} A is not blocked"
    assert off_forward < 1e-9, f"forward leakage {off_forward:.4e} A is not blocked"
    assert forward_current > 1e-3, "device does not conduct with ctrl high"


# --------------------------------------------------------------------------- #
# BM_LOAD


@pytest.mark.ltspice
def test_load_static_profile_and_step(tmp_path: Path, ltspice_exe: Path, lib: Path) -> None:
    """The load draws I_STATIC, then I_STATIC + I_STEP after T_STEP."""
    i_static, i_step, t_step = 1e-3, 3e-3, 2e-3
    body = f"""
.tran 0 4m 0 1u
Vdd out 0 3.3
Vsense out load 0
X1 load 0 BM_LOAD I_STATIC={i_static} I_STEP={i_step} T_STEP={t_step}
"""
    raw, _ = _simulate(tmp_path, ltspice_exe, lib, "load", body)
    t = raw.time_column()
    assert t is not None
    current = raw.column("I(Vsense)")

    before = _value_at(t, current, t_step / 2)
    after = _value_at(t, current, t_step + 1e-3)
    # measured: 1.000000 mA before, 4.000000 mA after
    assert abs(before - i_static) / i_static <= 0.05, f"static current {before:.6e} A"
    expected_after = i_static + i_step
    assert abs(after - expected_after) / expected_after <= 0.05, f"step current {after:.6e} A"
    assert abs(after - before) > i_step / 2, "no step observed"


# --------------------------------------------------------------------------- #
# BM_PG


@pytest.mark.ltspice
def test_pg_open_drain_high_impedance_threshold_and_delay(
    tmp_path: Path, ltspice_exe: Path, lib: Path
) -> None:
    """Below threshold the pin is high impedance (pull-up wins); above it is pulled low."""
    vth, vhys, td = 1.2, 0.2, 10e-6
    body = f"""
.tran 0 130u 0 10n
Vdd vdd 0 3.3
* below threshold: only the 10k pull-up drives the pin
V1 open_a 0 0
Rpu_a out_a vdd 10k
Xa open_a out_a vdd 0 BM_PG VTH={vth} VHYS={vhys} TD={td} PULLUP_MAX=100k
* above the upper threshold: the pin is pulled down
V2 open_b 0 2.0
Rpu_b out_b vdd 10k
Xb open_b out_b vdd 0 BM_PG VTH={vth} VHYS={vhys} TD={td} PULLUP_MAX=100k
* step across the threshold for the delay measurement
V3 open_c 0 PWL(0 0 100u 0 100.1u 2 130u 2)
Rpu_c out_c vdd 10k
Xc open_c out_c vdd 0 BM_PG VTH={vth} VHYS={vhys} TD={td} PULLUP_MAX=100k
"""
    raw, _ = _simulate(tmp_path, ltspice_exe, lib, "pg", body)
    t = raw.time_column()
    assert t is not None

    open_state = float(raw.column("V(out_a)")[-1])
    # measured: 3.300000 V behind a 10k pull-up = within 5 % of vdd
    assert abs(open_state - 3.3) <= 0.05 * 3.3, f"high-impedance level {open_state:.5f} V"

    asserted = float(raw.column("V(out_b)")[-1])
    # measured: 16.4 mV (50 ohm pull-down against the 10k pull-up)
    assert asserted <= 0.05 * 3.3, f"asserted level {asserted:.5f} V"

    v_in = raw.column("V(open_c)")
    v_out = raw.column("V(out_c)")
    driven = _crossings(t, v_in, vth + vhys / 2, "rise")[0]
    responded = _crossings(t, v_out, 1.65, "fall")[0]
    measured_delay = responded - driven
    # measured: 10.005 us for TD = 10 us
    assert abs(measured_delay - td) <= 0.10 * td, f"TD measured {measured_delay * 1e6:.4f} us"


# --------------------------------------------------------------------------- #
# library-level checks (no simulator needed)


def test_primitive_text_is_deterministic_and_ports_match() -> None:
    """Emitted text is byte-stable and every subckt lists its ports in order."""
    names: Sequence[str] = tuple(PRIMITIVE_PORT_ORDER)
    for name in names:
        assert primitive_text(name) == primitive_text(name)
        line = _subckt_line(name)
        assert port_order_matches(name, line)
        assert port_order_matches(
            name, f".SUBCKT {name.upper()} {' '.join(PRIMITIVE_PORT_ORDER[name])}"
        )
        # a permuted port list must never be accepted
        ports = list(PRIMITIVE_PORT_ORDER[name])
        swapped = [ports[1], ports[0], *ports[2:]]
        assert not port_order_matches(name, f".subckt {name} {' '.join(swapped)}")
        assert not port_order_matches(name, f".subckt BM_OTHER {' '.join(ports)}")
        # a parameter clause without the "params:" keyword is still not a port list
        assert port_order_matches(name, f".subckt {name} {' '.join(ports)} P=1k")
        assert not port_order_matches(name, f".subckt {name} P=1k {' '.join(ports)}")
        # every documented parameter must carry a default
        params_clause = line.split("params:", 1)[1]
        for param in PRIMITIVE_PARAMS[name]:
            assert f"{param}=" in params_clause, f"{param} has no default in {line}"
        assert line.count("params:") == 1
        assert primitive_text(name).count(".subckt") == 1
        assert primitive_text(name).count(".ends") == 1

    with pytest.raises(KeyError):
        primitive_text("BM_NOPE")


def test_port_and_param_tables_match_the_specification() -> None:
    """The documented tables are the contract; they must not drift."""
    assert PRIMITIVE_PORT_ORDER == {
        "BM_SCHMITT": ("in", "ref", "out", "vdd", "vss"),
        "BM_DELAY": ("in", "out", "vdd", "vss"),
        "BM_OD": ("out", "vss"),
        "BM_PUSHPULL": ("in", "out", "vdd", "vss"),
        "BM_SUPPLY_IO": ("in", "out", "vdd", "vss"),
        "BM_CONDUCTION": ("a", "b", "ctrl"),
        "BM_LOAD": ("out", "vss"),
        "BM_PG": ("open_in", "out", "vdd", "vss"),
    }
    assert PRIMITIVE_PARAMS == {
        "BM_SCHMITT": ("VTH", "VHYS", "VOH", "VOL", "TPD"),
        "BM_DELAY": ("TD", "TR"),
        "BM_OD": ("RON_LOW", "ILEAK"),
        "BM_PUSHPULL": ("VOH", "VOL", "ROUT"),
        "BM_SUPPLY_IO": ("VIL_MAX", "VIH_MIN", "ICLAMP"),
        "BM_CONDUCTION": ("RC_ON", "RC_OFF"),
        "BM_LOAD": ("I_STATIC", "I_STEP", "T_STEP"),
        "BM_PG": ("VTH", "VHYS", "TD", "PULLUP_MAX"),
    }


def test_instance_card_and_library_file(tmp_path: Path) -> None:
    """Instance cards name the subckt after the ports; the file is reproducible."""
    card = instance_card(
        "BM_SCHMITT", "X1", PRIMITIVE_PORT_ORDER["BM_SCHMITT"], {"VTH": 1.2, "VHYS": 0.2}
    )
    assert card == "X1 in ref out vdd vss BM_SCHMITT VTH=1.2 VHYS=0.2"
    assert instance_card("BM_LOAD", "X9", ("out", "vss"), {}) == "X9 out vss BM_LOAD"

    with pytest.raises(ValueError):
        instance_card("BM_LOAD", "X9", ("out",), {})
    with pytest.raises(ValueError):
        instance_card("BM_LOAD", "X9", ("out", "vss"), {"I_TYPO": 1.0})

    first = write_primitive_library(tmp_path / "one" / "bm.lib")
    second = write_primitive_library(tmp_path / "two" / "bm.lib")
    assert first.read_bytes() == second.read_bytes()
    assert first.read_text() == write_primitive_library(tmp_path / "three.lib").read_text()

    subset = write_primitive_library(tmp_path / "subset.lib", ["BM_OD", "BM_LOAD"])
    text = subset.read_text()
    assert "BM_OD" in text and "BM_LOAD" in text and "BM_SCHMITT" not in text
    with pytest.raises(KeyError):
        write_primitive_library(tmp_path / "bad.lib", ["BM_NOPE"])


# --------------------------------------------------------------------------- #
# defaults


@pytest.mark.ltspice
def test_every_primitive_simulates_with_all_defaults(
    tmp_path: Path, ltspice_exe: Path, lib: Path
) -> None:
    """Every primitive must run with no instance parameters at all.

    A missing default shows up here as either a simulator error or a dead output;
    the numeric checks per instance prove the default values are functional, not
    merely parseable.
    """
    body = """
.tran 0 1.6m 0 100n
Vdd vdd 0 3.3
* BM_SCHMITT defaults (VTH=1.2 VHYS=0.2 VOH=3.3 VOL=0 TPD=1u)
Vsc_in sc_in 0 PWL(0 0 500u 2 1m 0 2m 0)
Xsc sc_in 0 sc_out vdd 0 BM_SCHMITT
* BM_DELAY defaults (TD=1u TR=100n)
Vdl_in dl_in 0 PULSE(0 3.3 200u 1n 1n 900u 4m)
Xdl dl_in dl_out vdd 0 BM_DELAY
* BM_OD defaults (RON_LOW=10 ILEAK=1u)
Iod 0 od_pin PWL(0 0 200u 1m 2m 1m)
Xod od_pin 0 BM_OD
* BM_PUSHPULL defaults (VOH=3.3 VOL=0 ROUT=50)
Vpp_in pp_in 0 3.3
Xpp pp_in pp_out vdd 0 BM_PUSHPULL
* BM_SUPPLY_IO defaults (VIL_MAX=0.8 VIH_MIN=2.0 ICLAMP=5m)
Vio_in io_in 0 3.3
Rk_io io_out vdd 10k
Xio io_in io_out vdd 0 BM_SUPPLY_IO
* BM_CONDUCTION defaults (RC_ON=50 RC_OFF=1e12)
Vcd_a cd_a 0 1
Vsense_cd cd_a cd_b 0
Vcd_ctrl cd_ctrl 0 3.3
Xcd cd_b 0 cd_ctrl BM_CONDUCTION
* BM_LOAD defaults (I_STATIC=1m I_STEP=1m T_STEP=1m)
Vld_out ld_out 0 3.3
Vsense_ld ld_out ld_load 0
Xld ld_load 0 BM_LOAD
* BM_PG defaults (VTH=1.2 VHYS=0.2 TD=10u PULLUP_MAX=100k)
Vpg_in pg_in 0 0
Rpu_pg pg_out vdd 10k
Xpg pg_in pg_out vdd 0 BM_PG
"""
    raw, result = _simulate(tmp_path, ltspice_exe, lib, "defaults", body)
    assert result.raw_path is not None
    t = raw.time_column()
    assert t is not None
    final = raw.npoints - 1

    # BM_SCHMITT toggled across its default thresholds
    assert float(raw.column("V(sc_out)").max()) > 3.0
    assert float(raw.column("V(sc_out)").min()) < 0.1
    # BM_DELAY delivered the delayed edge (sampled while the input pulse is high)
    assert _value_at(t, raw.column("V(dl_out)"), 0.8e-3) > 3.0
    # BM_OD pulled the 1 mA drive down to RON_LOW * I
    assert abs(float(raw.column("V(od_pin)")[final]) - 0.01) < 0.005
    # BM_PUSHPULL drove VOH
    assert abs(float(raw.column("V(pp_out)")[final]) - 3.3) < 0.01
    # BM_SUPPLY_IO drove vdd minus the internal drop
    assert abs(float(raw.column("V(io_out)")[final]) - 3.1) < 0.05
    # BM_CONDUCTION conducted through RC_ON = 50 ohm
    assert abs(float(raw.column("I(Vsense_cd)")[final]) - 1 / 50) < 0.005
    # BM_LOAD stepped from I_STATIC to I_STATIC + I_STEP at T_STEP = 1 ms
    assert abs(_value_at(t, raw.column("I(Vsense_ld)"), 0.5e-3) - 1e-3) < 1e-4
    assert abs(_value_at(t, raw.column("I(Vsense_ld)"), 1.4e-3) - 2e-3) < 1e-4
    # BM_PG released the pin into the pull-up
    assert float(raw.column("V(pg_out)")[final]) > 3.2
