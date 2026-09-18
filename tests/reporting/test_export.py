"""Export and symbolism tests (Phase 2 step 7).

The export is the artifact a colleague receives, so the tests check the things
that would silently break it: relative paths, vendor originals not copied, the
manifest hashing every file, coverage naming the requirements with no test, and
the symbol's SpiceOrder bijection.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from boardmodeler.domain.enums import (
    Criticality,
    EvidenceExtraction,
    EvidenceLevel,
    ModelKind,
    RequirementClass,
    RequirementKind,
    RequirementOrigin,
)
from boardmodeler.domain.records import (
    BEHAVIOR_KEYS,
    EvidenceRef,
    ExpectationSpec,
    ModelCapability,
    PageRef,
    Requirement,
    TestCase,
    TestResult,
)
from boardmodeler.models.library import ModelStore
from boardmodeler.models.symbolism import (
    symbol_pin_orders,
    symbol_text,
    validate_symbol,
)
from boardmodeler.pipeline.project import Project, create_project
from boardmodeler.reporting.export import export_project, requirements_coverage

PORTS = ("VIN", "EN", "FB", "PG", "VOUT", "GND", "SW", "ILIM_MODE")
DIRECTIONS = {
    "VIN": "power",
    "EN": "input",
    "FB": "input",
    "PG": "output",
    "VOUT": "output",
    "GND": "ground",
    "SW": "output",
    "ILIM_MODE": "input",
}

MODEL_TEXT = """* generated behavioral regulator (test fixture)
.subckt BM_REG_BUCK VIN EN FB PG VOUT GND SW ILIM_MODE
Rleak VIN GND 1G
.ends BM_REG_BUCK
"""


def make_requirement(
    req_id: str, *, criticality: Criticality = Criticality.CRITICAL
) -> Requirement:
    return Requirement(
        req_id=req_id,
        applies_to="U1",
        kind=RequirementKind.ELECTRICAL,
        **{
            "class": RequirementClass.DOCUMENTED_LIMIT,
            "criticality": criticality,
            "origin": RequirementOrigin.TEST_FIXTURE,
            "statement": "the rail stays within its limits",
            "limits": {"min": 3.234, "max": 3.366, "unit": "V"},
            "expression": {
                "op": "between",
                "signal": "V(VOUT)",
                "low": 3.234,
                "high": 3.366,
                "unit": "V",
            },
            "evidence": [
                EvidenceRef(
                    doc_id="doc_synthetic",
                    page=PageRef(pdf_page=0),
                    excerpt="synthetic fixture requirement",
                    extraction=EvidenceExtraction.SYNTHETIC_FIXTURE,
                )
            ],
            "citation_verified": True,
            "status": "active",
        },
    )


def make_case(test_id: str, req_ids: list[str]) -> TestCase:
    return TestCase(
        test_id=test_id,
        requirement_ids=req_ids,
        scenario_id="nominal_startup",
        scope="circuit_compliance",
        deck_template="tests/decks/nominal.cir",
        expected=ExpectationSpec(kind="satisfy", detail="rail within limits"),
        measurement=["V(VOUT)"],
    )


# --------------------------------------------------------------------------- #
# symbolism


def test_symbol_spice_order_is_a_bijection_onto_the_ports() -> None:
    asy = symbol_text("BM_REG_BUCK", PORTS, model_file="model.lib", directions=DIRECTIONS)
    pairs = symbol_pin_orders(asy)
    assert [name for name, _ in pairs] == list(PORTS) or set(name for name, _ in pairs) == set(
        PORTS
    )
    orders = sorted(order for _name, order in pairs)
    assert orders == list(range(1, len(PORTS) + 1))
    assert validate_symbol(asy, ports=PORTS, model_file="model.lib") == []


def test_symbol_validation_catches_a_dropped_pin_and_a_duplicate_order() -> None:
    asy = symbol_text("BM_REG_BUCK", PORTS, model_file="model.lib", directions=DIRECTIONS)
    dropped = asy.replace("PINATTR SpiceOrder 5\n", "", 1)
    codes = {f.code for f in validate_symbol(dropped, ports=PORTS)}
    assert "SYM002_spice_order_not_a_bijection" in codes

    duplicated = asy.replace("PINATTR SpiceOrder 5", "PINATTR SpiceOrder 4", 1)
    codes = {f.code for f in validate_symbol(duplicated, ports=PORTS)}
    assert "SYM002_spice_order_not_a_bijection" in codes

    wrong_ports = validate_symbol(asy, ports=(*PORTS, "EXTRA"), model_file="model.lib")
    assert any(f.code == "SYM001_port_set_mismatch" for f in wrong_ports)

    no_model = validate_symbol(
        asy.replace("SYMATTR SpiceModel model.lib", ""), ports=PORTS, model_file="model.lib"
    )
    assert any(f.code == "SYM003_spicemodel_attribute_missing" for f in no_model)


def test_symbol_is_deterministic_and_interprets_directions() -> None:
    first = symbol_text("BM_REG_BUCK", PORTS, model_file="model.lib", directions=DIRECTIONS)
    second = symbol_text("BM_REG_BUCK", PORTS, model_file="model.lib", directions=DIRECTIONS)
    assert first == second
    assert "SYMATTR Prefix X" in first
    assert "SYMATTR Value2 BM_REG_BUCK" in first
    # Every port appears exactly once as a PinName.
    for port in PORTS:
        assert first.count(f"PINATTR PinName {port}\n") == 1


# --------------------------------------------------------------------------- #
# coverage


def test_coverage_names_the_requirements_with_no_test() -> None:
    requirements = [
        make_requirement("REQ_A"),
        make_requirement("REQ_B", criticality=Criticality.IMPORTANT),
    ]
    tests = [make_case("T_a", ["REQ_A"])]
    results = [
        TestResult(
            test_id="T_a",
            status="PASS",
            requirement_ids=["REQ_A"],
            measured={"min(V(VOUT))": 3.3},
            expected="rail within limits",
        )
    ]
    coverage = requirements_coverage(requirements, tests, results)
    assert coverage["requirements_total"] == 2
    assert coverage["requirements_with_a_test"] == 1
    assert [entry["req_id"] for entry in coverage["not_dynamically_covered"]] == ["REQ_B"]
    assert coverage["coverage_fraction"] == pytest.approx(0.5)


def test_coverage_flags_requirements_whose_only_verdict_is_unknown() -> None:
    requirements = [make_requirement("REQ_A")]
    tests = [make_case("T_a", ["REQ_A"])]
    results = [
        TestResult(
            test_id="T_a",
            status="UNKNOWN",
            requirement_ids=["REQ_A"],
            measured={"note": "no waveform"},
            expected="rail within limits",
            unknown_reason="signal_not_saved",
        )
    ]
    coverage = requirements_coverage(requirements, tests, results)
    assert coverage["not_dynamically_covered"] == []
    assert [entry["req_id"] for entry in coverage["non_verdict_requirements"]] == ["REQ_A"]
    assert coverage["requirements_with_a_verdict"] == 0


def test_coverage_reports_capability_gated_requirements() -> None:
    states = {key: "supported" for key in BEHAVIOR_KEYS}
    states["load_transients"] = "unsupported"
    coverage = requirements_coverage(
        [make_requirement("REQ_A"), make_requirement("REQ_B")],
        [],
        [],
        behaviours=states,
        capability_map={"REQ_A": "startup", "REQ_B": "load_transients"},
    )
    assert coverage["capability_gated"] == [
        {"req_id": "REQ_B", "behavior": "load_transients", "state": "unsupported"}
    ]


# --------------------------------------------------------------------------- #
# export


def build_project(
    tmp_path: Path,
) -> tuple[
    Project, ModelStore, list[Requirement], ModelCapability, list[TestCase], list[TestResult]
]:
    project = create_project(tmp_path, project_id="prj_export", name="Export test project")
    store = ModelStore(tmp_path)
    store.add_text_artifact(MODEL_TEXT, model_id="bm_reg_buck_test", kind="generated")
    requirements = [make_requirement("REQ_A"), make_requirement("REQ_B")]
    tests = [make_case("T_a", ["REQ_A"])]
    results = [
        TestResult(
            test_id="T_a",
            status="PASS",
            requirement_ids=["REQ_A"],
            measured={"min(V(VOUT))": 3.31},
            expected="rail within limits",
            run_id="run_export",
        )
    ]
    (tmp_path / "tests" / "decks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests" / "decks" / "nominal.cir").write_text("* deck\n.end\n", encoding="utf-8")
    capability = ModelCapability(
        model_id="bm_reg_buck_test",
        kind=ModelKind.REDUCED_BEHAVIORAL,
        behaviors={key: "supported" for key in BEHAVIOR_KEYS},
        evidence_level=EvidenceLevel.SYNTHETIC_ANALYTICAL,
        source_model_hash="b" * 64,
    )
    return project, store, requirements, capability, tests, results


def test_export_writes_the_expected_file_set_with_relative_paths(tmp_path: Path) -> None:
    project, _store, requirements, capability, tests, results = build_project(tmp_path)
    out = tmp_path / "export"
    result = export_project(
        project,
        out,
        requirements=requirements,
        tests=tests,
        results=results,
        capability=capability,
        symbol_ports=PORTS,
        symbol_directions=DIRECTIONS,
        subckt="BM_REG_BUCK",
        parameters={"vin": "12 V", "load": "1 A"},
        simulator="LTspice 26.0.0.3",
        reproduction=["uv run boardmodeler export --project . --out export"],
    )
    names = set(result.relative_files())
    for expected in (
        "MODEL_CARD.md",
        "README.md",
        "manifest.json",
        "coverage.json",
        "requirements.json",
        "results.json",
        "model.lib",
        "symbol.asy",
        "tests/tests.json",
    ):
        assert expected in names, (expected, sorted(names))
    # Paths are relative and no file escapes the export directory.
    for entry in result.files:
        assert not entry.path.startswith("/") and ".." not in entry.path
        assert (out / entry.path).is_file()
    assert result.ok, [f.message for f in result.findings]


def test_export_manifest_hashes_every_file_and_records_parameters(tmp_path: Path) -> None:
    import hashlib

    project, _store, requirements, capability, tests, results = build_project(tmp_path)
    out = tmp_path / "export"
    result = export_project(
        project,
        out,
        requirements=requirements,
        tests=tests,
        results=results,
        capability=capability,
        symbol_ports=PORTS,
        parameters={"vin": "12 V"},
    )
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["parameters"] == {"vin": "12 V"}
    assert manifest["telemetry"] == "none"
    assert manifest["vendor_models_referenced_not_copied"] is True
    for entry in manifest["files"]:
        actual = hashlib.sha256((out / entry["path"]).read_bytes()).hexdigest()
        assert actual == entry["sha256"], entry["path"]
    assert result.manifest_path == out / "manifest.json"


def test_export_card_states_exclusions_and_never_claims_bench_evidence(tmp_path: Path) -> None:
    project, _store, requirements, capability, tests, results = build_project(tmp_path)
    out = tmp_path / "export"
    export_project(
        project,
        out,
        requirements=requirements,
        tests=tests,
        results=results,
        capability=capability,
        symbol_ports=PORTS,
        limitations=["vendor originals are not redistributed"],
    )
    card = (out / "MODEL_CARD.md").read_text(encoding="utf-8")
    assert "reproduces only the behaviour" in card
    assert "## Exclusions and unverified behaviour" in card
    assert "vendor originals are not redistributed" in card
    assert "BENCH" not in card


def test_export_reports_a_critical_requirement_with_no_test(tmp_path: Path) -> None:
    project, _store, requirements, capability, tests, results = build_project(tmp_path)
    out = tmp_path / "export"
    result = export_project(
        project,
        out,
        requirements=requirements,
        tests=tests,
        results=results,
        capability=capability,
        symbol_ports=PORTS,
    )
    codes = {finding.code for finding in result.findings}
    assert "COV001_critical_requirement_untested" in codes
    coverage = json.loads((out / "coverage.json").read_text(encoding="utf-8"))
    assert coverage["not_dynamically_covered"][0]["req_id"] == "REQ_B"


def test_export_refuses_to_redistribute_a_vendor_original(tmp_path: Path) -> None:
    project = create_project(tmp_path, project_id="prj_vendor", name="Vendor project")
    store = ModelStore(tmp_path)
    vendor_file = tmp_path / "vendor.lib"
    vendor_file.write_text(".subckt VENDOR a b\nR1 a b 1k\n.ends\n", encoding="utf-8")
    store.add_vendor_original(
        vendor_file, model_id="vendor_pkg", license_note="vendor terms; no redistribution"
    )
    out = tmp_path / "export"
    result = export_project(project, out, model_id="vendor_pkg")
    codes = {finding.code for finding in result.findings}
    assert "EXP001_vendor_original_not_exported" in codes
    assert not (out / "model.lib").exists()
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["model_hashes"].get("vendor_pkg")


def test_export_is_portable_after_being_copied(tmp_path: Path) -> None:
    """Nothing inside the export may reference the original project path."""
    import shutil

    project, _store, requirements, capability, tests, results = build_project(tmp_path)
    out = tmp_path / "export"
    export_project(
        project,
        out,
        requirements=requirements,
        tests=tests,
        results=results,
        capability=capability,
        symbol_ports=PORTS,
    )
    moved = tmp_path / "moved"
    shutil.copytree(out, moved)
    for path in moved.rglob("*"):
        if path.is_file() and path.suffix in (".md", ".json", ".lib", ".asy", ".cir"):
            text = path.read_text(encoding="utf-8", errors="replace")
            assert str(tmp_path).replace("\\", "\\\\") not in text, path
            assert str(out) not in text, path
