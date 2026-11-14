"""Validate an uploaded PDF before it is stored or parsed.

Checks run cheapest first, and nothing is read before the size check:
1. the path is an existing regular file;
2. size: not empty, not over ``max_bytes`` (from ``stat``, then again while reading);
3. ``%PDF-`` within the first 1024 bytes. This is only an initial filter (a renamed PNG fails
   here); it does not prove the file is a valid PDF. Step 4 does that;
4. PDFium (pypdfium2) can open it; a password-protected file is ``encrypted``;
5. no encryption at all, including owner-password-only PDFs, which open without a password;
6. page count between 1 and ``max_pages``.

Rejections raise ``DocumentValidationError`` with a stable ``reason``. Messages never contain the
filename (it can contain a patient's name), document content, or PDFium/OS error text. Only
``PdfiumError`` and ``OSError`` are translated; any other exception is a bug and propagates.
"""

import stat
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import pypdfium2
import pypdfium2.raw as pdfium_raw

from app.core.errors import DocumentValidationError

PDF_MAGIC = b"%PDF-"
MAGIC_WINDOW_BYTES = 1024  # PDF readers accept a header anywhere in the first 1024 bytes
_NOT_ENCRYPTED = -1  # FPDF_GetSecurityHandlerRevision result for an unencrypted document


class ValidationReason(StrEnum):
    NOT_A_FILE = "not_a_file"
    EMPTY = "empty"
    TOO_LARGE = "too_large"
    NOT_PDF = "not_pdf"
    UNREADABLE = "unreadable"
    ENCRYPTED = "encrypted"
    NO_PAGES = "no_pages"
    TOO_MANY_PAGES = "too_many_pages"


@dataclass(frozen=True)
class ValidatedPdf:
    """A PDF that passed every check. ``data`` is the exact uploaded bytes."""

    data: bytes = field(repr=False)
    size_bytes: int
    page_count: int


def _reject(reason: ValidationReason, message: str) -> DocumentValidationError:
    return DocumentValidationError(message, reason=reason)


def validate_pdf(source: Path, *, max_bytes: int, max_pages: int) -> ValidatedPdf:
    """Read and validate the file at ``source``.

    Raises:
        DocumentValidationError: with a ``ValidationReason`` value as ``reason``.
    """
    error: DocumentValidationError | None = None
    data = b""
    try:
        info = source.stat()
        if not stat.S_ISREG(info.st_mode):
            error = _reject(ValidationReason.NOT_A_FILE, "The upload is not a regular file")
        elif info.st_size == 0:
            error = _reject(ValidationReason.EMPTY, "The file is empty")
        elif info.st_size > max_bytes:
            error = _too_large(max_bytes)
        else:
            with source.open("rb") as handle:
                # One byte more than allowed: detects a file that grew after stat().
                data = handle.read(max_bytes + 1)
    except OSError:
        # The OS message can contain the path (and so the filename): keep only a fixed text.
        error = _reject(ValidationReason.NOT_A_FILE, "The upload could not be read as a file")
    # Raised outside the except block: no chained exception carries the OS message.
    if error is not None:
        raise error
    return validate_pdf_bytes(data, max_bytes=max_bytes, max_pages=max_pages)


def _too_large(max_bytes: int) -> DocumentValidationError:
    return _reject(
        ValidationReason.TOO_LARGE, f"The file is larger than the {max_bytes}-byte limit"
    )


def validate_pdf_bytes(data: bytes, *, max_bytes: int, max_pages: int) -> ValidatedPdf:
    """Validate PDF bytes already in memory (steps 2–6 of the module docstring)."""
    if not data:
        raise _reject(ValidationReason.EMPTY, "The file is empty")
    if len(data) > max_bytes:
        raise _too_large(max_bytes)
    if PDF_MAGIC not in data[:MAGIC_WINDOW_BYTES]:
        raise _reject(ValidationReason.NOT_PDF, "The file is not a PDF (no PDF header)")

    open_error: ValidationReason | None = None
    document: pypdfium2.PdfDocument | None = None
    try:
        document = pypdfium2.PdfDocument(data)
    except pypdfium2.PdfiumError as exc:
        is_password = exc.err_code == pdfium_raw.FPDF_ERR_PASSWORD
        open_error = ValidationReason.ENCRYPTED if is_password else ValidationReason.UNREADABLE
    if open_error is ValidationReason.ENCRYPTED:
        raise _reject(open_error, "The PDF is password-protected")
    if open_error is not None or document is None:
        raise _reject(ValidationReason.UNREADABLE, "The PDF is damaged or cannot be read")

    try:
        if pdfium_raw.FPDF_GetSecurityHandlerRevision(document.raw) != _NOT_ENCRYPTED:
            raise _reject(ValidationReason.ENCRYPTED, "The PDF is encrypted")
        page_count = len(document)
    finally:
        document.close()  # always, also when a check above raised

    if page_count == 0:
        raise _reject(ValidationReason.NO_PAGES, "The PDF has no pages")
    if page_count > max_pages:
        raise _reject(
            ValidationReason.TOO_MANY_PAGES,
            f"The PDF has {page_count} pages; the limit is {max_pages}",
        )
    return ValidatedPdf(data=data, size_bytes=len(data), page_count=page_count)
