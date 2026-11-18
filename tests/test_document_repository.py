"""Unit tests for document reads, rename and delete (Tasks 1.8c-1.8e). No database is used.

Validation must fail before any session exists (``_no_database``). The filename rules of the
records layer are checked against the ingestion sanitizer here, in a test, so the application code
needs no dependency from records to ingestion. All names are fictional.
"""

import datetime as dt
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.errors import DocumentProcessingError
from app.ingestion import storage
from app.records import repositories as repo
from app.records.models import Document

PATIENT_ID = uuid.uuid4()
DOCUMENT_ID = uuid.uuid4()
SECRET = "Secret Fictional Person"


def _no_database():
    raise AssertionError("validation must fail before a database session is created")


def _invalid(name: object) -> DocumentProcessingError:
    with pytest.raises(DocumentProcessingError) as caught:
        repo.rename_document(PATIENT_ID, DOCUMENT_ID, name, factory=_no_database)  # type: ignore[arg-type]
    assert caught.value.kind == repo.INVALID_FILENAME
    return caught.value


# --- canonical display filenames (rename, Task 1.8d) ---------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "report.pdf",
        "Lab report 12 March 2024.pdf",
        "_CON.pdf",  # the sanitizer's own output for a device name
        "Zoë Ñandú.pdf",
        "क्षमा रिपोर्ट.pdf",
        "a" * 251 + ".pdf",  # exactly 255 characters
        "no-extension",
    ],
)
def test_canonical_names_are_accepted_unchanged(name) -> None:
    assert repo._valid_display_filename(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "a" * 256,
        "../report.pdf",
        "folder\\report.pdf",
        "report" + chr(0) + ".pdf",
        "report\n.pdf",
        "report\t.pdf",
        "report" + chr(0x202E) + "fdp.exe",  # right-to-left override (Cf)
        chr(0x200B) + "report.pdf",  # zero width space (Cf)
        "report" + chr(0xD800) + ".pdf",  # lone surrogate: cannot be stored
        "Zoe" + chr(0x0308) + ".pdf",  # decomposed (not NFC)
        " report.pdf",
        "report.pdf ",
        "report.pdf.",
        ".hidden",
        "two  spaces.pdf",
        "no" + chr(0xA0) + "break.pdf",  # whitespace other than U+0020
        "CON.pdf",
        "con",
        "LPT1 .txt",
    ],
)
def test_non_canonical_names_are_rejected_before_the_database(name) -> None:
    _invalid(name)


@pytest.mark.parametrize("name", [None, 42, b"report.pdf", ["report.pdf"]])
def test_non_text_filenames_are_a_type_error(name) -> None:
    with pytest.raises(TypeError):
        repo.rename_document(PATIENT_ID, DOCUMENT_ID, name, factory=_no_database)


def test_errors_never_echo_the_filename() -> None:
    for bad in (SECRET + chr(0) + ".pdf", "  " + SECRET + ".pdf", SECRET * 20):
        error = _invalid(bad)
        assert "Secret" not in str(error) and "Secret" not in repr(error.args)
        assert error.__cause__ is None and error.__context__ is None


HOSTILE_INPUTS = [
    "report.pdf",
    "C:\\Users\\someone\\Documents\\report.pdf",
    "/etc/passwd",
    "  spaced   out  name .pdf ",
    "...",
    "",
    "   ",
    "CON",
    "con.pdf",
    "Com3.tar.gz",
    "LPT9 .txt",
    "report" + chr(0x202E) + "fdp.exe",
    chr(0xFEFF) + "bom.pdf",
    "tab\there.pdf",
    "line\nbreak.pdf",
    "Zoe" + chr(0x0308) + ".pdf",
    "no" + chr(0xA0) + "break.pdf",
    "a" * 300 + ".pdf",
    "a" * 300,
    "trailing dots...",
    "क्षमा रिपोर्ट.pdf",
]


@pytest.mark.parametrize("raw", HOSTILE_INPUTS)
def test_every_sanitizer_output_is_canonical(raw) -> None:
    """Drift guard: whatever upload stores, a rename to the same name must be accepted."""
    sanitized = storage.safe_display_filename(raw)
    assert repo._valid_display_filename(sanitized) == sanitized


@pytest.mark.parametrize("raw", HOSTILE_INPUTS)
def test_every_name_the_sanitizer_would_change_is_rejected(raw) -> None:
    """Drift guard: the repository never accepts a name the sanitizer would rewrite."""
    if storage.safe_display_filename(raw) != raw:
        _invalid(raw)
    else:
        assert repo._valid_display_filename(raw) == raw


def test_the_device_name_sets_and_length_limits_agree() -> None:
    assert repo._WINDOWS_DEVICE_NAMES == storage._WINDOWS_DEVICE_NAMES
    assert repo.MAX_FILENAME_CHARS == storage.MAX_FILENAME_CHARS


# --- projections (Task 1.8c) ---------------------------------------------------------------------


def _document() -> Document:
    now = dt.datetime(2026, 11, 16, 9, 30, tzinfo=dt.UTC)
    return Document(
        id=DOCUMENT_ID,
        patient_id=PATIENT_ID,
        original_filename=SECRET + ".pdf",
        storage_path="uploads/x/y.pdf",
        sha256="0" * 64,
        mime_type="application/pdf",
        size_bytes=10,
        page_count=2,
        doc_type="lab_report",
        doc_type_confidence=Decimal("0.875"),
        identity_check="mismatch",
        document_date=dt.date(2024, 3, 1),
        document_date_precision="month",
        processing_status="needs_review",
        created_at=now,
        updated_at=now,
    )


def test_summary_maps_every_field_and_omits_storage_path_and_hash() -> None:
    summary = repo._summary(_document())
    assert summary == repo.DocumentSummary(
        id=DOCUMENT_ID,
        patient_id=PATIENT_ID,
        original_filename=SECRET + ".pdf",
        page_count=2,
        processing_status="needs_review",
        doc_type="lab_report",
        document_date=dt.date(2024, 3, 1),
        document_date_precision="month",
        created_at=dt.datetime(2026, 11, 16, 9, 30, tzinfo=dt.UTC),
        updated_at=dt.datetime(2026, 11, 16, 9, 30, tzinfo=dt.UTC),
        doc_type_confidence=Decimal("0.875"),  # exact, never rounded to a float
        identity_check="mismatch",
    )
    assert not hasattr(summary, "storage_path") and not hasattr(summary, "sha256")


def test_summary_repr_hides_the_filename() -> None:
    summary = repo._summary(_document())
    assert "Secret" not in repr(summary) and str(DOCUMENT_ID) in repr(summary)


# --- delete (Task 1.8e) --------------------------------------------------------------------------


def test_delete_needs_a_positive_stale_threshold() -> None:
    with pytest.raises(ValueError):
        repo.delete_document(PATIENT_ID, DOCUMENT_ID, stale_after_seconds=0, factory=_no_database)


def test_supersedes_fk_constant_matches_the_model() -> None:
    [fk] = [
        c
        for c in Document.__table__.foreign_key_constraints
        if c.referred_table.name == "documents"
    ]
    assert fk.column_keys == ["supersedes_document_id", "patient_id"]
    assert fk.name == repo._DOCUMENT_SUPERSEDES_FK and fk.ondelete == "RESTRICT"


def _integrity_error(sqlstate: str | None, constraint: str | None) -> IntegrityError:
    orig = SimpleNamespace(sqlstate=sqlstate, diag=SimpleNamespace(constraint_name=constraint))
    return IntegrityError("DELETE ...", {}, orig)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("sqlstate", "constraint", "expected"),
    [
        ("23503", "fk_documents_supersedes_document_id_patient_id_documents", True),
        ("23503", "fk_documents_patient_id_patients", False),
        ("23503", "fk_pages_document_id_patient_id_documents", False),
        ("23503", None, False),
        # PostgreSQL reports a delete blocked by ON DELETE RESTRICT as 23001.
        ("23001", "fk_documents_supersedes_document_id_patient_id_documents", True),
        ("23001", "fk_pages_document_id_patient_id_documents", False),
        ("23505", "fk_documents_supersedes_document_id_patient_id_documents", False),
        (None, None, False),
    ],
)
def test_only_the_supersedes_fk_means_document_referenced(sqlstate, constraint, expected) -> None:
    names = frozenset({repo._DOCUMENT_SUPERSEDES_FK})
    assert repo._fk_violation(_integrity_error(sqlstate, constraint), names) is expected


def test_not_found_message_is_identical_for_any_unknown_id() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    a, b = repo._document_not_found(first), repo._document_not_found(second)
    assert a.kind == b.kind == repo.DOCUMENT_NOT_FOUND
    assert str(a).replace(str(first), "<id>") == str(b).replace(str(second), "<id>")
