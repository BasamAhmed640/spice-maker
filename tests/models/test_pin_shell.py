"""The pin shell writes models the viability gate accepts, for any pin table."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.models.proof_parts import (
    LM358_GATE,
    LM358_PINS,
    MCU8_GATE,
    MCU8_PINS,
    lm358_text,
    mcu8_text,
)

from boardmodeler.authoring.viability import PIN_KINDS as GATE_KINDS
from boardmodeler.authoring.viability import run_gate, static_checks
from boardmodeler.domain.enums import Status
from boardmodeler.models.library import subckt_ports
from boardmodeler.models.pin_shell import PIN_KINDS, ShellError, ShellPin, render_shell
from boardmodeler.models.symbolism import symbol_text

CASES = {
    "LM358": (lm358_text, LM358_PINS, LM358_GATE),
    "MCU8": (mcu8_text, MCU8_PINS, MCU8_GATE),
}


def test_pin_kinds_agree_with_the_gate() -> None:
    assert PIN_KINDS == GATE_KINDS


@pytest.mark.parametrize("part", list(CASES))
def test_every_pin_is_a_port_in_pin_table_order(part: str) -> None:
    text, pins, _gate = CASES[part]
    assert subckt_ports(text(), part) == tuple(p.port for p in pins)


@pytest.mark.parametrize("part", list(CASES))
def test_the_rendered_model_passes_the_static_checks(part: str) -> None:
    text, pins, gate = CASES[part]
    asy = symbol_text(part, [p.port for p in pins], model_file=f"{part}.lib")
    checks = static_checks(text(), gate, asy_text=asy)
    assert all(c.status is Status.PASS for c in checks), [c.as_dict() for c in checks]


def test_the_same_pin_table_renders_the_same_bytes() -> None:
    assert lm358_text() == lm358_text()
    assert mcu8_text() == mcu8_text()


def test_nothing_refers_to_node_zero() -> None:
    body = [line for line in lm358_text().splitlines() if not line.startswith((".", "*"))]
    assert not any(" 0 " in f" {line} " for line in body)


def test_an_output_is_inert_until_commanded() -> None:
    text = mcu8_text()
    assert "params: LEVEL_PA0=-1 LEVEL_PB0=-1" in text
    assert "u(LEVEL_PB0+0.5)" in text


REFUSALS = {
    "no ground pin": ((ShellPin("VDD", "supply"), ShellPin("IN", "input")), "no ground pin"),
    "a port named twice": ((ShellPin("GND", "ground"), ShellPin("gnd", "input")), "twice"),
    "an input with no supply to clamp to": (
        (ShellPin("GND", "ground"), ShellPin("IN", "input")),
        "no supply pin",
    ),
}


@pytest.mark.parametrize("name", list(REFUSALS))
def test_a_pin_table_that_needs_a_guess_is_refused(name: str) -> None:
    pins, message = REFUSALS[name]
    with pytest.raises(ShellError, match=message):
        render_shell("X", pins)


def test_a_drive_for_something_that_is_not_an_output_is_refused() -> None:
    pins = (ShellPin("VDD", "supply"), ShellPin("GND", "ground"), ShellPin("IN", "input"))
    with pytest.raises(ShellError, match="not an output"):
        render_shell("X", pins, drives={"IN": "1"})


def test_unknown_kinds_are_refused() -> None:
    with pytest.raises(ShellError, match="kind"):
        ShellPin("P", "amplifier")


@pytest.mark.ltspice
@pytest.mark.parametrize("part", list(CASES))
def test_the_rendered_model_passes_every_bench(
    part: str, ltspice_exe: Path, tmp_path: Path
) -> None:
    text, pins, gate = CASES[part]
    lib = tmp_path / f"{part}.lib"
    lib.write_text(text(), encoding="utf-8")
    asy = tmp_path / f"{part}.asy"
    asy.write_text(symbol_text(part, [p.port for p in pins], model_file=lib.name), encoding="utf-8")
    report = run_gate(lib, gate, ltspice_exe, tmp_path / "gate", asy_path=asy)
    assert report.status is Status.PASS, [c.as_dict() for c in report.checks]
    by_id = {c.id: c for c in report.checks}
    # the outputs really delivered current into a short, and the supply pin carried it
    assert by_id["supply_carries_output_current"].status is Status.PASS
