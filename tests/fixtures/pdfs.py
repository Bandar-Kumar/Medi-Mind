"""Synthetic test PDFs built in code with ReportLab (no binary files are committed).

All content is fictional. ``invariant=1`` removes ReportLab's timestamp and random document ID, so
the same call always returns the same bytes. The scanned variants are rendered with pypdfium2 and
Pillow (fixed random seed for the noise).
"""

import io
import random

import pypdfium2
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# ReportLab's bundled Vera font has every symbol below (µ ± ° – − ≤ ≥ ³ ×);
# the base-14 fonts do not.
_FONT = "MediMindVera"
pdfmetrics.registerFont(TTFont(_FONT, "Vera.ttf"))

# Ground truth for the Docling tests (fictional values). Units, decimals, Indian digit grouping and
# medical symbols must come back exactly from a native PDF.
LAB_TABLE: tuple[tuple[str, str, str, str], ...] = (
    ("Test", "Result", "Unit", "Reference range"),
    ("Haemoglobin", "13.5", "g/dL", "13.0–17.0"),
    ("Fasting glucose", "92", "mg/dL", "70–100"),
    ("Platelet count", "1,50,000", "/cumm", "1,50,000–4,50,000"),
    ("TSH", "2.5", "µIU/mL", "0.4–4.0"),
    ("Body temperature", "37.0", "°C", "36.1–37.2"),
    ("HbA1c", "5.4 ± 0.2", "%", "≤ 5.6"),
    ("Anti-HBs", "<10", "mIU/mL", "≥ 10"),
    ("Base excess", "−1.2", "mmol/L", "−2.0 to +2.0"),
    ("WBC", "7.2 × 10³", "/µL", "4.0–11.0"),
)
PATIENT_LINE = "Patient: Test Person (fictional)   Date: 03/02/2026"
SCAN_DPI = 200

# A fictional lab table: never real patient data.
_LAB_ROWS = (
    ("Test", "Result", "Unit", "Reference range"),
    ("Haemoglobin", "13.5", "g/dL", "13.0 - 17.0"),
    ("Fasting glucose", "92", "mg/dL", "70 - 100"),
    ("Platelet count", "2.5", "lakhs/cumm", "1.5 - 4.5"),
)


def _build(pages: int, encrypt: StandardEncryption | None = None) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, invariant=1, encrypt=encrypt)
    for number in range(1, pages + 1):
        pdf.drawString(72, 760, f"Synthetic Diagnostics Lab - fictional report, page {number}")
        pdf.drawString(72, 740, "Patient: Test Person (fictional)   Date: 03/02/2026")
        for row_index, row in enumerate(_LAB_ROWS):
            for col_index, value in enumerate(row):
                pdf.drawString(72 + col_index * 120, 700 - row_index * 18, value)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def native_pdf(pages: int = 1) -> bytes:
    """A valid, unencrypted PDF with a text layer and a small lab table on every page."""
    return _build(pages)


def user_password_pdf() -> bytes:
    """Encrypted with a user password: cannot be opened without it."""
    return _build(1, StandardEncryption("fictional-user-pw", ownerPassword="fictional-owner-pw"))


def owner_only_encrypted_pdf() -> bytes:
    """Encrypted with an owner password only: opens without a password, but is restricted."""
    return _build(1, StandardEncryption("", ownerPassword="fictional-owner-pw", canPrint=0))


def truncated_pdf() -> bytes:
    """A real PDF cut in half: has the header but is structurally broken."""
    data = native_pdf()
    return data[: len(data) // 2]


def pdf_header_garbage() -> bytes:
    """Starts with a PDF header, followed by bytes that are not a PDF."""
    return b"%PDF-1.7\n" + b"this is not a pdf body\n" * 20


def png_bytes() -> bytes:
    """The start of a PNG file (signature plus an IHDR chunk header): an image, not a PDF."""
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 17


# --- Docling fixtures (Task 2.4) ------------------------------------------------------------------


def page_marker(page_number: int) -> str:
    return f"PAGE-{page_number}-MARKER"


def lab_table_pdf(pages: int = 1) -> bytes:
    """A native lab report: a gridded LAB_TABLE and a unique page marker on every page."""
    buffer = io.BytesIO()
    style = ParagraphStyle("body", fontName=_FONT, fontSize=10, leading=14)
    story: list = []
    for number in range(1, pages + 1):
        story += [
            Paragraph("Synthetic Diagnostics Lab - fictional report", style),
            Paragraph(PATIENT_LINE, style),
            Paragraph(page_marker(number), style),
            Spacer(1, 12),
        ]
        table = Table([list(row) for row in LAB_TABLE], colWidths=[130, 80, 80, 130])
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), _FONT),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ]
            )
        )
        story.append(table)
        if number < pages:
            story.append(PageBreak())
    SimpleDocTemplate(buffer, pagesize=A4, invariant=1).build(story)
    return buffer.getvalue()


# Lines that Docling's text normalization is expected to rejoin: an attached hyphen at a line end is
# dropped when both sides are alphanumeric ("Anti-"/"HBs" -> "AntiHBs"; "4.0-"/"6.0" -> "4.06.0").
WRAPPED_LINES: tuple[str, ...] = (
    "Hepatitis B immunity: result for Anti-",
    "HBs antibody is reported below.",
    "Reference range for potassium is 4.0-",
    "6.0 mmol/L in this fictional report.",
)


def wrapped_text_pdf() -> bytes:
    """One paragraph whose lines break right after hyphens (to observe Docling's de-hyphenation)."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    pdf.setFont(_FONT, 11)
    for index, line in enumerate(WRAPPED_LINES):
        pdf.drawString(72, 760 - index * 14, line)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def blank_pdf() -> bytes:
    """One page with no content at all."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def scanned_pdf(source: bytes | None = None) -> bytes:
    """An image-only PDF (no text layer): each page of ``source`` rendered at 200 dpi, rotated 1°,
    with light salt-and-pepper noise from a fixed seed. Defaults to a one-page lab report."""
    document = pypdfium2.PdfDocument(source if source is not None else lab_table_pdf())
    rng = random.Random(2026)
    images: list[Image.Image] = []
    try:
        for index in range(len(document)):
            image = document[index].render(scale=SCAN_DPI / 72).to_pil().convert("L")
            image = image.rotate(1.0, expand=True, fillcolor=255, resample=Image.BICUBIC)
            pixels = image.load()
            width, height = image.size
            for _ in range(width * height // 400):
                pixels[rng.randrange(width), rng.randrange(height)] = rng.choice((0, 255))
            images.append(image)
    finally:
        document.close()
    buffer = io.BytesIO()
    images[0].save(buffer, "PDF", resolution=SCAN_DPI, save_all=True, append_images=images[1:])
    return buffer.getvalue()


def mixed_pdf() -> bytes:
    """Page 1 native (text layer), page 2 scanned (image only)."""
    combined = pypdfium2.PdfDocument(lab_table_pdf())
    scanned = pypdfium2.PdfDocument(scanned_pdf())
    try:
        combined.import_pages(scanned)
        buffer = io.BytesIO()
        combined.save(buffer)
        return buffer.getvalue()
    finally:
        scanned.close()
        combined.close()


def searchable_scan_pdf() -> bytes:
    """A "searchable scan": a page image plus an invisible text layer, as scanner software makes.

    The image is the lab report rendered at 200 dpi. On top, the same fictional text is written
    with PDF text rendering mode 3 (invisible: no ink). PDFium reads the invisible text; Docling
    treats invisible text as absent and runs OCR on the image instead.
    """
    document = pypdfium2.PdfDocument(lab_table_pdf())
    try:
        image = document[0].render(scale=SCAN_DPI / 72).to_pil().convert("L")
    finally:
        document.close()
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    width, height = A4
    pdf.drawImage(ImageReader(image), 0, 0, width=width, height=height)
    text = pdf.beginText(72, 780)
    text.setTextRenderMode(3)  # invisible
    text.setFont(_FONT, 10)
    text.textLine(PATIENT_LINE)
    for row in LAB_TABLE:
        text.textLine("  ".join(row))
    pdf.drawText(text)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


# Text runs for mixed_visibility_pdf(): (text, PDF text rendering mode, starts a new line).
# Mode 0 fills (visible), 3 is invisible, 7 only adds to the clipping path (no ink). Fictional.
VISIBILITY_RUNS: tuple[tuple[str, int, bool], ...] = (
    ("Haemoglobin 13.5 g/dL (13.0–17.0)", 0, True),
    ("Base excess −1.2 mmol/L (−2.0–2.0)", 3, True),
    ("Potassium 4.1 mmol/L (3.5–5.1)", 7, True),
    ("Fasting glucose 92 mg/dL", 0, True),
    ("Sodium 140 ", 0, True),  # one line: visible, invisible, visible
    ("hidden 135–145", 3, False),
    (" mmol/L", 0, False),
)
INVISIBLE_RENDER_MODES = frozenset({3, 7})


def visible_text(runs: tuple[tuple[str, int, bool], ...] = VISIBILITY_RUNS) -> str:
    """The text of the visibly drawn runs (for expected counts in tests)."""
    return "".join(text for text, mode, _ in runs if mode not in INVISIBLE_RENDER_MODES)


def invisible_text(runs: tuple[tuple[str, int, bool], ...] = VISIBILITY_RUNS) -> str:
    """The text of the runs drawn without ink (modes 3 and 7)."""
    return "".join(text for text, mode, _ in runs if mode in INVISIBLE_RENDER_MODES)


def classified_text(
    runs: tuple[tuple[str, int, bool], ...] = VISIBILITY_RUNS,
) -> list[tuple[str, bool]]:
    """Every character of the runs as (character, visible), for expected text-layer counts."""
    return [(char, mode not in INVISIBLE_RENDER_MODES) for text, mode, _ in runs for char in text]


def mixed_visibility_pdf(runs: tuple[tuple[str, int, bool], ...] = VISIBILITY_RUNS) -> bytes:
    """One native page mixing visible, invisible and clip-only text, also within one line."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    text = pdf.beginText(72, 780)
    text.setFont(_FONT, 10)
    first = True
    for run, mode, new_line in runs:
        if new_line and not first:
            text.textLine("")
        text.setTextRenderMode(mode)
        text.textOut(run)
        first = False
    pdf.drawText(text)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def invisible_text_pdf() -> bytes:
    """A page whose only text is invisible, with no image: nothing is drawn at all."""
    return mixed_visibility_pdf(tuple((text, 3, new_line) for text, _, new_line in VISIBILITY_RUNS))


def clip_only_text_pdf() -> bytes:
    """A page whose only text is clip-only (mode 7), with no image: nothing is drawn at all."""
    return mixed_visibility_pdf(tuple((text, 7, new_line) for text, _, new_line in VISIBILITY_RUNS))
