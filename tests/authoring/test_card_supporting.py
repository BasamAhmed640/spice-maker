"""The model card's supporting-material section: listed, sourced, never a verdict.

The card is a generated public artifact, so asserting on its content is asserting on the
product's output. What matters: retrieved sources carry their URL and hash, claims we could
not confirm are labelled unverified with the reason, and nothing in the section can be
mistaken for a judged status.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from boardmodeler.authoring.card import render_card
from boardmodeler.authoring.harness import HarnessReport, ProbeOutcome
from boardmodeler.authoring.reinforce import ReinforcementReport, SourceRecord
from boardmodeler.authoring.spec import Characteristic, SpecSet


def _spec() -> SpecSet:
    return SpecSet(
        part="TPS54320",
        subckt="TPS54320",
        doc_id="doc_tps54320",
        characteristics=(
            Characteristic(
                char_id="REQ_TPS54320_ELEC_004",
                statement="The internal VIN UVLO threshold with VIN rising is between 4.0 V and 4.5 V.",
                unit="V",
                min_value=4.0,
                max_value=4.5,
                typ_value=None,
                target=None,
                source_page=4,
                excerpt="VIN internal UVLO threshold VIN rising 4.0 4.5 V",
                req_class="DOCUMENTED_LIMIT",
                probe="uvlo_rise",
                probe_params={},
                not_testable_reason=None,
            ),
        ),
    )


def _report() -> HarnessReport:
    return HarnessReport(
        part="TPS54320",
        model_sha256="ab" * 32,
        spec_digest="cd" * 32,
        outcomes=(
            ProbeOutcome(
                probe_id="uvlo_rise",
                status="PASS",
                measured={"vin_at_start": 4.30223},
                detail="measured 4.30223 V within 4.0-4.5 V",
                unknown_reason=None,
                run_dir="probes/uvlo_rise",
                char_ids=("REQ_TPS54320_ELEC_004",),
            ),
        ),
    )


def _reinforcement(status: str = "ok") -> ReinforcementReport:
    return ReinforcementReport(
        part="TPS54320",
        enabled=True,
        status=status,
        detail="2 source(s) retrieved, 1 unverified claim(s), 1 caveat(s), 1 suggested probe(s)",
        sources=(
            SourceRecord(
                url="https://www.ti.com/lit/an/slva535b/slva535b.pdf",
                claim="Application note on the device family",
                retrieved=True,
                excerpt="The device family requires a minimum output capacitance for stability.",
                sha256="9f" * 32,
                content_type="application/pdf",
                retrieved_utc="2026-09-18T12:00:00Z",
                reason=None,
            ),
            SourceRecord(
                url="https://example.invalid/forum/thread",
                claim="A forum thread claims a prebias limitation",
                retrieved=False,
                excerpt=None,
                sha256=None,
                content_type=None,
                retrieved_utc=None,
                reason="unverified_claim: http_error: 404",
            ),
        ),
        caveats=(
            "https://www.ti.com/lit/an/slva535b/slva535b.pdf: requires a minimum output capacitance",
        ),
        suggested_probes=("load_regulation",),
        spec_digest="cd" * 32,
    )


def test_the_card_lists_retrieved_sources_with_their_hash_and_url() -> None:
    card = render_card(
        part="TPS54320",
        subckt="TPS54320",
        spec=_spec(),
        report=_report(),
        model_file="TPS54320.lib",
        reinforcement=_reinforcement(),
    )
    assert "https://www.ti.com/lit/an/slva535b/slva535b.pdf" in card
    assert "9f9f9f9f9f9f" in card, "a retrieved source must carry its content hash"
    assert "requires a minimum output capacitance" in card


def test_an_unretrieved_claim_is_labelled_with_its_reason() -> None:
    card = render_card(
        part="TPS54320",
        subckt="TPS54320",
        spec=_spec(),
        report=_report(),
        model_file="TPS54320.lib",
        reinforcement=_reinforcement(),
    )
    assert "https://example.invalid/forum/thread" in card
    assert "404" in card
    assert "unverified" in card.lower()


def test_a_skipped_search_says_so_without_touching_the_verdicts() -> None:
    skipped = ReinforcementReport(
        part="TPS54320",
        enabled=False,
        status="skipped",
        detail="disabled by the caller",
        sources=(),
        caveats=(),
        suggested_probes=(),
    )
    card = render_card(
        part="TPS54320",
        subckt="TPS54320",
        spec=_spec(),
        report=_report(),
        model_file="TPS54320.lib",
        reinforcement=skipped,
    )
    assert "skipped" in card
    # the judged row keeps its status and measurement regardless
    assert "| PASS |" in card or "PASS" in card
    assert "4.30223" in card.replace("vin_at_start=", ""), card[-800:]


def test_no_reinforcement_report_leaves_the_section_out() -> None:
    card = render_card(
        part="TPS54320",
        subckt="TPS54320",
        spec=_spec(),
        report=_report(),
        model_file="TPS54320.lib",
    )
    assert "Supporting material" not in card


def test_the_card_is_written_into_the_output_directory(tmp_path: Path) -> None:
    from boardmodeler.authoring.card import write_deliverables

    written = write_deliverables(
        out_dir=tmp_path,
        part="TPS54320",
        subckt="TPS54320",
        spec=_spec(),
        report=_report(),
        reinforcement=_reinforcement(),
    )
    names = {path.name for path in written}
    assert {"MODEL_CARD.md", "example.cir", "install.md"} <= names
    card = (tmp_path / "MODEL_CARD.md").read_text(encoding="utf-8")
    assert "https://www.ti.com/lit/an/slva535b/slva535b.pdf" in card


def test_the_deliverables_accept_a_missing_reinforcement_report(tmp_path: Path) -> None:
    from boardmodeler.authoring.card import write_deliverables

    written = write_deliverables(
        out_dir=tmp_path,
        part="TPS54320",
        subckt="TPS54320",
        spec=_spec(),
        report=_report(),
    )
    assert (tmp_path / "MODEL_CARD.md").is_file()
    assert len(written) == 3


@pytest.mark.parametrize("status", ["skipped", "unavailable"])
def test_a_non_ok_status_never_claims_sources(status: str) -> None:
    report = ReinforcementReport(
        part="TPS54320",
        enabled=True,
        status=status,
        detail="nothing retrievable",
        sources=(),
        caveats=(),
        suggested_probes=(),
    )
    card = render_card(
        part="TPS54320",
        subckt="TPS54320",
        spec=_spec(),
        report=_report(),
        model_file="TPS54320.lib",
        reinforcement=report,
    )
    assert "nothing retrievable" in card
    assert "Sources retrieved at build time" not in card
