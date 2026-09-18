"""Extraction layer: page chunking, strict payload parsing, cache reuse, egress.

Every test is offline. The fixture provider is used where replay determinism is
being proven, and a recording double is used where the *request* is the object of
the test (what was sent, to whom, and with which pages).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from boardmodeler.documents.chunk import chunk_document, chunk_text, select_pages
from boardmodeler.documents.pdf import PdfDocument, PdfPage
from boardmodeler.documents.store import DocumentStore
from boardmodeler.domain.enums import ProviderKind
from boardmodeler.domain.hashing import sha256_text
from boardmodeler.domain.records import DocumentRecord, ProviderIdentity
from boardmodeler.pipeline.project import Project, create_project
from boardmodeler.providers.base import (
    MAX_SNIPPET_CHARS,
    DocSnippet,
    ExtractionRequest,
    ExtractionResponse,
    ExtractionTask,
    ProviderCapabilities,
    ProviderError,
    ProviderHealth,
    request_hash,
)
from boardmodeler.providers.fixture import FixtureProvider
from boardmodeler.requirements.extract import (
    extract_requirements,
    schema_json,
    tasks_for,
)
from boardmodeler.security.policy import DataPolicy

CONTRACT_TEXT = (
    "Synthetic interface contract (test fixture).\n"
    "Input voltage range: 4.5 V to 60 V.\n"
    "Enable threshold: 1.2 V typical.\n"
    "Power-good is open drain.\n"
)
INVENTED_EXCERPT = "the input range is 3.0 V to 5.5 V"
EXCERPT = "Input voltage range: 4.5 V to 60 V."


# --------------------------------------------------------------------------- #
# fixtures and doubles


def make_project(tmp_path: Path) -> Project:
    return create_project(tmp_path / "proj", project_id="P_EXTRACT", name="Extraction test")


def store_text_document(project: Project, text: str = CONTRACT_TEXT) -> DocumentRecord:
    return DocumentStore(project.root).add_synthetic("contract.txt", text)


def store_plain_document(
    project: Project,
    text: str,
    *,
    doc_id: str,
    classification: str,
    remote_inference_allowed: bool,
) -> DocumentRecord:
    """Write a record directly, to exercise classification values the store refuses."""
    files_dir = project.root / "docs" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    name = f"{doc_id}-contract.txt"
    (files_dir / name).write_text(text, encoding="utf-8")
    record = DocumentRecord(
        doc_id=doc_id,
        title=doc_id,
        doc_type="synthetic_contract",
        file_hash=sha256_text(text),
        provenance="synthetic_fixture",
        classification=classification,
        remote_inference_allowed=remote_inference_allowed,
        page_count=1,
        text_extraction="embedded",
        path=f"docs/files/{name}",
    )
    (project.root / "docs" / f"{doc_id}.json").write_text(
        record.model_dump_json(indent=2), encoding="utf-8"
    )
    return record


def store_pdf_document(
    project: Project, pages: Sequence[str], *, name: str = "sheet"
) -> DocumentRecord:
    from reportlab.pdfgen import canvas

    source = project.root / f"{name}.pdf"
    sheet = canvas.Canvas(str(source))
    for text in pages:
        sheet.drawString(72, 720, text)
        sheet.showPage()
    sheet.save()
    return DocumentStore(project.root).add_file(
        source,
        doc_type="datasheet",
        provenance="downloaded_public",
        classification="public",
        remote_inference_allowed=True,
        title=name,
    )


class RecordingProvider:
    """A remote-shaped double: records the requests it was handed."""

    def __init__(
        self,
        payloads: Mapping[ExtractionTask, dict],
        *,
        kind: ProviderKind = ProviderKind.HTTP_INFERENCE,
        model: str = "documented-model",
    ) -> None:
        self.payloads = dict(payloads)
        self.kind = kind
        self.model = model
        self.requests: list[ExtractionRequest] = []

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            provider="recording",
            kind=self.kind,
            model=self.model,
            endpoint="http://127.0.0.1:9/v1/chat/completions",
            usage_units="tokens",
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            structured_output=True,
            max_snippet_chars_pages=MAX_SNIPPET_CHARS,
            streaming=False,
            usage_units="tokens",
        )

    def health(self, timeout_s: float) -> ProviderHealth:
        return ProviderHealth(ok=True, code="ok", detail="in-process test double")

    def extract(
        self, request: ExtractionRequest, cancel: object | None = None
    ) -> ExtractionResponse:
        self.requests.append(request)
        return ExtractionResponse(
            payload=self.payloads[request.task],
            identity=self.identity(),
            raw_text=json.dumps(self.payloads[request.task]),
            from_cache=False,
            request_hash=request_hash(request, provider="recording", model=self.model),
        )

    def snippets_for(self, task: ExtractionTask) -> tuple[DocSnippet, ...]:
        for request in self.requests:
            if request.task is task:
                return request.snippets
        raise AssertionError(f"the provider was never asked for {task.value}")


def requirement_payload(
    req_id: str,
    excerpt: str,
    *,
    page: int = 0,
    doc_id: str = "DOC_1",
) -> dict:
    return {
        "req_id": req_id,
        "applies_to": "device",
        "kind": "ELECTRICAL",
        "class": "DOCUMENTED_LIMIT",
        "criticality": "CRITICAL",
        "origin": "DOCUMENT",
        "statement": "the input voltage range is 4.5 V to 60 V",
        "limits": {"min": 4.5, "max": 60.0, "unit": "V"},
        "evidence": [
            {
                "doc_id": doc_id,
                "page": {"pdf_page": page},
                "excerpt": excerpt,
                "extraction": "embedded_text",
            }
        ],
    }


def pin_payload() -> dict:
    return {
        "part_id": "SYNTH",
        "pins": [
            {
                "physical_pin": "1",
                "name": "VIN",
                "function": "supply input",
                "polarity": "not_applicable",
                "direction": "power",
                "supply_domain": "VIN",
                "output_topology": "power",
                "connection_requirement": "required",
            },
            {
                "physical_pin": "2",
                "name": "GND",
                "function": "ground",
                "polarity": "not_applicable",
                "direction": "ground",
                "output_topology": "power",
                "connection_requirement": "required",
            },
        ],
    }


def payloads(*requirements: dict) -> dict[ExtractionTask, dict]:
    return {
        ExtractionTask.IDENTITY: {
            "part": {
                "manufacturer": "Synthetic",
                "base_part": "SYNTH",
                "confidence": "partial",
                "ambiguities": ["no revision marking in the fixture"],
            }
        },
        ExtractionTask.PINMAP: pin_payload(),
        ExtractionTask.REQUIREMENTS: {"requirements": list(requirements)},
        ExtractionTask.CAPABILITY_SUMMARY: {
            "behaviors": {"startup": "unknown", "thermal_dependence": "unsupported"}
        },
    }


def snippets_of(project: Project, *, max_chars: int = MAX_SNIPPET_CHARS) -> list[DocSnippet]:
    snippets: list[DocSnippet] = []
    for record in sorted(project.documents(), key=lambda item: item.doc_id):
        try:
            snippets.extend(chunk_document(record, base_dir=project.root, max_chars=max_chars))
        except OSError:
            continue
    return snippets


def author_fixtures(
    project: Project, fixture_dir: Path, data: Mapping[ExtractionTask, dict]
) -> FixtureProvider:
    provider = FixtureProvider(fixture_dir)
    snippets = snippets_of(project)
    for task, payload in data.items():
        provider.write_fixture(tasks_for(task, snippets), payload)
    return provider


# --------------------------------------------------------------------------- #
# chunking


def test_chunk_text_preserves_every_character_within_the_budget() -> None:
    long_line = "x" * 250
    text = "\n".join([f"row {index} " + "y" * 30 for index in range(12)] + [long_line])
    pieces = chunk_text("DOC_1", text, pdf_page=2, max_chars=100)
    assert len(pieces) > 1
    assert "".join(piece.text for piece in pieces) == text
    assert all(len(piece.text) <= 100 for piece in pieces)
    assert all(piece.pdf_page == 2 for piece in pieces)
    # A table row is not split across chunks unless it is longer than the budget.
    assert any("\n" not in piece.text for piece in pieces), "the long line must be hard-split"


def test_chunk_text_marks_an_empty_page_instead_of_dropping_it() -> None:
    pieces = chunk_text("DOC_1", "")
    assert [piece.text for piece in pieces] == [""]


def test_chunk_text_rejects_a_budget_above_the_snippet_cap() -> None:
    with pytest.raises(ValueError):
        chunk_text("DOC_1", "text", max_chars=MAX_SNIPPET_CHARS + 1)


def test_chunk_document_keeps_page_numbers_and_filters_pages() -> None:
    pages = [
        PdfPage(
            pdf_page=index,
            printed_label=f"p{index}",
            text=f"page {index} " + "z" * 40,
            char_count=0,
            has_embedded_text=True,
        )
        for index in range(3)
    ]
    document = PdfDocument(
        path=Path("sheet.pdf"),
        file_hash="0" * 64,
        page_count=3,
        pages=pages,
        page_labels={},
        text_extraction="embedded",
        title="sheet",
        metadata={},
    )
    everything = chunk_document(document, max_chars=20)
    assert [snippet.pdf_page for snippet in everything] == [0, 0, 0, 1, 1, 1, 2, 2, 2]
    assert [snippet.printed_label for snippet in everything] == ["p0"] * 3 + ["p1"] * 3 + ["p2"] * 3
    only_two = chunk_document(document, max_chars=20, pages=[2])
    assert {snippet.pdf_page for snippet in only_two} == {2}
    assert select_pages(everything, pages=[0, 2]) == [
        snippet for snippet in everything if snippet.pdf_page in (0, 2)
    ]
    assert select_pages(everything, pages=None) == everything


def test_chunk_document_reads_a_stored_text_document(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    record = store_text_document(project)
    snippets = chunk_document(record, base_dir=project.root)
    assert len(snippets) == 1
    assert snippets[0].doc_id == record.doc_id
    assert snippets[0].text == CONTRACT_TEXT
    with pytest.raises(FileNotFoundError):
        chunk_document(record, base_dir=tmp_path / "elsewhere")


# --------------------------------------------------------------------------- #
# extraction


def test_extraction_parses_every_task_and_verifies_citations(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    record = store_text_document(project)
    good = requirement_payload("REQ_GOOD", EXCERPT, doc_id=record.doc_id)
    invented = requirement_payload("REQ_INVENTED", INVENTED_EXCERPT, doc_id=record.doc_id)
    provider = author_fixtures(project, tmp_path / "fixtures", payloads(good, invented))

    result = extract_requirements(project, provider=provider, cache_dir=tmp_path / "cache")

    assert [requirement.req_id for requirement in result.requirements] == [
        "REQ_GOOD",
        "REQ_INVENTED",
    ]
    assert result.requirements[0].limits is not None
    assert result.pins[0].physical_pin == "1"
    assert result.pins[1].direction == "ground"
    assert result.part is not None and result.part.base_part == "SYNTH"
    assert result.capability_summary == {
        "startup": "unknown",
        "thermal_dependence": "unsupported",
    }
    assert result.disclosures == [], "fixture replay never leaves the machine"
    assert result.cache_hits == 0
    assert result.review.verified == ["REQ_GOOD"]
    assert result.review.unverified == ["REQ_INVENTED"]
    assert any(issue.req_id == "REQ_INVENTED" for issue in result.issues)
    assert "requirements=2" in result.detail


def test_the_second_extraction_makes_zero_provider_calls(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    record = store_text_document(project)
    provider = author_fixtures(
        project,
        tmp_path / "fixtures",
        payloads(requirement_payload("REQ_GOOD", EXCERPT, doc_id=record.doc_id)),
    )
    cache_dir = tmp_path / "cache"
    first = extract_requirements(project, provider=provider, cache_dir=cache_dir)
    calls_after_first = len(provider.requests)
    assert calls_after_first == 4
    assert first.cache_hits == 0

    second = extract_requirements(project, provider=provider, cache_dir=cache_dir)
    assert len(provider.requests) == calls_after_first, "replay must not ask the provider again"
    assert second.cache_hits == 4
    assert [requirement.req_id for requirement in second.requirements] == ["REQ_GOOD"]


def test_a_payload_that_does_not_validate_fails_the_whole_extraction(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    record = store_text_document(project)
    broken = requirement_payload("REQ_BAD", EXCERPT, doc_id=record.doc_id)
    del broken["statement"]
    provider = author_fixtures(project, tmp_path / "fixtures", payloads(broken))
    with pytest.raises(ProviderError) as excinfo:
        extract_requirements(project, provider=provider, cache_dir=tmp_path / "cache")
    assert excinfo.value.code == "extraction_payload_invalid"
    assert "REQUIREMENTS" in excinfo.value.detail
    assert "statement" in excinfo.value.detail


def test_an_unknown_capability_state_is_rejected(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    record = store_text_document(project)
    data = payloads(requirement_payload("REQ_GOOD", EXCERPT, doc_id=record.doc_id))
    data[ExtractionTask.CAPABILITY_SUMMARY] = {"behaviors": {"startup": "probably"}}
    provider = author_fixtures(project, tmp_path / "fixtures", data)
    with pytest.raises(ProviderError) as excinfo:
        extract_requirements(project, provider=provider, cache_dir=tmp_path / "cache")
    assert excinfo.value.code == "extraction_payload_invalid"
    assert "CAPABILITY_SUMMARY" in excinfo.value.detail


def test_an_unreadable_document_is_reported_and_the_others_continue(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    good = store_text_document(project)
    missing = store_text_document(project, "second contract\n")
    (project.root / missing.path).unlink()
    snippets = chunk_document(good, base_dir=project.root)
    provider = FixtureProvider(tmp_path / "fixtures")
    data = payloads(requirement_payload("REQ_GOOD", EXCERPT, doc_id=good.doc_id))
    for task, payload in data.items():
        provider.write_fixture(tasks_for(task, snippets), payload)

    result = extract_requirements(project, provider=provider, cache_dir=tmp_path / "cache")
    assert [finding.code for finding in result.findings] == ["extract_document_unreadable"]
    assert result.findings[0].detail["doc_id"] == missing.doc_id
    assert [requirement.req_id for requirement in result.requirements] == ["REQ_GOOD"]


def test_a_project_without_documents_is_an_explicit_error(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    provider = FixtureProvider(tmp_path / "fixtures")
    with pytest.raises(ProviderError) as excinfo:
        extract_requirements(project, provider=provider, cache_dir=tmp_path / "cache")
    assert excinfo.value.code == "no_documents"


# --------------------------------------------------------------------------- #
# egress


def test_a_denied_document_is_not_sent_and_is_reported(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    allowed = store_plain_document(
        project,
        CONTRACT_TEXT,
        doc_id="DOC_PUBLIC",
        classification="public",
        remote_inference_allowed=True,
    )
    denied = store_plain_document(
        project,
        "SECRET-PAGE-TEXT " + CONTRACT_TEXT,
        doc_id="DOC_UNKNOWN",
        classification="unknown",
        remote_inference_allowed=True,
    )
    provider = RecordingProvider(
        payloads(requirement_payload("REQ_GOOD", EXCERPT, doc_id="DOC_PUBLIC"))
    )

    result = extract_requirements(
        project,
        provider=provider,
        cache_dir=tmp_path / "cache",
        policy=DataPolicy(),
        allow_remote=True,
    )

    denied_findings = [
        finding for finding in result.findings if finding.code == "extract_egress_denied"
    ]
    assert len(denied_findings) == 1
    assert denied_findings[0].detail["doc_id"] == denied.doc_id
    assert denied_findings[0].detail["reason"] == "classification_unknown_and_denied"
    assert denied_findings[0].status.value == "BLOCKED"

    sent_doc_ids = {snippet.doc_id for request in provider.requests for snippet in request.snippets}
    assert sent_doc_ids == {allowed.doc_id}
    assert all(
        "SECRET-PAGE-TEXT" not in snippet.text
        for request in provider.requests
        for snippet in request.snippets
    )
    assert [disclosure.doc_ids for disclosure in result.disclosures] == [["DOC_PUBLIC"]]
    assert result.disclosures[0].endpoint == provider.identity().endpoint
    assert result.disclosures[0].chars > 0


def test_when_no_document_may_be_sent_the_failure_names_the_reason(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    store_plain_document(
        project,
        CONTRACT_TEXT,
        doc_id="DOC_UNKNOWN",
        classification="unknown",
        remote_inference_allowed=True,
    )
    provider = RecordingProvider(payloads())
    with pytest.raises(ProviderError) as excinfo:
        extract_requirements(
            project,
            provider=provider,
            cache_dir=tmp_path / "cache",
            policy=DataPolicy(),
            allow_remote=True,
        )
    assert excinfo.value.code == "egress_denied_no_documents"
    assert "classification_unknown_and_denied" in excinfo.value.detail
    assert provider.requests == []


def test_remote_disabled_by_policy_sends_nothing(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    store_plain_document(
        project,
        CONTRACT_TEXT,
        doc_id="DOC_PUBLIC",
        classification="public",
        remote_inference_allowed=True,
    )
    provider = RecordingProvider(payloads())
    with pytest.raises(ProviderError) as excinfo:
        extract_requirements(
            project, provider=provider, cache_dir=tmp_path / "cache", policy=DataPolicy()
        )
    assert excinfo.value.code == "egress_denied_no_documents"
    assert "remote_not_enabled" in excinfo.value.detail
    assert provider.requests == []


# --------------------------------------------------------------------------- #
# page selection and real PDFs


def test_task_pages_restrict_only_the_named_task(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    document = store_pdf_document(project, ["page one text", "page two text"])
    data = payloads(requirement_payload("REQ_1", "page two text", page=1, doc_id=document.doc_id))
    provider = RecordingProvider(data)

    result = extract_requirements(
        project,
        provider=provider,
        cache_dir=tmp_path / "cache",
        task_pages={ExtractionTask.REQUIREMENTS: [1]},
        policy=DataPolicy(),
        allow_remote=True,
    )

    assert {snippet.pdf_page for snippet in provider.snippets_for(ExtractionTask.REQUIREMENTS)} == {
        1
    }
    assert {snippet.pdf_page for snippet in provider.snippets_for(ExtractionTask.PINMAP)} == {0, 1}
    assert result.review.verified == ["REQ_1"]
    # Extraction never writes the verification itself: apply_review (run by the
    # pipeline's BUILD_REQUIREMENTS stage) is what sets citation_verified.
    assert result.requirements[0].citation_verified is False


def test_a_citation_on_the_wrong_page_is_not_verified(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    document = store_pdf_document(project, ["page one text", "page two text"])
    data = payloads(
        requirement_payload("REQ_WRONG", "page two text", page=0, doc_id=document.doc_id)
    )
    provider = RecordingProvider(data)
    result = extract_requirements(
        project,
        provider=provider,
        cache_dir=tmp_path / "cache",
        policy=DataPolicy(),
        allow_remote=True,
    )
    assert result.review.verified == []
    assert result.review.unverified == ["REQ_WRONG"]


def test_schema_json_is_stable_and_task_specific() -> None:
    first = schema_json(ExtractionTask.PINMAP)
    assert first == schema_json(ExtractionTask.PINMAP)
    assert first != schema_json(ExtractionTask.REQUIREMENTS)
    assert "physical_pin" in first
