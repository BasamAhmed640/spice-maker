"""Every demo-board scenario deck must apply the stimulus it declares (D-011).

No simulator is needed: these tests read the decks the build wrote and the
machine-readable record beside them. A scenario that was checked against a deck
without its stimulus would be a claim about an experiment nobody ran, so the
assertions here are about the deck *text* — which is the artifact the simulator
would have executed.
"""

from __future__ import annotations

import json
import re

import pytest

from boardmodeler.pipeline.demo import (
    CLOCK_STANDIN_PERIOD_S,
    build_demo_project,
    deck_for_scenario,
    stimulus_for,
)
from boardmodeler.schematic.neutral import read_neutral_project
from boardmodeler.verification.scenarios import all_scenario_ids

#: The stand-in rate must stay cheap; 100 MHz with 1 ns edges cost ~1e6 timesteps.
MAX_CLOCK_HZ = 1e6


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory):
    return build_demo_project(tmp_path_factory.mktemp("demo_decks") / "project")


@pytest.fixture(scope="module")
def record(built) -> dict[str, dict[str, object]]:
    path = built.project_dir / "tests" / "scenario_stimulus.json"
    assert path.is_file(), "the build wrote no scenario_stimulus.json"
    return json.loads(path.read_text(encoding="utf-8"))


def source_cards(text: str) -> dict[str, str]:
    """Every independent-source card in a deck, keyed by its name."""
    cards: dict[str, str] = {}
    for line in text.splitlines():
        if line.lstrip().startswith(("*", ".")):
            continue
        match = re.match(r"^(V\w+|I\w+)\s+", line)
        if match:
            cards[match.group(1)] = line.strip()
    return cards


def test_the_record_covers_every_known_scenario(built, record) -> None:
    assert set(record) == set(all_scenario_ids()), (
        "the stimulus record and the scenario registry disagree: "
        f"{set(all_scenario_ids()) ^ set(record)}"
    )
    for scenario_id, entry in record.items():
        assert isinstance(entry["applied"], bool)
        if not entry["applied"]:
            assert entry["note"], f"{scenario_id} is not applied and gives no reason"
        assert entry["injection"] is not None


def test_every_applied_scenario_declares_what_it_injects(record) -> None:
    for scenario_id, entry in record.items():
        if entry["applied"] and scenario_id != "nominal_startup":
            assert entry["injection"] != "none", (
                f"{scenario_id} claims to be applied but injects nothing"
            )


def test_each_test_case_has_a_deck_that_differs_from_nominal(built) -> None:
    nominal = (built.project_dir / "tests" / "decks" / "nominal_startup.cir").read_text(
        encoding="utf-8"
    )
    for scenario_id in built.decks:
        path = built.project_dir / "tests" / "decks" / f"{scenario_id}.cir"
        assert path.is_file(), f"{scenario_id} has no deck"
        text = path.read_text(encoding="utf-8")
        if scenario_id == "nominal_startup":
            continue
        assert text != nominal, (
            f"{scenario_id} runs the nominal deck, so its result would describe an "
            "experiment that was never performed"
        )
        assert f"* scenario {scenario_id}:" in text, "the deck does not name its scenario"
        assert "* declared stimulus:" in text, "the deck does not record its declared stimulus"


def test_no_deck_drives_the_reference_clock_at_silicon_speed(built) -> None:
    """The clock is a declared stand-in; nothing may spend the run resolving 100 MHz."""
    for scenario_id in built.decks:
        text = (built.project_dir / "tests" / "decks" / f"{scenario_id}.cir").read_text(
            encoding="utf-8"
        )
        clock = source_cards(text).get("V2")
        assert clock is not None, f"{scenario_id} has no REFCLK_100M source"
        period = re.search(r"PULSE\(([^)]*)\)", clock)
        if period is None:
            # clock_missing holds the net at a DC level, which is the injection.
            assert "DC " in clock, f"{scenario_id}: unexpected clock card {clock!r}"
            continue
        numbers = [float(value) for value in period.group(1).split()]
        assert numbers, f"{scenario_id}: clock card has no parameters: {clock!r}"
        pulse_period = numbers[-1]
        assert pulse_period >= CLOCK_STANDIN_PERIOD_S, (
            f"{scenario_id}: clock period {pulse_period:g} s is faster than the declared "
            f"stand-in {CLOCK_STANDIN_PERIOD_S:g} s (about {1 / pulse_period / 1e6:.1f} MHz)"
        )
        assert 1 / pulse_period <= MAX_CLOCK_HZ, (
            f"{scenario_id}: clock is {1 / pulse_period:.0f} Hz"
        )


def test_the_clock_scenarios_change_the_clock_they_claim_to(built, record) -> None:
    """`clock_missing` and `clock_late` must differ from the stand-in as declared."""
    decks = built.project_dir / "tests" / "decks"
    neutral = read_neutral_project(built.project_dir / "circuit")
    nominal = source_cards((decks / "nominal_startup.cir").read_text(encoding="utf-8"))["V2"]
    missing_deck = deck_for_scenario(neutral, "clock_missing", out=built.project_dir)
    late_deck = deck_for_scenario(neutral, "clock_late", out=built.project_dir)
    missing = {s.name: s.card() for s in missing_deck.sources}["V2"]
    late = {s.name: s.card() for s in late_deck.sources}["V2"]
    assert "DC " in missing, f"clock_missing still drives a pulse: {missing!r}"
    assert missing != nominal
    assert late != nominal, "clock_late drives an identical clock to nominal"
    stand_in = re.search(r"PULSE\(([^)]*)\)", nominal)
    late_pulse = re.search(r"PULSE\(([^)]*)\)", late)
    assert stand_in is not None and late_pulse is not None
    assert float(late_pulse.group(1).split()[2]) > float(stand_in.group(1).split()[2]), (
        "clock_late does not delay the clock"
    )


def test_the_strap_and_pullup_scenarios_inject_their_edit(built) -> None:
    """Spot-check three stimuli whose effect is unmistakable in the deck text."""
    decks = built.project_dir / "tests" / "decks"
    nominal_text = (decks / "nominal_startup.cir").read_text(encoding="utf-8")
    nominal_cards = source_cards(nominal_text)
    assert "R17" in nominal_text, "the nominal deck no longer carries the SMB_DAT pull-up"

    missing = (decks / "pullup_missing.cir").read_text(encoding="utf-8")
    assert not re.search(r"^R17\s", missing, re.MULTILINE), (
        "pullup_missing still contains R17, so nothing was removed"
    )
    assert "* removed: R17" in missing, "the deck does not record what it removed"

    wrong = (decks / "pullup_wrong_domain.cir").read_text(encoding="utf-8")
    wrong_r17 = re.search(r"^R17\s+(\S+)\s+(\S+)", wrong, re.MULTILINE)
    nominal_r17 = re.search(r"^R17\s+(\S+)\s+(\S+)", nominal_text, re.MULTILINE)
    assert wrong_r17 is not None and nominal_r17 is not None
    assert wrong_r17.group(2) != nominal_r17.group(2), (
        f"pullup_wrong_domain did not move R17's return ({nominal_r17.group(2)} -> "
        f"{wrong_r17.group(2)})"
    )

    strap = (decks / "invalid_strap.cir").read_text(encoding="utf-8")
    strap_sources = source_cards(strap)
    assert any(name not in nominal_cards for name in strap_sources), (
        f"invalid_strap added no source: {sorted(strap_sources)}"
    )


def test_a_scenario_deck_can_be_rebuilt_from_the_project_alone(built) -> None:
    """The build must be reproducible from the neutral project (no hidden state)."""
    neutral = read_neutral_project(built.project_dir / "circuit")
    for scenario_id in ("nominal_startup", "load_step"):
        spec = deck_for_scenario(neutral, scenario_id, out=built.project_dir)
        written = (built.project_dir / "tests" / "decks" / f"{scenario_id}.cir").read_text(
            encoding="utf-8"
        )
        assert spec.title in written
        for card in spec.elements:
            assert card in written, f"{scenario_id}: rebuilt deck lost card {card!r}"


def test_the_stimulus_table_matches_the_registry() -> None:
    """A scenario with no stimulus entry is an error, not a silent nominal run."""
    for scenario_id in all_scenario_ids():
        stimulus = stimulus_for(scenario_id)
        assert stimulus.note or scenario_id == "nominal_startup", (
            f"{scenario_id} carries no note explaining its injection"
        )
    with pytest.raises(KeyError):
        stimulus_for("not_a_scenario")
