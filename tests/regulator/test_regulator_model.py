"""Numeric reference tests for the reduced behavioural regulator templates (D8).

Every assertion here is a measured number taken from a real LTspice run through
:func:`boardmodeler.simulation.ltspice.run_batch`: each test writes a deck under
``tmp_path`` (never inside the repository tree), includes the emitted library by
absolute path and reads the ``.raw`` with the project's own reader.  The comment
next to each tolerance names the quantity that was measured and the law it is
checked against; nothing is asserted about "the run not erroring" alone.

The tests cover the mandatory behaviours of the templates in the same order as
the model docstring: UVLO, EN, soft start, external-divider regulation, current
limit (hiccup and latch), power good, discharge, pre-bias, reverse blocking and
the input-current/energy relation.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import pytest

from boardmodeler.models.primitives import primitive_text
from boardmodeler.models.regulator import (
    REGULATOR_EXTRA_PARAMS,
    REGULATOR_PARAMS,
    REGULATOR_PORT_ORDER,
    REGULATOR_PRIMITIVES,
    is_switching,
    regulator_instance,
    regulator_library_text,
    regulator_text,
    write_regulator_library,
)
from boardmodeler.simulation.deck import DeckSpec, Include, Source, TranSpec, write_deck
from boardmodeler.simulation.log import LogSummary, parse_log
from boardmodeler.simulation.ltspice import BatchResult, run_batch
from boardmodeler.simulation.raw import RawFile, read_raw

TIMEOUT_S = 120.0
"""Simulator wall-clock limit for every deck in this module (D-006)."""

ISS = 1.7e-6
"""Internal soft-start charge current of the templates; dV/dt = ISS/CSS."""


@pytest.fixture(scope="module")
def lib(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The emitted library, written under pytest's temp dir (never the repo)."""
    return write_regulator_library(tmp_path_factory.mktemp("regulator_library") / "bm_reg.lib")


# --------------------------------------------------------------------------- #
# deck harness


def _attempt(
    tmp_path: Path, exe: Path, lib: Path, name: str, spec: DeckSpec
) -> tuple[BatchResult, LogSummary | None]:
    run_dir = tmp_path / name
    run_dir.mkdir(parents=True, exist_ok=True)
    deck = write_deck(spec, run_dir / f"{name}.cir")
    result = run_batch(exe, deck, run_dir, timeout_s=TIMEOUT_S)
    summary = (
        parse_log(result.log_path)
        if result.log_path is not None and result.log_path.is_file()
        else None
    )
    return result, summary


def _silent_abort(result: BatchResult, summary: LogSummary | None) -> bool:
    """True when LTspice died without completing *and* without reporting a reason.

    An infrastructure abort (measured once in the primitive reference suite)
    reports nothing at all; a real failure always leaves an error or a
    convergence line, is never retried, and is never relaxed.
    """
    if result.timed_out or result.cancelled or result.exit_code == 0:
        return False
    if summary is None:
        return True
    return not summary.completed and not summary.errors and not summary.convergence_issues


def _simulate(tmp_path: Path, exe: Path, lib: Path, name: str, spec: DeckSpec) -> RawFile:
    """Run one deck and return the waveform, failing on any unreported problem."""
    result, summary = _attempt(tmp_path, exe, lib, name, spec)
    if _silent_abort(result, summary):
        warnings.warn(f"{name}: LTspice aborted without a reason; retrying once", stacklevel=2)
        result, summary = _attempt(tmp_path, exe, lib, name, spec)

    assert result.exit_code == 0, f"{name}: exit={result.exit_code} stderr={result.stderr!r}"
    assert not result.timed_out, f"{name}: timed out after {result.wall_s:.1f}s"
    assert result.raw_path is not None and result.raw_path.is_file(), (
        f"{name}: no .raw produced; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.log_path is not None and result.log_path.is_file(), f"{name}: no .log"
    assert summary is not None
    assert summary.completed, f"{name}: log without completion marker: {summary.text[-400:]!r}"
    assert not summary.errors, f"{name}: simulator errors {summary.errors}"
    assert not summary.convergence_issues, f"{name}: convergence {summary.convergence_issues}"
    raw = read_raw(result.raw_path)
    assert raw.npoints > 200, f"{name}: only {raw.npoints} points"
    return raw


def _deck(
    lib: Path,
    title: str,
    elements: Sequence[str],
    sources: Sequence[Source],
    tstop: float,
    *,
    tmax: float = 2e-6,
    uic: bool = True,
    ics: Mapping[str, float] | None = None,
    save: Sequence[str],
) -> DeckSpec:
    """One transient deck: explicit sources, explicit external network, explicit saves."""
    return DeckSpec(
        title=title,
        includes=(Include(path=str(lib)),),
        sources=tuple(sources),
        elements=tuple(elements),
        tran=TranSpec(tstep=tstop / 2000, tstop=tstop, tstart=0.0, tmax=tmax, uic=uic),
        initial_conditions=ics or {},
        save=tuple(save),
    )


def _t(raw: RawFile) -> np.ndarray:
    time = raw.time_column()
    assert time is not None
    return time


def _crossings(t: np.ndarray, v: np.ndarray, level: float, edge: str | None = None) -> list[float]:
    """Interpolated times at which ``v`` crosses ``level`` (rising/falling selected)."""
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
            out.append(float(t[k - 1] + before / (before - after) * (t[k] - t[k - 1])))
    return out


def _at(t: np.ndarray, v: np.ndarray, when: float) -> float:
    return float(np.interp(when, t, v))


def _window(t: np.ndarray, v: np.ndarray, start: float, stop: float) -> np.ndarray:
    lo = int(np.searchsorted(t, start))
    hi = int(np.searchsorted(t, stop))
    assert hi > lo, f"empty window [{start}, {stop}]"
    return v[lo:hi]


def _slope(t: np.ndarray, v: np.ndarray, start: float, stop: float) -> float:
    lo = int(np.searchsorted(t, start))
    hi = int(np.searchsorted(t, stop))
    return float(np.polyfit(t[lo:hi], v[lo:hi], 1)[0])


# --------------------------------------------------------------------------- #
# the external networks used by the decks


def _buck(nodes: Mapping[str, str] | None = None, params: Mapping[str, float | int] | None = None):
    return regulator_instance(
        "BM_REG_BUCK",
        "X1",
        nodes
        or {
            "VIN": "vin",
            "EN": "en",
            "FB": "fb",
            "PG": "pg",
            "VOUT": "vout",
            "GND": "0",
            "SW": "sw",
            "ILIM_MODE": "im",
        },
        params,
    )


def _ldo(nodes: Mapping[str, str] | None = None, params: Mapping[str, float | int] | None = None):
    return regulator_instance(
        "BM_REG_LDO",
        "X1",
        nodes or {"VIN": "vin", "EN": "en", "FB": "fb", "PG": "pg", "VOUT": "vout", "GND": "0"},
        params,
    )


DIV_NOM = ("R_fbt vout_l fb 10k", "R_fbb fb 0 3.24k")
"""1 % divider: the regulation target is VREF*(1+10k/3.24k) = 3.2716 V."""

DIV_WRONG = ("R_fbt vout_l fb 30k", "R_fbb fb 0 3.24k")
"""Wrong divider: the model must follow it to VREF*(1+30k/3.24k) = 8.210 V."""

PG_PULLUP = "R_pu pg vdd_pu 10k"
SW_TIE = "R_sw sw 0 10k"
EN_TIE = "R_en en vin 1"


def _load(value: str) -> str:
    return f"R_load vout_l 0 {value}"


# --------------------------------------------------------------------------- #
# behaviour 1: VIN UVLO with hysteresis


@pytest.mark.ltspice
def test_vin_uvlo_start_stop_thresholds(tmp_path: Path, ltspice_exe: Path, lib: Path) -> None:
    """Start above UVLO_RISE, keep running down to UVLO_FALL, then stop.

    VIN sweeps 0 -> 6 V in 30 ms (0.2 V/ms), holds, then 6 -> 0 V in 15 ms
    (0.4 V/ms).  Measured: the output starts 0.3 ms after VIN crosses
    UVLO_RISE = 4.3 V (the soft start needs 0.29 ms to reach 0.2 V) and stops
    12 us after VIN crosses UVLO_FALL = 3.9 V (the 10 ohm discharge moves the
    output 0.27 V in 18 us).
    """
    spec = _deck(
        lib,
        "buck UVLO: VIN 0->6 V in 30 ms, hold, 6->0 V in 15 ms",
        [
            _buck(),
            "R_insns vin_s vin 1m",
            "R_sns vout vout_l 1m",
            "C_out vout_l 0 22u",
            _load("330"),
            PG_PULLUP,
            SW_TIE,
            EN_TIE,
            *DIV_NOM,
        ],
        [
            Source(
                name="V1",
                terminals=("vin_s", "0"),
                kind="pwl",
                points=((0.0, 0.0), (30e-3, 6.0), (40e-3, 6.0), (55e-3, 0.0), (60e-3, 0.0)),
            ),
            Source.dc("V_im", "im", "0", 0.0),
            Source.dc("V_pu", "vdd_pu", "0", 3.3),
        ],
        60e-3,
        tmax=5e-6,
        save=("V(vin_s)", "V(vin)", "V(vout)", "V(vout_l)", "V(fb)", "V(pg)"),
    )
    raw = _simulate(tmp_path, ltspice_exe, lib, "uvlo", spec)
    t = _t(raw)
    vin = raw.column("V(vin)")
    vout = raw.column("V(vout_l)")
    i_in = (raw.column("V(vin_s)") - raw.column("V(vin)")) / 1e-3

    t_rise = 30e-3 * 4.3 / 6.0  # VIN crosses UVLO_RISE on the 0.2 V/ms ramp
    t_fall = 40e-3 + 15e-3 * (6.0 - 3.9) / 6.0  # VIN crosses UVLO_FALL on the 0.4 V/ms ramp
    starts = _crossings(t, vout, 0.2, "rise")
    assert starts, "output never started"
    t_start = starts[0]
    # measured: t_start - t_rise = 0.30 ms (the soft start needs 0.29 ms to 0.2 V)
    assert 0.0 <= t_start - t_rise <= 0.6e-3, f"started {t_start - t_rise} s after UVLO_RISE"
    v_start = _at(t, vin, t_start)
    # measured: V(vin) at the start = 4.36 V (UVLO_RISE 4.3 V + 0.2 V/ms x 0.30 ms)
    assert abs(v_start - 4.3) <= 0.25, f"start threshold measured {v_start:.3f} V"
    assert float(_window(t, vout, 0.0, t_rise).max()) < 0.1, "output moved below UVLO_RISE"

    collapse = [x for x in _crossings(t, vout, 3.0, "fall") if x > 40e-3]
    assert collapse, "output never collapsed"
    t_stop = collapse[0]
    # measured: t_stop - t_fall = 12 us (the discharge sweeps 0.27 V in 18 us)
    assert 0.0 <= t_stop - t_fall <= 0.3e-3, f"stopped {t_stop - t_fall} s after UVLO_FALL"
    v_stop = _at(t, vin, t_stop)
    # measured: V(vin) at the stop = 3.895 V for UVLO_FALL = 3.9 V
    assert abs(v_stop - 3.9) <= 0.10, f"stop threshold measured {v_stop:.3f} V"
    hysteresis = v_start - v_stop
    # measured: 0.47 V for UVLO_RISE - UVLO_FALL = 0.4 V (the rise reading carries the
    # soft-start delay of 0.06 V)
    assert abs(hysteresis - 0.4) <= 0.3, f"hysteresis measured {hysteresis:.3f} V"

    assert float(_window(t, vout, 35e-3, 39.9e-3).min()) > 3.2, "output not regulated while running"
    assert float(_window(t, i_in, 50e-3, 60e-3).max()) < 1e-3, "input current while stopped"


# --------------------------------------------------------------------------- #
# behaviour 2: EN threshold, hysteresis and polarity


@pytest.mark.ltspice
def test_enable_threshold_hysteresis_and_polarity(
    tmp_path: Path, ltspice_exe: Path, lib: Path
) -> None:
    """POL_EN=1 starts at EN_RISE and stops at EN_FALL; POL_EN=0 inverts.

    VIN is a fixed 5 V.  For POL_EN=1 the enable pin is a 0 -> 2 V -> 0 V
    triangle at 0.1 V/ms, so a threshold can be read both as a time (the model
    starts 0.3 ms after EN crosses 1.25 V, i.e. the soft-start delay) and as a
    voltage.  For POL_EN=0 the pin steps high first (must stay off) and low
    afterwards (must run).
    """
    spec = _deck(
        lib,
        "buck EN: 0->2->0 V triangle at 0.1 V/ms, POL_EN=1",
        [
            _buck(params={"POL_EN": 1}),
            "R_sns vout vout_l 1m",
            "C_out vout_l 0 22u",
            _load("330"),
            PG_PULLUP,
            SW_TIE,
            *DIV_NOM,
        ],
        [
            Source("V1", ("vin", "0"), "dc", params={"v": 5.0}),
            Source(
                name="V_en",
                terminals=("en", "0"),
                kind="pwl",
                points=((0.0, 0.0), (20e-3, 2.0), (40e-3, 0.0), (45e-3, 0.0)),
            ),
            Source.dc("V_im", "im", "0", 0.0),
            Source.dc("V_pu", "vdd_pu", "0", 3.3),
        ],
        45e-3,
        tmax=1e-6,
        save=("V(en)", "V(vout)", "V(vout_l)", "V(fb)", "V(pg)"),
    )
    raw = _simulate(tmp_path, ltspice_exe, lib, "en_high", spec)
    t = _t(raw)
    vout = raw.column("V(vout_l)")
    ven = raw.column("V(en)")

    t_en_rise = 20e-3 * 1.25 / 2.0
    t_en_fall = 20e-3 + 20e-3 * (2.0 - 1.15) / 2.0
    starts = [x for x in _crossings(t, vout, 0.2, "rise")]
    assert starts, "output never started"
    t_start = starts[0]
    assert 0.0 <= t_start - t_en_rise <= 0.6e-3, f"started {t_start - t_en_rise} s after EN_RISE"
    # measured: V(en) at the start = 1.28 V for EN_RISE = 1.25 V
    assert abs(_at(t, ven, t_start) - 1.25) <= 0.10, f"rise measured {_at(t, ven, t_start):.3f} V"
    collapse = [x for x in _crossings(t, vout, 3.0, "fall") if x > t_en_fall - 1e-3]
    assert collapse, "output never collapsed"
    t_stop = collapse[0]
    assert 0.0 <= t_stop - t_en_fall <= 0.3e-3, f"stopped {t_stop - t_en_fall} s after EN_FALL"
    # measured: V(en) at the stop = 1.148 V for EN_FALL = 1.15 V
    assert abs(_at(t, ven, t_stop) - 1.15) <= 0.05, f"fall measured {_at(t, ven, t_stop):.3f} V"
    assert float(_window(t, vout, 2e-3, 12.4e-3).max()) < 0.1, "ran before EN_RISE"
    assert float(_window(t, vout, 18e-3, 28.4e-3).min()) > 3.2, "not regulated after EN_RISE"

    spec = _deck(
        lib,
        "buck EN: POL_EN=0, pin high first then low",
        [
            _buck(params={"POL_EN": 0}),
            "R_sns vout vout_l 1m",
            "C_out vout_l 0 22u",
            _load("330"),
            PG_PULLUP,
            SW_TIE,
            *DIV_NOM,
        ],
        [
            Source("V1", ("vin", "0"), "dc", params={"v": 5.0}),
            Source(
                name="V_en",
                terminals=("en", "0"),
                kind="pwl",
                points=((0.0, 3.3), (15e-3, 3.3), (15.05e-3, 0.0), (40e-3, 0.0)),
            ),
            Source.dc("V_im", "im", "0", 0.0),
            Source.dc("V_pu", "vdd_pu", "0", 3.3),
        ],
        40e-3,
        tmax=1e-6,
        save=("V(en)", "V(vout)", "V(vout_l)"),
    )
    raw = _simulate(tmp_path, ltspice_exe, lib, "en_low", spec)
    t = _t(raw)
    vout = raw.column("V(vout_l)")
    # measured: 0.0 V with the pin high (3.3 V) and 3.269 V after it goes low
    assert float(_window(t, vout, 2e-3, 15e-3).max()) < 0.1, "active-low part ran with EN high"
    assert float(_window(t, vout, 30e-3, 40e-3).min()) > 3.2, (
        "active-low part did not run with EN low"
    )


# --------------------------------------------------------------------------- #
# behaviour 3: soft start


@pytest.mark.ltspice
def test_soft_start_ramp_law_monotonic_and_no_overshoot(
    tmp_path: Path, ltspice_exe: Path, lib: Path
) -> None:
    """dV(FB)/dt = ISS/CSS, monotonic, and never above VREF.

    Two decks differ only in CSS (10 nF and 40 nF), so the measured slope of
    V(FB) during the ramp must differ by exactly the capacitance ratio.  The
    reference itself is clamped by ``min(V(ss), VREF)``, so an overshoot above
    VREF would be a real defect.
    """
    slopes: dict[float, float] = {}
    for css in (10e-9, 40e-9):
        name = f"softstart_{int(css * 1e9)}n"
        spec = _deck(
            lib,
            f"buck soft start with CSS={css * 1e9:g} nF",
            [
                _buck(params={"CSS": css}),
                "R_sns vout vout_l 1m",
                "C_out vout_l 0 22u",
                _load("330"),
                PG_PULLUP,
                SW_TIE,
                EN_TIE,
                *DIV_NOM,
            ],
            [
                Source.ramp("V1", "vin", "0", v0=0.0, v1=5.0, rise_s=2e-3, hold_s=60e-3),
                Source.dc("V_im", "im", "0", 0.0),
                Source.dc("V_pu", "vdd_pu", "0", 3.3),
            ],
            60e-3,
            tmax=2e-6,
            save=("V(vin)", "V(vout)", "V(vout_l)", "V(fb)"),
        )
        raw = _simulate(tmp_path, ltspice_exe, lib, name, spec)
        t = _t(raw)
        fb = raw.column("V(fb)")
        vout = raw.column("V(vout_l)")
        starts = _crossings(t, vout, 0.2, "rise")
        assert starts, f"{name}: output never started"
        # window well inside the linear ramp (V(FB) 0.30 V .. 0.50 V = 37 % .. 63 % of VREF)
        lo = starts[0] + 0.30 * 10e-9 / ISS * (css / 10e-9)
        hi = starts[0] + 0.50 * 10e-9 / ISS * (css / 10e-9)
        slope = _slope(t, fb, lo, hi)
        slopes[css] = slope
        expected = ISS / css
        # measured: 169.5 V/s for CSS=10n and 42.4 V/s for CSS=40n (law: 170 / 42.5)
        assert abs(slope - expected) / expected <= 0.10, f"{name}: slope {slope:.2f} V/s"

        ramp = _window(t, fb, starts[0], starts[0] + 0.9 * css / ISS * 0.8)
        steps = np.diff(ramp)
        # measured: the smallest step is +89 uV (no numerical dip on a 0.6 mV step)
        assert float(steps.min()) > -2e-4, f"{name}: ramp not monotonic ({steps.min():.2e} V)"
        # measured: max V(FB) = 0.8072 V (VREF 0.8 V, +0.9 %); the reference is clamped
        assert float(fb.max()) <= 0.8 * 1.02, f"{name}: overshoot to {fb.max():.4f} V"
        assert abs(fb[-1] - 0.8) <= 0.016, f"{name}: settled at {fb[-1]:.5f} V"

    ratio = slopes[10e-9] / slopes[40e-9]
    # measured: 4.00 for the CSS ratio of 4
    assert abs(ratio - 4.0) <= 0.4, f"CSS ratio measured {ratio:.2f}"


# --------------------------------------------------------------------------- #
# behaviour 4: regulation through the external divider


@pytest.mark.ltspice
def test_regulation_follows_external_feedback_divider(
    tmp_path: Path, ltspice_exe: Path, lib: Path
) -> None:
    """Steady state is V(FB) = VREF, so the output follows whatever divider is fitted.

    10k/3.24k must settle at 3.2716 V while 30k/3.24k must settle at 8.210 V -
    the model has no internal fixed output voltage to hide a wrong divider.
    """
    measured: dict[str, float] = {}
    for label, divider, expect in (("nominal", DIV_NOM, 3.2716), ("wrong", DIV_WRONG, 8.210)):
        spec = _deck(
            lib,
            f"buck regulation with the {label} divider, VIN 12 V",
            [
                _buck(),
                "R_insns vin_s vin 1m",
                "R_sns vout vout_l 1m",
                "C_out vout_l 0 22u",
                _load("330"),
                PG_PULLUP,
                SW_TIE,
                EN_TIE,
                *divider,
            ],
            [
                Source.ramp("V1", "vin_s", "0", v0=0.0, v1=12.0, rise_s=2e-3, hold_s=28e-3),
                Source.dc("V_im", "im", "0", 0.0),
                Source.dc("V_pu", "vdd_pu", "0", 3.3),
            ],
            30e-3,
            save=("V(vin_s)", "V(vin)", "V(vout)", "V(vout_l)", "V(fb)"),
        )
        raw = _simulate(tmp_path, ltspice_exe, lib, f"divider_{label}", spec)
        t = _t(raw)
        vout = raw.column("V(vout_l)")
        fb = raw.column("V(fb)")
        settles = float(np.mean(_window(t, vout, 25e-3, 30e-3)))
        measured[label] = settles
        error = (settles - expect) / expect
        # measured: 3.26913 V for 3.2716 V (-0.076 %) and 8.2015 V for 8.210 V (-0.10 %)
        assert abs(error) <= 0.02, f"{label}: settled at {settles:.4f} V vs {expect:.4f} V"
        fb_final = float(np.mean(_window(t, fb, 25e-3, 30e-3)))
        assert abs(fb_final - 0.8) <= 0.008, f"{label}: V(FB) = {fb_final:.5f} V vs VREF 0.8 V"

    # the wrong divider must not be hidden by any internal 3.3 V target
    assert measured["wrong"] > 7.5, f"wrong divider settled at {measured['wrong']:.3f} V"
    assert measured["nominal"] < 3.6


@pytest.mark.ltspice
def test_ldo_template_regulates_through_the_divider(
    tmp_path: Path, ltspice_exe: Path, lib: Path
) -> None:
    """The LDO text is a real, simulatable model: 3.3 V in, divider-set output."""
    spec = _deck(
        lib,
        "LDO regulation: VIN 3.3 V, 10k/3.24k divider",
        [
            _ldo(),
            "R_insns vin_s vin 1m",
            "R_sns vout vout_l 1m",
            "C_out vout_l 0 22u",
            _load("330"),
            PG_PULLUP,
            EN_TIE,
            *DIV_NOM,
        ],
        [
            Source.ramp("V1", "vin_s", "0", v0=0.0, v1=3.3, rise_s=2e-3, hold_s=28e-3),
            Source.dc("V_pu", "vdd_pu", "0", 3.3),
        ],
        30e-3,
        save=("V(vin_s)", "V(vin)", "V(vout)", "V(vout_l)", "V(fb)", "V(pg)"),
    )
    raw = _simulate(tmp_path, ltspice_exe, lib, "ldo_regulation", spec)
    t = _t(raw)
    vout = raw.column("V(vout_l)")
    fb = raw.column("V(fb)")
    settles = float(np.mean(_window(t, vout, 25e-3, 30e-3)))
    # measured: 3.26917 V for the 3.2716 V target (-0.075 %)
    assert abs(settles - 3.2716) / 3.2716 <= 0.02, f"LDO settled at {settles:.4f} V"
    assert abs(float(np.mean(_window(t, fb, 25e-3, 30e-3))) - 0.8) <= 0.008, "LDO V(FB) != VREF"
    # measured: PG released to 3.3 V once the output is inside the window
    assert float(_window(t, raw.column("V(pg)"), 25e-3, 30e-3).min()) > 3.0, "LDO PG not released"
    # the LDO is not a switching model, and the buck is
    assert is_switching("BM_REG_BUCK") and not is_switching("BM_REG_LDO")


# --------------------------------------------------------------------------- #
# behaviour 5: current limit


def _overload(at: float, ohms: float = 0.5) -> str:
    """A load that steps to ``ohms`` (a hard overload) at ``at``."""
    return f"B_ld vout_l 0 I = V(vout_l)*limit((time-{at:g})/1u,0,1)/{ohms:g}"


@pytest.mark.ltspice
def test_current_limit_hiccup_retries_every_retry_ms(
    tmp_path: Path, ltspice_exe: Path, lib: Path
) -> None:
    """The output current never exceeds ILIM and, in ILIM_MODE=0, retries periodically.

    A 0.5 ohm overload is switched in at 8 ms.  The measured burst current is the
    current limit itself; the measured interval between bursts is the hiccup
    period (RETRY_MS plus the ~0.2 ms the memory needs to re-trip).
    """
    spec = _deck(
        lib,
        "buck current limit, hiccup (ILIM_MODE=0), 0.5 ohm overload at 8 ms",
        [
            _buck(params={"ILIM_MODE": 0}),
            "R_sns vout vout_l 1m",
            "C_out vout_l 0 22u",
            _overload(8e-3),
            PG_PULLUP,
            SW_TIE,
            EN_TIE,
            *DIV_NOM,
        ],
        [
            Source.ramp("V1", "vin", "0", v0=0.0, v1=5.0, rise_s=2e-3, hold_s=48e-3),
            Source.dc("V_im", "im", "0", 0.0),
            Source.dc("V_pu", "vdd_pu", "0", 3.3),
        ],
        50e-3,
        save=("V(vout)", "V(vout_l)", "V(pg)"),
    )
    raw = _simulate(tmp_path, ltspice_exe, lib, "ilim_hiccup", spec)
    t = _t(raw)
    i_out = (raw.column("V(vout)") - raw.column("V(vout_l)")) / 1e-3
    vout = raw.column("V(vout_l)")

    rest = _window(t, i_out, 6e-3, 7.9e-3)
    # measured: 10.1 mA before the step
    assert float(rest.max()) < 0.1, f"load current before the step: {rest.max():.3f} A"
    bursts = _window(t, i_out, 8.2e-3, 50e-3)
    # measured: bursts peak at 3.000 A for ILIM = 3.0 A (the limit is exact)
    assert float(bursts.max()) <= 3.0 * 1.001, f"limit exceeded: {bursts.max():.4f} A"
    assert float(bursts.max()) >= 3.0 * 0.98, f"limit never reached: {bursts.max():.4f} A"
    # measured: the output collapses to 0 V between bursts
    assert float(_window(t, vout, 12e-3, 50e-3).min()) < 0.2, "output never collapsed"

    # a burst is a pulse of the model's own output current at the limit
    pulses = [x for x in _crossings(t, i_out, 1.0, "rise") if x > 9e-3]
    assert len(pulses) >= 3, f"only {len(pulses)} retry bursts observed"
    periods = np.diff(pulses)
    typical = float(np.median(periods))
    gaps = _window(t, i_out, pulses[0] + 0.5e-3, pulses[1] - 0.5e-3)
    # measured: 0 A between bursts (the drive is gated off for the whole interval)
    assert float(gaps.max()) < 0.1, f"drive not off between bursts: {gaps.max():.3f} A"
    # measured: 8.19 ms for RETRY_MS = 8 ms (RETRY_MS + the 0.18 ms re-trip ramp)
    assert abs(typical - 8e-3) <= 0.25 * 8e-3, f"hiccup period measured {typical * 1e3:.3f} ms"


@pytest.mark.ltspice
def test_current_limit_latch_requires_enable_cycle(
    tmp_path: Path, ltspice_exe: Path, lib: Path
) -> None:
    """ILIM_MODE=1 latches off: removing the overload is not enough, EN must be cycled.

    The 0.5 ohm overload is in from 8 ms to 20 ms; from 22 ms to 30 ms the model
    must still be off, and only the EN low pulse at 30 ms may restart it.
    """
    spec = _deck(
        lib,
        "buck current limit, latch (ILIM_MODE=1), overload 8-20 ms, EN pulse at 30 ms",
        [
            _buck(params={"ILIM_MODE": 1}),
            "R_sns vout vout_l 1m",
            "C_out vout_l 0 22u",
            "B_ld vout_l 0 I = V(vout_l)*(limit((time-8m)/1u,0,1)-limit((time-20m)/1u,0,1))/0.5",
            PG_PULLUP,
            SW_TIE,
            *DIV_NOM,
        ],
        [
            Source.ramp("V1", "vin", "0", v0=0.0, v1=5.0, rise_s=2e-3, hold_s=53e-3),
            Source(
                name="V_en",
                terminals=("en", "0"),
                kind="pwl",
                points=(
                    (0.0, 3.3),
                    (30e-3, 3.3),
                    (30.05e-3, 0.0),
                    (30.8e-3, 0.0),
                    (30.85e-3, 3.3),
                    (55e-3, 3.3),
                ),
            ),
            Source.dc("V_im", "im", "0", 0.0),
            Source.dc("V_pu", "vdd_pu", "0", 3.3),
        ],
        55e-3,
        save=("V(en)", "V(vout)", "V(vout_l)", "V(pg)"),
    )
    raw = _simulate(tmp_path, ltspice_exe, lib, "ilim_latch", spec)
    t = _t(raw)
    i_out = (raw.column("V(vout)") - raw.column("V(vout_l)")) / 1e-3
    vout = raw.column("V(vout_l)")
    ven = raw.column("V(en)")

    # measured: the burst peaks at 3.000 A and stops (no second burst before 30 ms)
    assert float(_window(t, i_out, 8e-3, 30e-3).max()) <= 3.0 * 1.001, "limit exceeded"
    bursts = [x for x in _crossings(t, i_out, 1.0, "rise") if 8e-3 < x < 30e-3]
    assert len(bursts) == 1, f"{len(bursts)} bursts observed; a latch must produce one"
    # measured: 0.000 V and 0 A while latched, even with the overload removed at 20 ms
    assert float(_window(t, vout, 22e-3, 30e-3).max()) < 0.5, "latched state did not hold"
    assert float(_window(t, i_out, 22e-3, 30e-3).max()) < 1e-3, "latched state still driving"

    # the enable pulse is the only reset: the model restarts with a full soft ramp
    # (measured: VOUT 0.5 -> 3.0 V in 3.6 ms, i.e. the ISS/CSS ramp) and is back at
    # 3.269 V by 50 ms
    assert float(_window(t, ven, 30.05e-3, 30.8e-3).max()) < 1.15, (
        "EN pulse did not go below EN_FALL"
    )
    after = [x for x in _crossings(t, vout, 0.5, "rise") if x > 31e-3]
    assert after, "no restart after the EN cycle"
    reach = [x for x in _crossings(t, vout, 3.0, "rise") if x > after[0]]
    assert reach, "restart did not reach the regulation value"
    ramp = reach[0] - after[0]
    assert ramp >= 2e-3, f"restart was not a soft ramp ({ramp * 1e3:.3f} ms 0.5 -> 3.0 V)"
    assert float(_window(t, vout, 45e-3, 55e-3).min()) > 3.2, "no recovery after the EN cycle"


# --------------------------------------------------------------------------- #
# behaviour 6: power good


@pytest.mark.ltspice
def test_power_good_window_delay_open_drain_and_sink_only(
    tmp_path: Path, ltspice_exe: Path, lib: Path
) -> None:
    """PG is high impedance only inside the window and EN active, else pulled low.

    PG_DELAY is set to 200 us (proving it is a real parameter) and measured from
    VOUT entering the window (|VOUT - VOUT_NOM| = 8.5 % => 3.0195 V) to the pin
    rising.  The implied pull-down resistance is V(PG)/I(pull-up) in the low
    state, and the pull-up current is checked never to flow backwards.
    """
    delay = 200e-6
    spec = _deck(
        lib,
        "buck power good: PG_DELAY=200us, EN pulse, 10k pull-up to 3.3 V",
        [
            _buck(params={"PG_DELAY": delay}),
            "R_sns vout vout_l 1m",
            "C_out vout_l 0 22u",
            _load("330"),
            PG_PULLUP,
            SW_TIE,
            *DIV_NOM,
        ],
        [
            Source.ramp("V1", "vin", "0", v0=0.0, v1=5.0, rise_s=2e-3, hold_s=38e-3),
            Source(
                name="V_en",
                terminals=("en", "0"),
                kind="pwl",
                points=((0.0, 3.3), (30e-3, 3.3), (30.02e-3, 0.0), (40e-3, 0.0)),
            ),
            Source.dc("V_im", "im", "0", 0.0),
            Source.dc("V_pu", "vdd_pu", "0", 3.3),
        ],
        40e-3,
        save=("V(en)", "V(vout)", "V(vout_l)", "V(pg)"),
    )
    raw = _simulate(tmp_path, ltspice_exe, lib, "pg", spec)
    t = _t(raw)
    vout = raw.column("V(vout_l)")
    pg = raw.column("V(pg)")
    i_pu = (3.3 - pg) / 10e3

    # the pin never sources: the pull-up current is never negative, and the pin never
    # exceeds the pull-up rail (measured: min -1e-13 A, max V(PG) 3.3000 V)
    assert float(i_pu.min()) > -1e-9, f"PG sourced current: {i_pu.min():.3e} A"
    assert float(pg.max()) <= 3.3 + 1e-6, f"PG above its pull-up rail: {pg.max():.6f} V"
    # measured: 16.3 mV low before the output is up, 3.300 V after the window
    assert float(_window(t, pg, 1e-3, 2e-3).max()) < 0.2, "PG not low before the output is up"
    assert float(_window(t, pg, 25e-3, 29e-3).min()) > 3.0, "PG not released inside the window"

    enter = _crossings(t, vout, 3.3 * 0.915, "rise")[0]
    rises = [x for x in _crossings(t, pg, 1.65, "rise") if x > enter]
    assert rises, "PG never asserted"
    measured = rises[0] - enter
    # measured: 0.206 ms for PG_DELAY = 0.2 ms (the assertion is the delayed edge)
    assert abs(measured - delay) <= 0.35 * delay, f"PG_DELAY measured {measured * 1e6:.1f} us"

    # the low state is t < 3 ms (output not yet up): measured V(PG) = 16.3 mV through
    # the 10 k pull-up, i.e. V(PG)/I(pull-up) = 49.7 ohm for the documented 50 ohm
    low = int(np.searchsorted(t, 1.5e-3))
    r_pd = float(pg[low] / i_pu[low])
    assert abs(r_pd - 50.0) <= 10.0, f"pull-down measured {r_pd:.1f} ohm"

    # leaving the window (disable) must pull the pin low immediately: measured < 0.2 ms
    t_off = 30.02e-3 + 0.98 / 3.3 * 20e-6
    after = _window(t, pg, t_off + 0.2e-3, 40e-3)
    assert float(after.max()) < 0.2, f"PG not low after disable: {after.max():.3f} V"


# --------------------------------------------------------------------------- #
# behaviour 7: output discharge when disabled


@pytest.mark.ltspice
def test_output_discharge_when_disabled(tmp_path: Path, ltspice_exe: Path, lib: Path) -> None:
    """Disabled, RDISCHARGE pulls VOUT down with the documented value mapping.

    The discharge current is measured through the pin current at the moment EN
    falls: for RDISCHARGE = 10 ohm it is V(VOUT)/10, for 40 ohm a quarter of
    that, and a request below the documented 10 ohm floor is clamped (the same
    current as 10 ohm, never more).
    """
    currents: dict[float, float] = {}
    for rdis in (10.0, 40.0, 2.0):
        name = f"discharge_{int(rdis)}"
        spec = _deck(
            lib,
            f"buck discharge: RDISCHARGE={rdis:g}, EN low at 20 ms, no external load",
            [
                _buck(params={"RDISCHARGE": rdis}),
                "R_sns vout vout_l 1m",
                "C_out vout_l 0 22u",
                _load("10k"),
                PG_PULLUP,
                SW_TIE,
                *DIV_NOM,
            ],
            [
                Source.ramp("V1", "vin", "0", v0=0.0, v1=5.0, rise_s=2e-3, hold_s=23e-3),
                Source(
                    name="V_en",
                    terminals=("en", "0"),
                    kind="pwl",
                    points=((0.0, 3.3), (20e-3, 3.3), (20.02e-3, 0.0), (25e-3, 0.0)),
                ),
                Source.dc("V_im", "im", "0", 0.0),
                Source.dc("V_pu", "vdd_pu", "0", 3.3),
            ],
            25e-3,
            save=("V(en)", "V(vout)", "V(vout_l)"),
        )
        raw = _simulate(tmp_path, ltspice_exe, lib, name, spec)
        t = _t(raw)
        vout = raw.column("V(vout_l)")
        i_out = (raw.column("V(vout)") - raw.column("V(vout_l)")) / 1e-3
        before = float(np.mean(_window(t, vout, 18e-3, 19.9e-3)))
        assert abs(before - 3.2716) / 3.2716 <= 0.02, (
            f"{name}: not regulated before ({before:.4f} V)"
        )

        sample = int(np.searchsorted(t, 20.02e-3 + 20e-6))
        r_eff = max(rdis, 10.0)
        measured = float(-i_out[sample])
        currents[rdis] = measured
        expected = before / r_eff - before / 10e3  # model sinks, 10 k load still draws
        # measured: 310 mA for 10 ohm, 78 mA for 40 ohm, 310 mA for the clamped 2 ohm
        assert abs(measured - expected) / expected <= 0.15, (
            f"{name}: discharge {measured * 1e3:.1f} mA vs {expected * 1e3:.1f} mA"
        )
        assert float(_window(t, vout, 24e-3, 25e-3).max()) < 0.1, f"{name}: did not discharge"

    # measured: 310 mA / 78 mA = 3.98 for a 40/10 ohm ratio of 4
    assert abs(currents[10.0] / currents[40.0] - 4.0) <= 0.4, "RDISCHARGE mapping is not 1/R"
    # measured: 2 ohm gives the same current as 10 ohm (the documented floor)
    assert abs(currents[2.0] - currents[10.0]) / currents[10.0] <= 0.05, "floor not applied"


# --------------------------------------------------------------------------- #
# behaviour 8: pre-bias start-up


@pytest.mark.ltspice
def test_prebias_start_does_not_sink_and_holds_off(
    tmp_path: Path, ltspice_exe: Path, lib: Path
) -> None:
    """A pre-biased output is never discharged and the drive waits for the ramp.

    The converter is enabled (VIN and EN at 5 V from t = 0, above UVLO_RISE) and
    the output capacitor is initialised to 1.5 V, loaded only by 1 Mohm so any
    droop in the measurement can only come from the model sinking.  V(FB) is then
    0.367 V, which the reference reaches 2.16 ms after t = 0 (0.367 V x CSS/ISS),
    so the model must neither sink nor drive before that and must drive after it.
    """
    prebias = 1.5
    spec = _deck(
        lib,
        "buck pre-bias: enabled, VOUT initialised to 1.5 V, 1 Mohm load",
        [
            _buck(),
            "R_sns vout vout_l 1m",
            "C_out vout_l 0 22u",
            _load("1meg"),
            PG_PULLUP,
            SW_TIE,
            EN_TIE,
            *DIV_NOM,
        ],
        [
            Source.dc("V1", "vin", "0", 5.0),
            Source.dc("V_im", "im", "0", 0.0),
            Source.dc("V_pu", "vdd_pu", "0", 3.3),
        ],
        25e-3,
        ics={"vout_l": prebias},
        save=("V(vin)", "V(vout)", "V(vout_l)", "V(fb)"),
    )
    raw = _simulate(tmp_path, ltspice_exe, lib, "prebias", spec)
    t = _t(raw)
    vout = raw.column("V(vout_l)")
    fb = raw.column("V(fb)")
    i_out = (raw.column("V(vout)") - raw.column("V(vout_l)")) / 1e-3

    # measured: the pin current never goes below -2e-13 A over the whole run
    assert float(i_out.min()) > -1e-9, f"model sank {i_out.min():.3e} A from the pre-bias"
    t_cross = prebias * 0.8 / 3.2716 * 10e-9 / ISS
    # measured: VOUT holds 1.5 V; the 9.7 mV droop over the window is the external
    # 13.24 k divider discharging the node at 113 uA (5.1 V/s), not the model - the
    # model's own pin current stays below 1e-5 A until the ramp reaches V(FB)
    assert float(_window(t, vout, 20e-6, t_cross - 0.3e-3).min()) > prebias - 0.02
    assert float(_window(t, i_out, 20e-6, t_cross - 0.3e-3).max()) < 1e-5
    # measured: the drive starts at 2.19 ms against the 2.16 ms prediction, and the
    # output then rises to the regulation value
    drivings = [x for x in _crossings(t, i_out, 1e-3, "rise")]
    assert drivings, "model never drove the output"
    assert abs(drivings[0] - t_cross) <= 0.5e-3, (
        f"drive started at {drivings[0] * 1e3:.3f} ms, predicted {t_cross * 1e3:.3f} ms"
    )
    assert float(vout[-1]) > 3.2, f"output did not recover ({vout[-1]:.4f} V)"
    assert abs(float(fb[-1]) - 0.8) <= 0.016, f"final V(FB) {fb[-1]:.5f} V"


# --------------------------------------------------------------------------- #
# behaviour 9: reverse-current blocking


@pytest.mark.ltspice
def test_reverse_current_blocking(tmp_path: Path, ltspice_exe: Path, lib: Path) -> None:
    """REVERSE_BLOCK=1: no current can flow from VOUT back into VIN.

    VIN is held at 4.5 V (above UVLO_RISE) and an external source drives VOUT to
    6 V from 10 ms, so the output sits above the input.  The input-pin current is
    measured through a 1 mohm sense resistor.  The REVERSE_BLOCK=0 variant shows
    what the parameter switches: the same deck then back-feeds the input rail.
    """
    currents: dict[int, float] = {}
    for block in (1, 0):
        spec = _deck(
            lib,
            f"buck reverse: VOUT driven to 6 V from 10 ms, REVERSE_BLOCK={block}",
            [
                _buck(params={"REVERSE_BLOCK": block}),
                "R_insns vin_s vin 1m",
                "R_sns vout vout_l 1m",
                "C_out vout_l 0 22u",
                "R_ext vout_ext vout_l 0.1",
                _load("330"),
                PG_PULLUP,
                SW_TIE,
                EN_TIE,
                *DIV_NOM,
            ],
            [
                Source.ramp("V1", "vin_s", "0", v0=0.0, v1=4.5, rise_s=2e-3, hold_s=23e-3),
                Source.ramp(
                    "V2", "vout_ext", "0", v0=0.0, v1=6.0, delay_s=10e-3, rise_s=1e-3, hold_s=14e-3
                ),
                Source.dc("V_im", "im", "0", 0.0),
                Source.dc("V_pu", "vdd_pu", "0", 3.3),
            ],
            25e-3,
            save=("V(vin_s)", "V(vin)", "V(vout)", "V(vout_l)"),
        )
        raw = _simulate(tmp_path, ltspice_exe, lib, f"reverse_{block}", spec)
        t = _t(raw)
        i_in = (raw.column("V(vin_s)") - raw.column("V(vin)")) / 1e-3
        vout = raw.column("V(vout_l)")
        assert float(_window(t, vout, 12e-3, 22e-3).min()) > 5.5, "VOUT was not driven above VIN"
        steady = _window(t, i_in, 12e-3, 22e-3)
        currents[block] = float(steady.min())

    # measured: -1.4e-17 A with REVERSE_BLOCK=1 (the pin cannot source at all)
    assert currents[1] > -1e-9, f"reverse current {currents[1]:.3e} A with blocking"
    # measured: -0.488 A with REVERSE_BLOCK=0 - the sink side is enabled and the input
    # current follows -POUT/ETA/VIN (I_sink = 0.5 x the 0.668 V over-voltage error)
    assert currents[0] < -0.05, (
        f"REVERSE_BLOCK=0 shows no reverse path ({currents[0]:.3e} A); the blocked case "
        "would be vacuous"
    )


# --------------------------------------------------------------------------- #
# behaviour 10: input current law and energy balance


@pytest.mark.ltspice
def test_input_current_law_and_no_energy_creation(
    tmp_path: Path, ltspice_exe: Path, lib: Path
) -> None:
    """IIN = clamp(POUT/(ETA*max(VIN,VMIN_FLOOR)),0,IIN_MAX) and no energy creation.

    A 3.3 ohm load (about 1 A) is driven from 12 V.  Both port currents are
    measured through 1 mohm shunts, so the input power, the output power and the
    two energies are independent measurements of the same law.
    """
    spec = _deck(
        lib,
        "buck input current: VIN 12 V, 3.3 ohm load (about 1 A)",
        [
            _buck(),
            "R_insns vin_s vin 1m",
            "R_sns vout vout_l 1m",
            "C_out vout_l 0 22u",
            _load("3.3"),
            PG_PULLUP,
            SW_TIE,
            EN_TIE,
            *DIV_NOM,
        ],
        [
            Source.ramp("V1", "vin_s", "0", v0=0.0, v1=12.0, rise_s=2e-3, hold_s=18e-3),
            Source.dc("V_im", "im", "0", 0.0),
            Source.dc("V_pu", "vdd_pu", "0", 3.3),
        ],
        20e-3,
        save=("V(vin_s)", "V(vin)", "V(vout)", "V(vout_l)"),
    )
    raw = _simulate(tmp_path, ltspice_exe, lib, "input_current", spec)
    t = _t(raw)
    v_in = raw.column("V(vin)")
    i_in = (raw.column("V(vin_s)") - v_in) / 1e-3
    v_out = raw.column("V(vout_l)")
    i_out = (raw.column("V(vout)") - raw.column("V(vout_l)")) / 1e-3

    p_in = v_in * i_in
    p_out = v_out * i_out
    eta = 0.9

    # the law itself, sample by sample in the steady state: measured worst deviation
    # 0.002 % of POUT from POUT/ETA (E(vin) >= 1 V here, so the floor is inactive)
    lo = int(np.searchsorted(t, 10e-3))
    hi = int(np.searchsorted(t, 15e-3))
    law = np.abs(p_in[lo:hi] - p_out[lo:hi] / eta) / np.maximum(p_out[lo:hi], 1e-12)
    assert float(law.max()) <= 0.02, f"input power law deviates by {law.max():.4f}"
    # measured: 0.995 A of output current for a 3.3 ohm load (POUT about 3.2 W)
    assert float(_window(t, i_out, 10e-3, 15e-3).min()) > 0.9, "load current too small to test"
    assert float(np.abs(_window(t, i_in, 0.0, 20e-3)).max()) <= 5.0, "IIN above IIN_MAX"

    def energy(start: float, stop: float) -> tuple[float, float]:
        a = int(np.searchsorted(t, start))
        b = int(np.searchsorted(t, stop))
        e_in = float(np.trapezoid(v_in[a:b] * i_in[a:b], t[a:b]))
        e_out = float(np.trapezoid(v_out[a:b] * i_out[a:b], t[a:b]))
        return e_in, e_out

    e_in, e_out = energy(5e-3, 19e-3)
    # measured: EOUT = 46.36 mJ = 0.8999 x EIN (ETA = 0.9); the model never creates
    # energy, and it does transfer energy (the check would be vacuous otherwise)
    assert e_out <= eta * e_in * 1.05, (
        f"energy created: EOUT {e_out:.6f} > ETA*EIN {eta * e_in:.6f}"
    )
    assert e_out > 0.5 * eta * e_in, f"not enough energy transferred to test: {e_out:.6f} J"


# --------------------------------------------------------------------------- #
# pure-logic checks (no simulator)


def test_port_and_parameter_tables_match_the_specification() -> None:
    """The documented port order and defaults are the contract; they must not drift."""
    assert REGULATOR_PORT_ORDER == {
        "BM_REG_BUCK": ("VIN", "EN", "FB", "PG", "VOUT", "GND", "SW", "ILIM_MODE"),
        "BM_REG_LDO": ("VIN", "EN", "FB", "PG", "VOUT", "GND"),
    }
    common = {
        "VREF": 0.8,
        "VOUT_NOM": 3.3,
        "EN_RISE": 1.25,
        "EN_FALL": 1.15,
        "POL_EN": 1,
        "CSS": 10e-9,
        "ILIM": 3.0,
        "ILIM_MODE": 0,
        "RETRY_MS": 8e-3,
        "RDISCHARGE": 10,
        "VPREBIAS_MAX": 0.05,
        "REVERSE_BLOCK": 1,
        "ETA": 0.9,
        "VMIN_FLOOR": 1,
        "IIN_MAX": 5,
        "GM": 50,
    }
    assert REGULATOR_PARAMS["BM_REG_BUCK"] == {
        **common,
        "UVLO_RISE": 4.3,
        "UVLO_FALL": 3.9,
    }
    assert REGULATOR_PARAMS["BM_REG_LDO"] == {
        **common,
        "UVLO_RISE": 2.2,
        "UVLO_FALL": 2.0,
    }
    assert REGULATOR_EXTRA_PARAMS == {
        "BM_REG_BUCK": {"PG_DELAY": 100e-6},
        "BM_REG_LDO": {"PG_DELAY": 100e-6},
    }
    assert {kind: is_switching(kind) for kind in REGULATOR_PORT_ORDER} == {
        "BM_REG_BUCK": True,
        "BM_REG_LDO": False,
    }
    with pytest.raises(KeyError):
        is_switching("BM_REG_NOPE")


def test_emitted_text_is_deterministic_and_self_contained(tmp_path: Path) -> None:
    """Same arguments -> byte-identical text, and every primitive is included."""
    for kind in REGULATOR_PORT_ORDER:
        text = regulator_text(kind)
        assert text == regulator_text(kind)
        assert f".subckt {kind} " in text
        for primitive in REGULATOR_PRIMITIVES[kind]:
            assert f".subckt {primitive} " in text, f"{kind} does not include {primitive}"
            assert primitive_text(primitive) in text

    both = write_regulator_library(tmp_path / "bm_reg.lib")
    first = both.read_bytes()
    again = write_regulator_library(tmp_path / "again.lib")
    assert again.read_bytes() == first
    assert both.read_bytes() == regulator_library_text().encode()
    text = first.decode()
    for kind in REGULATOR_PORT_ORDER:
        assert text.count(f".subckt {kind} ") == 1
    for primitive in ("BM_SCHMITT", "BM_DELAY"):
        assert text.count(f".subckt {primitive} ") == 1, f"{primitive} duplicated or missing"
    # one kind at a time is still self-contained and is a subset of the full library
    single = write_regulator_library(tmp_path / "bucket.lib", ["BM_REG_BUCK"])
    assert ".subckt BM_REG_LDO" not in single.read_text()
    assert ".subckt BM_SCHMITT" in single.read_text()


def test_subckt_lines_declare_ports_and_defaults_in_order() -> None:
    """Ports appear exactly once, in order, and every default matches the table."""
    for kind, ports in REGULATOR_PORT_ORDER.items():
        text = regulator_text(kind)
        line = next(line for line in text.splitlines() if line.startswith(f".subckt {kind} "))
        head, _, tail = line.partition("params:")
        declared = head.split()[2:]
        assert tuple(declared) == ports, f"{kind} ports {declared} != {ports}"
        declared_params = {
            token.split("=", 1)[0]: _spice_value(token.split("=", 1)[1]) for token in tail.split()
        }
        for name, value in {**REGULATOR_PARAMS[kind], **REGULATOR_EXTRA_PARAMS[kind]}.items():
            assert name in declared_params, f"{kind} does not declare {name}"
            assert declared_params[name] == pytest.approx(value), f"{kind}.{name}"
        assert set(declared_params) == set(_declared(kind)), (
            f"{kind} declares undocumented parameters"
        )

    # an override lands in the text and is reproducible
    overridden = regulator_text("BM_REG_BUCK", extra_params={"CSS": 100e-9, "PG_DELAY": 1e-3})
    assert "CSS=100n" in overridden and "PG_DELAY=1m" in overridden
    assert overridden == regulator_text(
        "BM_REG_BUCK", extra_params={"CSS": 100e-9, "PG_DELAY": 1e-3}
    )
    with pytest.raises(ValueError):
        regulator_text("BM_REG_BUCK", extra_params={"NOPE": 1.0})


def _declared(kind: str) -> tuple[str, ...]:
    return tuple(REGULATOR_PARAMS[kind]) + tuple(REGULATOR_EXTRA_PARAMS[kind])


_SPICE_SUFFIX: dict[str, float] = {
    "t": 1e12,
    "g": 1e9,
    "meg": 1e6,
    "k": 1e3,
    "m": 1e-3,
    "u": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
}


def _spice_value(text: str) -> float:
    """Parse a SPICE number with an engineering suffix ("10n" -> 1e-8)."""
    match = re.fullmatch(r"([-+]?[0-9.]+(?:[eE][-+]?[0-9]+)?)(meg|[tgkmunpf])?", text)
    assert match, f"{text!r} is not a SPICE number"
    number = float(match.group(1))
    suffix = match.group(2)
    return number * (_SPICE_SUFFIX[suffix] if suffix else 1.0)


def test_regulator_instance_wiring_is_validated() -> None:
    """A missing or extra node is a wiring error, never a silently wrong deck."""
    nodes = {
        "VIN": "vin",
        "EN": "en",
        "FB": "fb",
        "PG": "pg",
        "VOUT": "vout",
        "GND": "0",
        "SW": "sw",
        "ILIM_MODE": "im",
    }
    card = regulator_instance("BM_REG_BUCK", "X1", nodes, {"ILIM": 2.0})
    assert card == "X1 vin en fb pg vout 0 sw im BM_REG_BUCK ILIM=2"
    with pytest.raises(ValueError):
        regulator_instance("BM_REG_BUCK", "X1", {k: v for k, v in nodes.items() if k != "SW"})
    with pytest.raises(ValueError):
        regulator_instance("BM_REG_BUCK", "X1", {**nodes, "BOGUS": "x"})
    with pytest.raises(ValueError):
        regulator_instance("BM_REG_BUCK", "X1", nodes, {"ILIMIT": 1.0})
    # the LDO has no SW/ILIM_MODE port at all
    assert (
        regulator_instance(
            "BM_REG_LDO",
            "X2",
            {"VIN": "a", "EN": "b", "FB": "c", "PG": "d", "VOUT": "e", "GND": "f"},
        )
        == "X2 a b c d e f BM_REG_LDO"
    )
    with pytest.raises(ValueError):
        regulator_instance("BM_REG_LDO", "X2", nodes)
