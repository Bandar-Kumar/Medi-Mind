"""Render a ground-truth report to a native-text PDF in one of three layouts (Task 1.9).

Each ground-truth page becomes exactly one PDF page, with exactly its rows, so the (report, page)
labels hold by construction. ``invariant=1`` makes reportlab's output byte-identical for the same
input (no creation date or random document id), so dataset hashes are reproducible.

Layouts (ADR-025: Indian report conventions, DD/MM/YYYY dates):
- ``grid``: ruled table, columns Test / Result / Unit / Reference Range / Flag;
- ``plain``: unruled columns, Investigation / Observed Value / Units /
  Biological Reference Interval;
- ``compact``: Parameter / Value / Normal Range / Unit, flag appended to the value.
Every page carries the synthetic disclaimer (ADR-005).
"""

import io

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from evals.synthetic.catalog import PANEL_BY_KEY
from evals.synthetic.generator import PatientTruth, ReportTruth, Row

DISCLAIMER = "SYNTHETIC TEST DOCUMENT - fictional patient and values, not a medical record."
_WIDTH, _HEIGHT = A4
_LEFT = 40

_COLUMNS = {
    "grid": (("Test", 40), ("Result", 230), ("Unit", 320), ("Reference Range", 410), ("Flag", 520)),
    "plain": (
        ("Investigation", 40),
        ("Observed Value", 230),
        ("Units", 330),
        ("Biological Reference Interval", 410),
    ),
    "compact": (("Parameter", 40), ("Value", 250), ("Normal Range", 340), ("Unit", 460)),
}


def _cells(layout: str, row: Row) -> tuple[str, ...]:
    if layout == "grid":
        return (row.printed_name, row.value, row.unit, row.reference, row.flag)
    if layout == "plain":
        value = f"{row.value} {row.flag}".strip()
        return (row.printed_name, value, row.unit, row.reference)
    value = f"{row.value} {row.flag}".strip()
    return (row.printed_name, value, row.reference, row.unit)


def _header(
    pdf: canvas.Canvas, patient: PatientTruth, report: ReportTruth, page: int, pages: int
) -> float:
    y = _HEIGHT - 50
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(_LEFT, y, report.lab_name)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(_LEFT, y - 14, report.lab_address)
    pdf.setFont("Helvetica", 10)
    y -= 40
    age = int(report.date[:4]) - int(patient.date_of_birth[:4])
    pdf.drawString(_LEFT, y, f"Patient Name: {patient.display_name}")
    pdf.drawString(330, y, f"Age/Sex: {age} Y / {patient.sex}")
    pdf.drawString(_LEFT, y - 14, f"Report Date: {report.date_printed}")
    pdf.drawString(330, y - 14, f"Sample ID: {report.sample_id}")
    pdf.setFont("Helvetica", 8)
    pdf.drawString(_LEFT, 30, DISCLAIMER)
    pdf.drawRightString(_WIDTH - _LEFT, 30, f"Page {page} of {pages}")
    return y - 40


def _table(pdf: canvas.Canvas, layout: str, title: str, rows: tuple[Row, ...], y: float) -> float:
    columns = _COLUMNS[layout]
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(_LEFT, y, title)
    y -= 20
    pdf.setFont("Helvetica-Bold", 9)
    for label, x in columns:
        pdf.drawString(x, y, label)
    if layout == "grid":
        pdf.line(_LEFT - 4, y + 12, _WIDTH - _LEFT, y + 12)
        pdf.line(_LEFT - 4, y - 5, _WIDTH - _LEFT, y - 5)
    y -= 18
    pdf.setFont("Helvetica", 9)
    for row in rows:
        for (_, x), text in zip(columns, _cells(layout, row), strict=True):
            pdf.drawString(x, y, text)
        if layout == "grid":
            pdf.line(_LEFT - 4, y - 5, _WIDTH - _LEFT, y - 5)
        y -= 18
    return y - 16


def render_report(patient: PatientTruth, report: ReportTruth) -> bytes:
    """The PDF bytes of one report (deterministic)."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    pdf.setTitle(f"Synthetic lab report {report.report_id}")
    pages = len(report.pages)
    for page in report.pages:
        y = _header(pdf, patient, report, page.page_number, pages)
        for panel in page.panels:
            rows = tuple(
                row for row in page.rows if row.analyte_key in PANEL_BY_KEY[panel].analytes
            )
            y = _table(pdf, report.layout, PANEL_BY_KEY[panel].title, rows, y)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()
