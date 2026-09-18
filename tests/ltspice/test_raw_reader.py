"""``.raw`` reader tests (Phase 0 step 7).

The reader is the only source of waveform data for the whole product, so it is
tested three ways: against closed-form circuit behaviour, against the same run
written in ASCII and binary, and against deliberately malformed files (which must
raise rather than silently return zeros).
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from boardmodeler.simulation.ltspice import run_batch
from boardmodeler.simulation.raw import RawFormatError, read_raw

pytestmark = pytest.mark.ltspice

RC_DECK = """* RC step
V1 in 0 PULSE(0 1 0 1n 1n 1 2)
R1 in out 1k
C1 out 0 1u
.tran 10m
.end
"""

DIVIDER_DECK = """* divider
V1 in 0 10
R1 in out 1k
R2 out 0 3k
.tran 1m
.end
"""


def _raw_path(
    ltspice_exe: Path, tmp_path: Path, name: str, text: str, *, ascii_raw: bool = False
) -> Path:
    """Run a deck and return its .raw path (asserting the run actually produced one)."""
    work = tmp_path / name
    work.mkdir(parents=True)
    deck = work / f"{name}.cir"
    deck.write_text(text, encoding="utf-8")
    result = run_batch(ltspice_exe, deck, work, timeout_s=60, ascii_raw=ascii_raw)
    assert result.exit_code == 0, result.observed()
    assert result.raw_path is not None and result.raw_path.is_file()
    return result.raw_path


def test_rc_step_matches_closed_form(ltspice_exe: Path, tmp_path: Path) -> None:
    raw = read_raw(_raw_path(ltspice_exe, tmp_path, "rc", RC_DECK))
    t = raw.time_column()
    assert t is not None
    tau = 1e-3
    for probe in (0.2e-3, 0.5e-3, 1e-3, 2e-3):
        expected = 1.0 - np.exp(-probe / tau)
        measured = float(np.interp(probe, t, raw.column("V(out)")))
        assert measured == pytest.approx(expected, abs=5e-4), probe
    final = float(np.interp(9.9e-3, t, raw.column("V(out)")))
    assert final == pytest.approx(1.0, abs=1e-3)


def test_divider_matches_closed_form(ltspice_exe: Path, tmp_path: Path) -> None:
    raw = read_raw(_raw_path(ltspice_exe, tmp_path, "div", DIVIDER_DECK))
    assert raw.has("V(out)")
    assert float(raw.column("V(out)")[-1]) == pytest.approx(7.5, abs=1e-6)
    # Kirchhoff: the same current flows through both resistors.
    # LTspice reports branch current from the first node to the second, so both
    # resistors carry +2.5 mA in the same direction (continuity across the node).
    i_r1 = float(raw.column("I(R1)")[-1])
    i_r2 = float(raw.column("I(R2)")[-1])
    assert i_r1 == pytest.approx(2.5e-3, rel=1e-6)
    assert i_r2 == pytest.approx(2.5e-3, rel=1e-6)


def test_ascii_and_binary_agree(ltspice_exe: Path, tmp_path: Path) -> None:
    binary = read_raw(_raw_path(ltspice_exe, tmp_path, "rcbin", RC_DECK))
    ascii_raw = read_raw(_raw_path(ltspice_exe, tmp_path, "rcascii", RC_DECK, ascii_raw=True))
    assert ascii_raw.layout == "values"
    assert binary.layout == "double+float32"
    assert ascii_raw.variables == binary.variables

    t_bin = binary.time_column()
    t_asc = ascii_raw.time_column()
    assert t_bin is not None and t_asc is not None
    assert ascii_raw.npoints == binary.npoints

    grid = np.linspace(float(t_bin[1]), float(t_bin[-1]), 500)
    for name in ("V(in)", "V(out)", "I(R1)"):
        a = np.interp(grid, t_asc, ascii_raw.column(name))
        b = np.interp(grid, t_bin, binary.column(name))
        scale = max(float(np.max(np.abs(b))), 1e-12)
        # Measured agreement: <=1.2e-9 for V(in) and <=2.9e-8 for V(out). The
        # binary payload stores non-time variables as float32 (eps ~1.2e-7), so
        # the ASCII text (~10 significant digits) is the more precise of the
        # two; 1e-7 is the honest bound. Consumers must not assert on binary raw
        # data more tightly than float32 permits.
        assert float(np.max(np.abs(a - b))) / scale < 1e-7, name


def test_time_axis_is_monotonic(ltspice_exe: Path, tmp_path: Path) -> None:
    raw = read_raw(_raw_path(ltspice_exe, tmp_path, "mono", RC_DECK))
    t = raw.time_column()
    assert t is not None
    assert np.all(np.diff(t) > 0)
    assert t[0] == 0.0


# --------------------------------------------------------------------------- #
# malformed files must raise, never zero-fill


def _header(nvars: int, npoints: int, mode: str = "Binary") -> bytes:
    lines = [
        "Title: synthetic",
        "Date: Thu Jan  1 00:00:00 2026",
        "Plotname: Transient Analysis",
        "Flags: real forward",
        f"No. Variables: {nvars}",
        f"No. Points: {npoints}",
        "Variables:",
    ]
    for i in range(nvars):
        lines.append(f"\t{i}\tv{i}\tvoltage")
    text = "\n".join(lines) + f"\n{mode}:\n"
    return text.encode("utf-16-le")


def test_truncated_payload_raises(tmp_path: Path) -> None:
    # Claims 10 points x 3 variables (stride 16) but supplies only 42 bytes.
    path = tmp_path / "truncated.raw"
    path.write_bytes(_header(3, 10) + bytes(42))
    with pytest.raises(RawFormatError, match="does not match any known layout"):
        read_raw(path)


def test_missing_marker_raises(tmp_path: Path) -> None:
    path = tmp_path / "nomarker.raw"
    path.write_bytes(b"Title: not a raw file\n")
    with pytest.raises(RawFormatError, match="no 'Binary:' or 'Values:' section"):
        read_raw(path)


def test_complex_payload_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ac.raw"
    path.write_bytes(
        _header(3, 2).replace(
            b"real forward".decode().encode("utf-16-le"), "complex".encode("utf-16-le")
        )
        + bytes(2 * (8 + 8 * 2))
    )
    with pytest.raises(RawFormatError, match="complex"):
        read_raw(path)


def test_stepped_payload_is_rejected(tmp_path: Path) -> None:
    header = _header(3, 2).replace(
        "real forward".encode("utf-16-le"), "real forward stepped".encode("utf-16-le")
    )
    path = tmp_path / "stepped.raw"
    path.write_bytes(header + bytes(2 * 16))
    with pytest.raises(RawFormatError, match="stepped"):
        read_raw(path)


def test_ascii_payload_count_mismatch_raises(tmp_path: Path) -> None:
    header = _header(2, 3, mode="Values")
    path = tmp_path / "short_ascii.raw"
    path.write_bytes(header + "\t0\t0.0\n\t1.0\n".encode("utf-16-le"))
    with pytest.raises(RawFormatError, match="header declares 3"):
        read_raw(path)


def test_binary_layout_is_detected_from_size(tmp_path: Path) -> None:
    """The reader derives the stride from the payload size, not from a guess."""
    npoints, nvars = 4, 2  # stride candidates 12, 16, 8, 8, 16
    payload = bytearray()
    for i in range(npoints):
        payload += struct.pack("<d", i * 1e-3)
        payload += struct.pack("<f", i * 2.0)
    path = tmp_path / "layout.raw"
    path.write_bytes(_header(nvars, npoints) + bytes(payload))
    raw = read_raw(path)
    assert raw.layout == "double+float32"
    assert raw.npoints == npoints
    np.testing.assert_allclose(raw.column("v0"), [0.0, 1e-3, 2e-3, 3e-3])
    np.testing.assert_allclose(raw.column("v1"), [0.0, 2.0, 4.0, 6.0], rtol=1e-6)
