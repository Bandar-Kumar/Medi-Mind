"""Persist the pages of a verified parse IR as ``pages`` rows (Task 2.5c).

The bridge between the database-free IR (``app.ingestion.ir``) and the records layer: each
``ParsedPage`` becomes one ``PageRecord``, and ``persist_parsed_pages()`` (Task 2.5a) writes them
under the caller's parse claim. This module adds no persistence of its own.

Mapping (one row per page, in page order):
- ``page_number`` → ``page_number`` (the IR guarantees 1..page_count, in order, no gaps);
- ``text`` → ``text``, **exactly**: never stripped, normalized, repaired or re-encoded;
- ``parse_method`` → its value, ``"native"`` or ``"ocr"`` (a test keeps both enums identical);
- ``has_tables`` → ``has_tables`` (True when the page has at least one structured table).
OCR provenance, ``native_char_count``, table cells and review flags are not columns: they stay in
the stored IR file (ADR-029).

Before anything reaches the database, every page's text must be storable by PostgreSQL ``text``:
no NUL character, and encodable as UTF-8 (a lone surrogate is not). Such text is rejected with
``text_not_storable``, naming the page number only; it is never modified. The IR's page count must
equal the document's (``integrity_mismatch``).
"""

import uuid

from app.core.errors import DocumentProcessingError
from app.ingestion.ir import ParsedDocument, ParsedPage
from app.records.repositories import PageRecord, ProcessingErrorKind, persist_parsed_pages

_NUL = chr(0)


def page_records_from_ir(
    document: ParsedDocument, *, expected_page_count: int
) -> tuple[PageRecord, ...]:
    """Map a verified IR to page records, in page order. No database access.

    Raises:
        DocumentProcessingError: ``integrity_mismatch`` if the IR's page count differs from
            ``expected_page_count``; ``text_not_storable`` if a page's text contains NUL or cannot
            be encoded as UTF-8.
    """
    if document.page_count != expected_page_count or len(document.pages) != expected_page_count:
        raise DocumentProcessingError(
            f"The parsed document has {document.page_count} pages; "
            f"the document record has {expected_page_count}",
            kind=ProcessingErrorKind.INTEGRITY_MISMATCH,
        )
    return tuple(_page_record(page) for page in document.pages)


def persist_ir_pages(
    patient_id: uuid.UUID,
    document_id: uuid.UUID,
    run_id: uuid.UUID,
    document: ParsedDocument,
    *,
    expected_page_count: int,
) -> int:
    """Write the IR's pages under the parse claim ``run_id``; returns the number of pages stored.

    The records are built and checked first, so unstorable text never opens a transaction. The
    repository then re-checks the claim, refuses existing pages and stores all pages or none.

    Raises:
        DocumentProcessingError: from ``page_records_from_ir``, or from ``persist_parsed_pages``
            (``claim_lost``, ``already_parsed``, ``integrity_mismatch``, ``document_not_found``).
    """
    records = page_records_from_ir(document, expected_page_count=expected_page_count)
    return persist_parsed_pages(patient_id, document_id, run_id, records)


def _page_record(page: ParsedPage) -> PageRecord:
    _require_storable(page)
    return PageRecord(
        page_number=page.page_number,
        text=page.text,
        parse_method=page.parse_method.value,
        has_tables=page.has_tables,
    )


def _require_storable(page: ParsedPage) -> None:
    unstorable = _NUL in page.text
    if not unstorable:
        try:
            page.text.encode("utf-8")
        except UnicodeEncodeError:
            unstorable = True
    if unstorable:  # raised outside the except block: no chained error can carry the text
        raise DocumentProcessingError(
            f"Page {page.page_number} contains text that cannot be stored "
            "(a NUL character or invalid Unicode)",
            kind=ProcessingErrorKind.TEXT_NOT_STORABLE,
        )
