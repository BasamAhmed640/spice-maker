"""The spec layer: byte-stable round trips, a cross-process digest, SI units.

These tests hold the frozen-spec guarantee the author loop depends on: the
digest an agent cannot move, and the binding file that must account for every
requirement exactly once (bound or not_testable with a reason).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from boardmodeler.authoring.probes import PROBES
from boardmodeler.authoring.spec import (
    SpecSet,
    load_tps54320_spec,
    normalize_unit,
    parse_subckt_ports,
)
from boardmodeler.domain.hashing import canonical_json_bytes, sha256_bytes

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "regulator" / "tps54320"
REQUIREMENTS = FIXTURE / "requirements.json"
BINDINGS = FIXTURE / "probes.json"


@pytest.fixture(scope="module")
def spec() -> SpecSet:
    return load_tps54320_spec(REQUIREMENTS, BINDINGS, part="TPS54320", subckt="BM_REG_BUCK")


# --------------------------------------------------------------------------- #
# units


@pytest.mark.parametrize(
    ("raw", "base", "scale"),
    [
        ("uA", "A", 1e-6),
        ("µA", "A", 1e-6),
        ("mA", "A", 1e-3),
        ("mV", "V", 1e-3),
        ("kHz", "Hz", 1e3),
        ("MHz", "Hz", 1e6),
        ("nF", "F", 1e-9),
        ("us", "s", 1e-6),
        ("ms", "s", 1e-3),
        ("kohm", "ohm", 1e3),
        ("V", "V", 1.0),
        ("A", "A", 1.0),
        ("Hz", "Hz", 1.0),
        ("ratio", "ratio", 1.0),
        ("V/V", "V/V", 1.0),
        ("cycles", "cycles", 1.0),
        ("  V  ", "V", 1.0),
        ("", "", 1.0),
    ],
)
def test_normalize_unit(raw: str, base: str, scale: float) -> None:
    assert normalize_unit(raw) == (base, scale)


def test_units_are_scaled_when_loading(tmp_path: Path) -> None:
    """A fixture written with suffixed units is scaled into SI base units."""
    requirements = {
        "document": {"doc_id": "doc_synthetic"},
        "requirements": [
            {
                "req_id": "REQ_SYN_001",
                "kind": "ELECTRICAL",
                "class": "DOCUMENTED_LIMIT",
                "statement": "The EN current is at most 5 uA and the reference is 800 mV.",
                "limits": {"min": 1150.0, "max": 3400.0, "unit": "uA"},
                "evidence": [
                    {
                        "doc_id": "doc_synthetic",
                        "page": {"pdf_page": 7, "printed_label": "7"},
                        "excerpt": "EN current 1.15 3.4 uA",
                        "extraction": "embedded_text",
                    }
                ],
            }
        ],
    }
    bindings = {"bindings": [{"req_id": "REQ_SYN_001", "probe": "en_rise", "params": {}}]}
    req_path = tmp_path / "requirements.json"
    bind_path = tmp_path / "probes.json"
    req_path.write_text(json.dumps(requirements), encoding="utf-8")
    bind_path.write_text(json.dumps(bindings), encoding="utf-8")

    loaded = load_tps54320_spec(req_path, bind_path, part="SYN", subckt="BM_REG_BUCK")
    char = loaded.by_id("REQ_SYN_001")
    assert char.unit == "A"
    assert char.min_value == pytest.approx(1150e-6)
    assert char.max_value == pytest.approx(3400e-6)
    assert char.source_page == 7
    assert char.excerpt == "EN current 1.15 3.4 uA"


# --------------------------------------------------------------------------- #
# round trip and digest


def test_round_trip_is_byte_stable(spec: SpecSet) -> None:
    text = spec.to_json()
    again = SpecSet.from_json(text)
    assert again.to_json() == text
    assert again == spec
    assert again.digest() == spec.digest()


def test_digest_is_stable_across_independent_loads() -> None:
    first = load_tps54320_spec(REQUIREMENTS, BINDINGS, part="TPS54320", subckt="BM_REG_BUCK")
    second = load_tps54320_spec(REQUIREMENTS, BINDINGS, part="TPS54320", subckt="BM_REG_BUCK")
    assert first.digest() == second.digest()
    assert first.digest() == sha256_bytes(canonical_json_bytes(first.payload()))
    assert len(first.digest()) == 64


def test_digest_changes_with_limits_bindings_and_deck_params(spec: SpecSet) -> None:
    char = spec.by_id("REQ_TPS54320_ELEC_004")
    relaxed = dataclasses.replace(char, min_value=0.0)
    assert _digest_with(spec, relaxed) != spec.digest()
    rebound = dataclasses.replace(char, probe="uvlo_fall")
    assert _digest_with(spec, rebound) != spec.digest()
    retuned = dataclasses.replace(char, probe_params={"ramp_v_per_s": 1.0})
    assert _digest_with(spec, retuned) != spec.digest()


def _digest_with(spec: SpecSet, char: object) -> str:
    chars = tuple(char if c.char_id == char.char_id else c for c in spec.characteristics)  # type: ignore[attr-defined]
    return dataclasses.replace(spec, characteristics=chars).digest()


# --------------------------------------------------------------------------- #
# subcircuit ports


def test_parse_subckt_ports_handles_continuation_and_params() -> None:
    text = (
        "* a library with two subcircuits\n"
        ".subckt BM_PART A B C params: R=1\n"
        ".ends\n"
        ".subckt BM_REG_BUCK VIN EN FB PG\n"
        "+ VOUT GND SW ILIM_MODE params: VREF=0.8 UVLO_RISE=4.3\n"
        "X1 VIN EN FB PG VOUT GND SW ILIM_MODE BM_REG_BUCK\n"
        ".ends\n"
    )
    assert parse_subckt_ports(text) == ["A", "B", "C"]
    assert parse_subckt_ports(text, name="BM_REG_BUCK") == [
        "VIN",
        "EN",
        "FB",
        "PG",
        "VOUT",
        "GND",
        "SW",
        "ILIM_MODE",
    ]


def test_parse_subckt_ports_raises_for_missing_subcircuit() -> None:
    from boardmodeler.models.library import ModelStoreError

    with pytest.raises(ModelStoreError):
        parse_subckt_ports("* nothing here\n", name="BM_ABSENT")


# --------------------------------------------------------------------------- #
# the fixture binding file


def test_every_requirement_appears_exactly_once(spec: SpecSet) -> None:
    requirements = json.loads(REQUIREMENTS.read_text(encoding="utf-8"))
    fixture_ids = [entry["req_id"] for entry in requirements["requirements"]]
    bindings = json.loads(BINDINGS.read_text(encoding="utf-8"))["bindings"]
    binding_ids = [entry["req_id"] for entry in bindings]

    assert len(fixture_ids) == len(set(fixture_ids))
    assert len(binding_ids) == len(set(binding_ids))
    assert set(binding_ids) == set(fixture_ids)
    assert [c.char_id for c in spec.characteristics] == fixture_ids


def test_bound_characteristics_are_judgeable(spec: SpecSet) -> None:
    assert len(spec.covered()) >= 5
    for char in spec.covered():
        assert char.probe in PROBES, char.char_id
        assert char.has_limits or char.typ_value is not None, char.char_id
        assert char.unit, char.char_id
        assert char.not_testable_reason is None
        assert isinstance(char.probe_params, dict)


def test_uncovered_characteristics_carry_a_concrete_reason(spec: SpecSet) -> None:
    for char in spec.uncovered():
        assert char.probe is None
        assert char.not_testable_reason
        assert len(char.not_testable_reason) > 40, char.char_id
        assert char.probe_params == {}


def test_bound_characteristic_keeps_page_and_citation(spec: SpecSet) -> None:
    char = spec.by_id("REQ_TPS54320_ELEC_004")
    assert char.probe == "uvlo_rise"
    assert char.req_class == "DOCUMENTED_LIMIT"
    assert char.unit == "V"
    assert char.min_value == pytest.approx(4.0)
    assert char.max_value == pytest.approx(4.5)
    assert char.source_page is not None and char.source_page >= 0
    assert char.excerpt
    assert char.target == char.typ_value

    typical = spec.by_id("REQ_TPS54320_TEMPORAL_062")
    assert typical.req_class == "TYPICAL_VALUE"
    assert typical.probe == "uvlo_rise"
    assert typical.min_value is None and typical.max_value is None
    assert typical.typ_value == pytest.approx(4.0)


def test_partition_and_probe_grouping(spec: SpecSet) -> None:
    covered = spec.covered()
    uncovered = spec.uncovered()
    assert len(covered) + len(uncovered) == len(spec.characteristics)
    assert set(spec.by_probe()) == {c.probe for c in covered}
    assert sum(len(chars) for chars in spec.by_probe().values()) == len(covered)


# --------------------------------------------------------------------------- #
# loader validation


def _write_pair(tmp_path: Path, requirement: dict, binding: dict) -> tuple[Path, Path]:
    requirements = {"document": {"doc_id": "doc_x"}, "requirements": [requirement]}
    req_path = tmp_path / "requirements.json"
    bind_path = tmp_path / "probes.json"
    req_path.write_text(json.dumps(requirements), encoding="utf-8")
    bind_path.write_text(json.dumps({"bindings": [binding]}), encoding="utf-8")
    return req_path, bind_path


def _requirement(req_id: str = "REQ_X_001", limits: dict | None = None) -> dict:
    return {
        "req_id": req_id,
        "kind": "ELECTRICAL",
        "class": "DOCUMENTED_LIMIT",
        "statement": "statement",
        "limits": limits,
        "evidence": [],
    }


def test_loader_rejects_bound_requirement_without_limits(tmp_path: Path) -> None:
    req_path, bind_path = _write_pair(
        tmp_path, _requirement(), {"req_id": "REQ_X_001", "probe": "uvlo_rise", "params": {}}
    )
    with pytest.raises(ValueError, match="no numeric limit"):
        load_tps54320_spec(req_path, bind_path, part="X", subckt="BM_REG_BUCK")


def test_loader_rejects_unbound_requirement_without_reason(tmp_path: Path) -> None:
    req_path, bind_path = _write_pair(
        tmp_path, _requirement(), {"req_id": "REQ_X_001", "probe": None}
    )
    with pytest.raises(ValueError, match="not_testable_reason"):
        load_tps54320_spec(req_path, bind_path, part="X", subckt="BM_REG_BUCK")


def test_loader_rejects_missing_binding(tmp_path: Path) -> None:
    req_path, bind_path = _write_pair(
        tmp_path, _requirement(), {"req_id": "REQ_OTHER", "probe": None}
    )
    with pytest.raises(ValueError):
        load_tps54320_spec(req_path, bind_path, part="X", subckt="BM_REG_BUCK")


def test_loader_rejects_unknown_probe(tmp_path: Path) -> None:
    req_path, bind_path = _write_pair(
        tmp_path,
        _requirement(limits={"min": 1.0, "max": 2.0, "unit": "V"}),
        {"req_id": "REQ_X_001", "probe": "not_a_probe", "params": {}},
    )
    with pytest.raises(ValueError, match="unknown probe"):
        load_tps54320_spec(req_path, bind_path, part="X", subckt="BM_REG_BUCK")


def test_loader_rejects_duplicate_binding(tmp_path: Path) -> None:
    requirements = {
        "document": {"doc_id": "doc_x"},
        "requirements": [_requirement()],
    }
    req_path = tmp_path / "requirements.json"
    bind_path = tmp_path / "probes.json"
    req_path.write_text(json.dumps(requirements), encoding="utf-8")
    bind_path.write_text(
        json.dumps(
            {
                "bindings": [
                    {"req_id": "REQ_X_001", "probe": "uvlo_rise", "params": {}},
                    {"req_id": "REQ_X_001", "probe": None, "not_testable_reason": "why"},
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_tps54320_spec(req_path, bind_path, part="X", subckt="BM_REG_BUCK")
