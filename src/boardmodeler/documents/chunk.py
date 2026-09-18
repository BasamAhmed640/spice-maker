"""Deterministic page-level chunking (Phase 4 step 1).

Extraction sees documents as ordered :class:`~boardmodeler.providers.base.DocSnippet`
chunks: one document, one 0-based PDF page, at most
:data:`~boardmodeler.providers.base.MAX_SNIPPET_CHARS` characters. Chunking is a
pure function of the document bytes and ``max_chars`` — the same document always
produces the same chunks in the same order, which is what makes the extraction
cache key (:func:`boardmodeler.providers.base.request_hash`) meaningful.

Two rules make chunking safe rather than lossy:

* Over-long pages are split on **line boundaries** (a table row stays in one
  piece) and, only when a single line is longer than the budget, mid-line. Every
  piece is a contiguous slice of the page text, so
  ``"".join(chunk.text for chunk in chunks)`` reproduces the page exactly:
  nothing is dropped, reordered, or summarized.
* Pages are **never** silently skipped. A page with no extractable text yields
  one empty snippet, so the page numbering the provider sees matches the PDF.

``chunk_text`` covers text-only documents (synthetic contracts stored by
:meth:`boardmodeler.documents.store.DocumentStore.add_synthetic`, which are plain
``.txt`` files presented as one logical page).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from boardmodeler.documents.pdf import PdfDocument, read_pdf
from boardmodeler.domain.records import DocumentRecord
from boardmodeler.providers.base import MAX_SNIPPET_CHARS, DocSnippet

__all__ = [
    "MAX_SNIPPET_CHARS",
    "DocSnippet",
    "chunk_document",
    "chunk_text",
    "select_pages",
]

_TEXT_SUFFIXES = frozenset({".txt", ".text", ".md"})


def chunk_text(
    doc_id: str,
    text: str,
    *,
    pdf_page: int = 0,
    max_chars: int = MAX_SNIPPET_CHARS,
    printed_label: str | None = None,
) -> list[DocSnippet]:
    """Chunk one page's ``text`` into snippets of at most ``max_chars`` characters.

    The pieces' concatenation is exactly ``text``; an empty page yields one empty
    snippet rather than nothing, so the caller's page count never changes.
    """
    _check_max_chars(max_chars)
    if not doc_id:
        raise ValueError("doc_id must be a non-empty string")
    if pdf_page < 0:
        raise ValueError(f"pdf_page is 0-based and must be >= 0, got {pdf_page}")
    return [
        DocSnippet(
            doc_id=doc_id,
            pdf_page=pdf_page,
            printed_label=printed_label,
            text=piece,
        )
        for piece in _split_text(text, max_chars)
    ]


def chunk_document(
    doc: PdfDocument | DocumentRecord,
    *,
    max_chars: int = MAX_SNIPPET_CHARS,
    pages: Iterable[int] | None = None,
    base_dir: Path | None = None,
) -> list[DocSnippet]:
    """Chunk a document page by page, optionally restricted to ``pages``.

    ``doc`` is either an already-read :class:`PdfDocument`, or a
    :class:`~boardmodeler.domain.records.DocumentRecord` whose ``path`` is
    resolved against ``base_dir`` (pass the project root for a record stored by
    :class:`~boardmodeler.documents.store.DocumentStore`). A record pointing at a
    text file is read as one page; anything else is parsed as a PDF, reading only
    as far as the last requested page.

    A :class:`PdfDocument` carries no ``doc_id``, so its path stem is used —
    pass the record when the id matters.
    """
    _check_max_chars(max_chars)
    if isinstance(doc, PdfDocument):
        wanted = _page_filter(pages)
        return _chunk_pages(
            doc_id=doc.path.stem or "document",
            pages=[
                (page.pdf_page, page.printed_label, page.text)
                for page in doc.pages
                if wanted is None or page.pdf_page in wanted
            ],
            max_chars=max_chars,
        )

    if isinstance(doc, DocumentRecord):
        path = _resolve_record_path(doc, base_dir)
        wanted = _page_filter(pages)
        if path.suffix.lower() in _TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8", errors="replace")
            if wanted is not None and 0 not in wanted:
                return []
            return chunk_text(
                doc.doc_id,
                text,
                pdf_page=0,
                max_chars=max_chars,
                printed_label=doc.page_labels.get(0),
            )
        limit = None if wanted is None else max(wanted) + 1
        parsed = read_pdf(path, max_pages=limit)
        return _chunk_pages(
            doc_id=doc.doc_id,
            pages=[
                (page.pdf_page, page.printed_label, page.text)
                for page in parsed.pages
                if wanted is None or page.pdf_page in wanted
            ],
            max_chars=max_chars,
        )

    raise TypeError(
        f"chunk_document expects a PdfDocument or DocumentRecord, got {type(doc).__name__}"
    )


def select_pages(
    snippets: Sequence[DocSnippet], *, pages: Iterable[int] | None
) -> list[DocSnippet]:
    """The snippets whose ``pdf_page`` is in ``pages`` (``None`` keeps every page).

    The returned list keeps the input order, so a task that is restricted to a
    page subset still hashes identically for identical inputs.
    """
    if pages is None:
        return list(snippets)
    wanted = _page_filter(pages)
    return [snippet for snippet in snippets if wanted is not None and snippet.pdf_page in wanted]


# --------------------------------------------------------------------------- #
# internals


def _check_max_chars(max_chars: int) -> None:
    if not isinstance(max_chars, int) or isinstance(max_chars, bool):
        raise TypeError(f"max_chars must be an int, got {type(max_chars).__name__}")
    if max_chars < 1:
        raise ValueError(f"max_chars must be >= 1, got {max_chars}")
    if max_chars > MAX_SNIPPET_CHARS:
        raise ValueError(
            f"max_chars must be <= {MAX_SNIPPET_CHARS} (the DocSnippet cap), got {max_chars}"
        )


def _page_filter(pages: Iterable[int] | None) -> frozenset[int] | None:
    if pages is None:
        return None
    wanted = frozenset(int(page) for page in pages)
    negative = sorted(page for page in wanted if page < 0)
    if negative:
        raise ValueError(f"page numbers are 0-based and must be >= 0, got {negative}")
    return wanted


def _resolve_record_path(record: DocumentRecord, base_dir: Path | None) -> Path:
    if not record.path:
        raise ValueError(f"document {record.doc_id!r} has no stored path")
    path = Path(record.path)
    if not path.is_absolute() and base_dir is not None:
        path = Path(base_dir) / path
    if not path.is_file():
        raise FileNotFoundError(
            f"document {record.doc_id!r} records {record.path!r}, which resolves to {path} "
            "and is not a file"
        )
    return path


def _chunk_pages(
    *, doc_id: str, pages: Sequence[tuple[int, str | None, str]], max_chars: int
) -> list[DocSnippet]:
    snippets: list[DocSnippet] = []
    for pdf_page, printed_label, text in pages:
        snippets.extend(
            chunk_text(
                doc_id,
                text,
                pdf_page=pdf_page,
                max_chars=max_chars,
                printed_label=printed_label,
            )
        )
    return snippets


def _split_text(text: str, max_chars: int) -> list[str]:
    """Split ``text`` into contiguous pieces of at most ``max_chars`` characters.

    Cut points prefer a line boundary, so a table row is not split across two
    chunks; a line longer than the budget is split mid-line, which is the only
    case where a piece does not end at a newline.
    """
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    start = 0
    total = len(text)
    while start < total:
        end = min(start + max_chars, total)
        if end < total:
            cut = text.rfind("\n", start + 1, end)
            if cut > start:
                end = cut + 1
        pieces.append(text[start:end])
        start = end
    return pieces
