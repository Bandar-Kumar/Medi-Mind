"""Unit tests for PDF validation (app/ingestion/validation.py). Synthetic PDFs only."""

from collections.abc import Callable
from pathlib import Path

import pypdfium2
import pytest

from app.core.errors import DocumentValidationError
from app.ingestion import validation
from app.ingestion.validation import ValidationReason, validate_pdf, validate_pdf_bytes
from tests.fixtures import pdfs

MAX_BYTES = 1_000_000
MAX_PAGES = 5
# A filename that looks like it holds personal data (fictional): it must never appear in errors.
PII_NAME = "Test Person DOB 01-01-1990 report.pdf"


def _write(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / PII_NAME
    path.write_bytes(data)
    return path


def _rejected(
    source: Path, reason: ValidationReason, *, max_bytes: int = MAX_BYTES
) -> DocumentValidationError:
    with pytest.raises(DocumentValidationError) as caught:
        validate_pdf(source, max_bytes=max_bytes, max_pages=MAX_PAGES)
    error = caught.value
    assert error.reason == reason
    assert "Test Person" not in str(error) and str(source) not in str(error)
    assert error.__cause__ is None and error.__context__ is None
    return error


@pytest.mark.parametrize("pages", [1, 3])
def test_valid_pdf_is_accepted_with_exact_bytes(tmp_path: Path, pages: int) -> None:
    data = pdfs.native_pdf(pages)
    result = validate_pdf(_write(tmp_path, data), max_bytes=MAX_BYTES, max_pages=MAX_PAGES)
    assert result.page_count == pages
    assert result.size_bytes == len(data)
    assert result.data == data
    assert "data=" not in repr(result)


def test_exactly_max_pages_is_accepted_and_one_more_is_rejected(tmp_path: Path) -> None:
    accepted = validate_pdf_bytes(pdfs.native_pdf(MAX_PAGES), max_bytes=MAX_BYTES, max_pages=5)
    assert accepted.page_count == MAX_PAGES
    error = _rejected(
        _write(tmp_path, pdfs.native_pdf(MAX_PAGES + 1)), ValidationReason.TOO_MANY_PAGES
    )
    assert str(MAX_PAGES) in str(error)


def test_missing_path_and_directory_are_not_files(tmp_path: Path) -> None:
    _rejected(tmp_path / "missing.pdf", ValidationReason.NOT_A_FILE)
    folder = tmp_path / "Test Person folder"
    folder.mkdir()
    _rejected(folder, ValidationReason.NOT_A_FILE)


def test_empty_file(tmp_path: Path) -> None:
    _rejected(_write(tmp_path, b""), ValidationReason.EMPTY)


def test_too_large_is_rejected_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write(tmp_path, pdfs.native_pdf())

    def no_open(*args: object, **kwargs: object) -> None:
        raise AssertionError("the file must not be opened")

    monkeypatch.setattr(Path, "open", no_open)
    _rejected(source, ValidationReason.TOO_LARGE, max_bytes=100)


def test_file_that_grows_after_stat_is_too_large() -> None:
    with pytest.raises(DocumentValidationError) as caught:
        validate_pdf_bytes(b"%PDF-" + b"x" * 200, max_bytes=100, max_pages=MAX_PAGES)
    assert caught.value.reason == ValidationReason.TOO_LARGE


def test_png_renamed_to_pdf_is_not_pdf(tmp_path: Path) -> None:
    _rejected(_write(tmp_path, pdfs.png_bytes()), ValidationReason.NOT_PDF)


def test_header_after_the_first_1024_bytes_is_not_pdf() -> None:
    data = b" " * 1024 + pdfs.native_pdf()
    with pytest.raises(DocumentValidationError) as caught:
        validate_pdf_bytes(data, max_bytes=MAX_BYTES, max_pages=MAX_PAGES)
    assert caught.value.reason == ValidationReason.NOT_PDF


@pytest.mark.parametrize("build", [pdfs.pdf_header_garbage, pdfs.truncated_pdf])
def test_header_alone_is_not_enough(tmp_path: Path, build: Callable[[], bytes]) -> None:
    _rejected(_write(tmp_path, build()), ValidationReason.UNREADABLE)


@pytest.mark.parametrize("build", [pdfs.user_password_pdf, pdfs.owner_only_encrypted_pdf])
def test_encrypted_pdfs_are_rejected(tmp_path: Path, build: Callable[[], bytes]) -> None:
    _rejected(_write(tmp_path, build()), ValidationReason.ENCRYPTED)


def test_document_is_closed_when_a_later_check_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []
    real_close = pypdfium2.PdfDocument.close

    def tracking_close(self: pypdfium2.PdfDocument) -> None:
        closed.append(True)
        real_close(self)

    monkeypatch.setattr(pypdfium2.PdfDocument, "close", tracking_close)
    for data, max_pages in ((pdfs.owner_only_encrypted_pdf(), MAX_PAGES), (pdfs.native_pdf(2), 1)):
        closed.clear()
        with pytest.raises(DocumentValidationError):
            validate_pdf_bytes(data, max_bytes=MAX_BYTES, max_pages=max_pages)
        assert closed, "the opened PDF was not closed"


def test_pdfium_message_is_never_exposed(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_open(*args: object, **kwargs: object) -> None:
        raise pypdfium2.PdfiumError("SECRET pdfium detail Test Person", err_code=3)

    monkeypatch.setattr(validation.pypdfium2, "PdfDocument", failing_open)
    with pytest.raises(DocumentValidationError) as caught:
        validate_pdf_bytes(pdfs.native_pdf(), max_bytes=MAX_BYTES, max_pages=MAX_PAGES)
    assert caught.value.reason == ValidationReason.UNREADABLE
    assert "SECRET" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_unexpected_errors_are_not_turned_into_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def buggy_open(*args: object, **kwargs: object) -> None:
        raise TypeError("programming error")

    monkeypatch.setattr(validation.pypdfium2, "PdfDocument", buggy_open)
    with pytest.raises(TypeError):
        validate_pdf_bytes(pdfs.native_pdf(), max_bytes=MAX_BYTES, max_pages=MAX_PAGES)
