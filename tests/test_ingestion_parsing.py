"""Unit tests for the Docling parser (app/ingestion/parsing.py) with a stand-in converter.

No Docling pipeline runs and no models are loaded or downloaded. The real exception classes from
huggingface_hub, requests, rapidocr and docling.exceptions are used (all light imports, no torch).
"""

import errno
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from docling.exceptions import (
    AcceleratorDeviceNotAvailableError,
    ConversionError,
    DocumentLoadError,
    OcrLanguageNotSupportedError,
)
from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem, Size
from huggingface_hub.errors import (
    HfHubHTTPError,
    LocalEntryNotFoundError,
    OfflineModeIsEnabled,
    RepositoryNotFoundError,
)
from rapidocr.utils.download_file import DownloadFileException

from app.core.config import Settings
from app.core.errors import DocumentParseError
from app.ingestion import parsing
from app.ingestion.docling_mapping import ReviewReason, TextLayerStats, text_layer_stats
from app.ingestion.ir import ParseMethod
from app.ingestion.parsing import (
    DOCLING_STREAM_NAME,
    RAPIDOCR_MODEL_LOADERS,
    VERIFIED_RAPIDOCR_VERSION,
    DoclingParser,
    ParseErrorKind,
    ParserOptions,
    _text_layer_stats,
    preload_ocr_models,
)
from app.ingestion.validation import ValidatedPdf, validate_pdf_bytes
from tests.fixtures import pdfs

SECRET = "SECRET Test Person 13.5 g/dL"
OPTIONS = ParserOptions(timeout_seconds=600, max_pages=20, max_bytes=20 * 1024 * 1024)


class _Status(StrEnum):  # stands in for docling's ConversionStatus (a str Enum)
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILURE = "failure"
    SKIPPED = "skipped"


def _instance(cls: type[Exception], message: str) -> Exception:
    """An instance of a real exception class whose constructor needs extra arguments."""
    exc = cls.__new__(cls)
    Exception.__init__(exc, message)
    return exc


def _validated(data: bytes) -> ValidatedPdf:
    return validate_pdf_bytes(data, max_bytes=OPTIONS.max_bytes, max_pages=OPTIONS.max_pages)


def _document(texts: dict[int, str]) -> DoclingDocument:
    document = DoclingDocument(name="document")
    for number in sorted(texts):
        document.add_page(page_no=number, size=Size(width=595, height=842))
        text = texts[number]
        document.add_text(
            label=DocItemLabel.TEXT,
            text=text,
            prov=ProvenanceItem(
                page_no=number, bbox=BoundingBox(l=1, t=2, r=3, b=1), charspan=(0, len(text))
            ),
        )
    return document


def _cells(*cells: tuple[str, bool, float | None]) -> Any:
    return SimpleNamespace(
        textline_cells=[
            SimpleNamespace(text=t, orig=t, from_ocr=ocr, confidence=c) for t, ocr, c in cells
        ]
    )


def _result(
    status: Any = _Status.SUCCESS,
    *,
    texts: dict[int, str] | None = None,
    pages: list[Any] | None = None,
    errors: list[Any] | None = None,
    document: Any = "default",
) -> Any:
    texts = texts if texts is not None else {1: "Haemoglobin 13.5 g/dL"}
    if pages is None:
        pages = [
            SimpleNamespace(page_no=n, parsed_page=_cells((t, False, None)))
            for n, t in texts.items()
        ]
    return SimpleNamespace(
        status=status,
        errors=errors or [],
        pages=pages,
        document=_document(texts) if document == "default" else document,
    )


class FakeConverter:
    instances = 0

    def __init__(
        self,
        result: Any = None,
        *,
        init_error: BaseException | None = None,
        convert_error: BaseException | None = None,
    ) -> None:
        self.result = result if result is not None else _result()
        self.init_error = init_error
        self.convert_error = convert_error
        self.initialized = 0
        self.received: list[bytes] = []

    def initialize(self) -> None:
        self.initialized += 1
        if self.init_error is not None:
            raise self.init_error

    def convert(self, data: bytes, *, max_pages: int, max_bytes: int) -> Any:
        assert (max_pages, max_bytes) == (OPTIONS.max_pages, OPTIONS.max_bytes)
        self.received.append(data)
        if self.convert_error is not None:
            raise self.convert_error
        return self.result


def _parser(converter: FakeConverter, counter: list[int] | None = None) -> DoclingParser:
    def factory(options: ParserOptions) -> FakeConverter:
        assert options == OPTIONS
        if counter is not None:
            counter.append(1)
        return converter

    return DoclingParser(OPTIONS, converter_factory=factory)


def _parse_error(action: Callable[[], Any]) -> DocumentParseError:
    with pytest.raises(DocumentParseError) as caught:
        action()
    error = caught.value
    assert SECRET not in str(error) and "Test Person" not in str(error)
    assert error.__cause__ is None and error.__context__ is None
    return error


# --- happy path and reuse -------------------------------------------------------------------------


def test_parse_maps_a_native_page_and_keeps_the_input_unchanged() -> None:
    pdf = _validated(pdfs.lab_table_pdf())
    before = hashlib.sha256(pdf.data).hexdigest()
    converter = FakeConverter()
    result = _parser(converter).parse(pdf)
    page = result.document.pages[0]
    assert page.text == "Haemoglobin 13.5 g/dL"
    assert page.parse_method == ParseMethod.NATIVE
    assert page.native_char_count > 0  # counted from the PDF's real text layer
    assert result.document.sha256 == before
    assert converter.received == [pdf.data]
    assert hashlib.sha256(pdf.data).hexdigest() == before
    assert json.loads(result.docling_json)["texts"][0]["text"] == "Haemoglobin 13.5 g/dL"
    assert "Haemoglobin" not in repr(result)
    assert DOCLING_STREAM_NAME == "document.pdf"


def test_one_converter_and_one_model_initialization_serve_a_batch() -> None:
    converter, built = FakeConverter(), []
    parser = _parser(converter, built)
    parser.warm_up()
    for _ in range(3):
        parser.parse(_validated(pdfs.lab_table_pdf()))
    assert (len(built), converter.initialized, len(converter.received)) == (1, 1, 3)


def test_scanned_page_is_ocr_with_engine_provenance() -> None:
    pages = [SimpleNamespace(page_no=1, parsed_page=_cells(("13.5", True, 0.93)))]
    converter = FakeConverter(_result(texts={1: "13.5"}, pages=pages))
    result = _parser(converter).parse(_validated(pdfs.scanned_pdf()))
    page = result.document.pages[0]
    assert page.parse_method == ParseMethod.OCR and page.native_char_count == 0
    assert page.ocr is not None and (page.ocr.engine, page.ocr.backend) == ("rapidocr", "torch")
    assert page.ocr.engine_version == "3.10.0"
    assert page.ocr.mean_confidence == pytest.approx(0.93)


def test_parser_info_records_versions_ocr_and_text_provenance() -> None:
    info = _parser(FakeConverter()).parser_info
    assert (info.name, info.version) == ("docling", "2.150.1")
    assert info.options["docling_core_version"] == "2.107.0"
    assert info.options["ocr_backend"] == "torch" and info.options["ocr_engine"] == "rapidocr"
    assert info.options["text_source"] == "docling_extracted_normalized"
    assert info.options["raw_text_preserved"] is False
    assert info.options["authoritative_source"] == "original_pdf"
    assert "en_dash" in str(info.options["text_normalization"])
    assert "dehyphenation" in str(info.options["text_normalization"])
    assert "pdfium" in str(info.options["normalization_check"])


def test_review_flags_are_returned() -> None:
    converter = FakeConverter(_result(texts={1: ""}))
    result = _parser(converter).parse(_validated(pdfs.blank_pdf()))
    assert any(f.reason == ReviewReason.EMPTY_PAGE for f in result.review_flags)


# --- status mapping (deterministic, including timeout) --------------------------------------------


def _error_item(category: str, component: str = "pipeline") -> Any:
    return SimpleNamespace(category=category, component_type=component, error_message=SECRET)


@pytest.mark.parametrize(
    ("status", "errors", "kind"),
    [
        (_Status.PARTIAL_SUCCESS, [_error_item("timeout")], ParseErrorKind.TIMEOUT),
        ("partial_success", [_error_item("inference_failure"), _error_item("timeout")], "timeout"),
        (_Status.PARTIAL_SUCCESS, [_error_item("backend_failure")], ParseErrorKind.PARTIAL_OUTPUT),
        (_Status.PARTIAL_SUCCESS, [], ParseErrorKind.PARTIAL_OUTPUT),
        (_Status.FAILURE, [_error_item("backend_failure", "document_backend")], "parser_failed"),
        (_Status.SKIPPED, [], ParseErrorKind.PARSER_FAILED),
        ("pending", [], ParseErrorKind.PARSER_FAILED),
    ],
)
def test_non_success_statuses_are_refused(status: Any, errors: list[Any], kind: str) -> None:
    converter = FakeConverter(_result(status, errors=errors))
    error = _parse_error(lambda: _parser(converter).parse(_validated(pdfs.lab_table_pdf())))
    assert error.kind == kind


def test_timeout_message_names_the_limit_only() -> None:
    converter = FakeConverter(_result(_Status.PARTIAL_SUCCESS, errors=[_error_item("timeout")]))
    error = _parse_error(lambda: _parser(converter).parse(_validated(pdfs.lab_table_pdf())))
    assert str(error) == "Parsing exceeded the 600-second limit"


def test_missing_document_is_unexpected_output() -> None:
    converter = FakeConverter(_result(document=None))
    error = _parse_error(lambda: _parser(converter).parse(_validated(pdfs.lab_table_pdf())))
    assert error.kind == ParseErrorKind.UNEXPECTED_OUTPUT


def test_docling_pages_that_do_not_match_the_pdf_are_refused() -> None:
    pages = [SimpleNamespace(page_no=1, parsed_page=_cells())]  # the PDF has 3 pages
    converter = FakeConverter(_result(texts={1: "x"}, pages=pages))
    error = _parse_error(lambda: _parser(converter).parse(_validated(pdfs.lab_table_pdf(3))))
    assert error.kind == ParseErrorKind.UNEXPECTED_OUTPUT


# --- exceptions -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (LocalEntryNotFoundError(SECRET), ParseErrorKind.MODELS_UNAVAILABLE),
        (_instance(HfHubHTTPError, SECRET), ParseErrorKind.MODELS_UNAVAILABLE),
        (_instance(RepositoryNotFoundError, SECRET), ParseErrorKind.MODELS_UNAVAILABLE),  # subclass
        (OfflineModeIsEnabled(SECRET), ParseErrorKind.MODELS_UNAVAILABLE),
        (requests.ConnectionError(SECRET), ParseErrorKind.MODELS_UNAVAILABLE),
        (DownloadFileException(SECRET), ParseErrorKind.MODELS_UNAVAILABLE),
        (_instance(OcrLanguageNotSupportedError, SECRET), ParseErrorKind.PARSER_MISCONFIGURED),
        (AcceleratorDeviceNotAvailableError(SECRET), ParseErrorKind.PARSER_MISCONFIGURED),
        (PermissionError(errno.EACCES, SECRET), ParseErrorKind.PARSER_ENVIRONMENT),
        (OSError(errno.ENOSPC, SECRET), ParseErrorKind.PARSER_ENVIRONMENT),
    ],
)
def test_known_model_initialization_failures_are_classified(exc: Exception, kind: str) -> None:
    error = _parse_error(lambda: _parser(FakeConverter(init_error=exc)).warm_up())
    assert error.kind == kind


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionResetError(SECRET),  # an OSError, but not a known model-download failure
        FileNotFoundError(errno.ENOENT, SECRET),
        OSError(errno.EIO, SECRET),
        TypeError(SECRET),
        KeyError(SECRET),
    ],
)
def test_unknown_initialization_errors_propagate_unchanged(exc: Exception) -> None:
    with pytest.raises(type(exc)) as caught:
        _parser(FakeConverter(init_error=exc)).warm_up()
    assert caught.value is exc


@pytest.mark.parametrize("exc", [ConversionError(SECRET), DocumentLoadError(SECRET)])
def test_docling_conversion_errors_are_parser_failed(exc: Exception) -> None:
    converter = FakeConverter(convert_error=exc)
    error = _parse_error(lambda: _parser(converter).parse(_validated(pdfs.lab_table_pdf())))
    assert error.kind == ParseErrorKind.PARSER_FAILED
    assert type(exc).__name__ in str(error)


@pytest.mark.parametrize(
    "exc",
    [
        LocalEntryNotFoundError(SECRET),  # model errors are only classified at initialization
        ConnectionResetError(SECRET),
        FileNotFoundError(SECRET),
        ValueError(SECRET),
        AttributeError(SECRET),
    ],
)
def test_other_conversion_errors_propagate_unchanged(exc: Exception) -> None:
    converter = FakeConverter(convert_error=exc)
    with pytest.raises(type(exc)) as caught:
        _parser(converter).parse(_validated(pdfs.lab_table_pdf()))
    assert caught.value is exc


def test_a_failed_initialization_is_retried_on_the_next_call() -> None:
    converter = FakeConverter(init_error=LocalEntryNotFoundError(SECRET))
    parser = _parser(converter)
    _parse_error(parser.warm_up)
    converter.init_error = None
    parser.warm_up()
    assert converter.initialized == 2


# --- import boundary ------------------------------------------------------------------------------


def test_creating_a_parser_loads_neither_docling_nor_torch() -> None:
    code = (
        "import sys\n"
        "from app.core.config import Settings\n"
        "from app.ingestion.parsing import DoclingParser\n"
        "DoclingParser.from_settings(Settings())\n"
        "heavy = [m for m in ('docling', 'docling_core', 'rapidocr', 'torch', 'transformers')"
        " if m in sys.modules]\n"
        "print(','.join(heavy))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == ""


def test_from_settings_uses_the_configured_limits() -> None:
    settings = Settings(parse_timeout_seconds=120, max_pages=7, max_upload_bytes=1000)
    built: list[ParserOptions] = []

    def factory(options: ParserOptions) -> FakeConverter:
        built.append(options)
        return FakeConverter()

    DoclingParser.from_settings(settings, converter_factory=factory).warm_up()
    assert built == [ParserOptions(timeout_seconds=120.0, max_pages=7, max_bytes=1000)]


# --- real Docling options (imports Docling and torch, no models): opt-in --------------------------


@pytest.mark.docling
def test_real_converter_pins_the_approved_options() -> None:
    from app.ingestion.parsing import DoclingConverter

    converter = DoclingConverter(OPTIONS)  # builds options only; models load in initialize()
    options = converter.pipeline_options
    assert options.do_ocr and options.do_table_structure
    assert options.ocr_options.backend == "torch" and options.ocr_options.lang == []
    assert not options.enable_remote_services and not options.allow_external_plugins
    assert options.document_timeout == 600
    assert options.generate_parsed_pages
    assert str(options.accelerator_options.device.value) == "cpu"


# --- eager RapidOCR model loading (Decision 2) ----------------------------------------------------


class FakeReader:
    def __init__(self, fail_on: str | None = None, error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self.fail_on, self.error = fail_on, error
        for name in RAPIDOCR_MODEL_LOADERS:
            setattr(self, name, self._loader(name))

    def _loader(self, name: str) -> Callable[[], None]:
        def load() -> None:
            self.calls.append(name)
            if name == self.fail_on and self.error is not None:
                raise self.error

        return load


def _pipeline(reader: Any) -> Any:
    return SimpleNamespace(ocr_model=SimpleNamespace(reader=reader))


def test_preload_calls_every_rapidocr_loader_once_in_order() -> None:
    reader = FakeReader()
    preload_ocr_models([_pipeline(reader)], VERIFIED_RAPIDOCR_VERSION)
    assert reader.calls == list(RAPIDOCR_MODEL_LOADERS)


def test_preload_refuses_an_unverified_rapidocr_version() -> None:
    reader = FakeReader()
    error = _parse_error(lambda: preload_ocr_models([_pipeline(reader)], "3.11.0"))
    assert error.kind == ParseErrorKind.PARSER_MISCONFIGURED
    assert reader.calls == []  # private loaders are never called on an unverified version


@pytest.mark.parametrize(
    "pipelines",
    [
        [],  # no initialized pipeline
        [SimpleNamespace(ocr_model=None)],  # OCR stage missing
        [_pipeline(None)],  # no reader
        [_pipeline(SimpleNamespace(_load_det_model=lambda: None))],  # loaders missing
    ],
)
def test_preload_refuses_an_unexpected_pipeline_shape(pipelines: list[Any]) -> None:
    error = _parse_error(lambda: preload_ocr_models(pipelines, VERIFIED_RAPIDOCR_VERSION))
    assert error.kind == ParseErrorKind.PARSER_MISCONFIGURED


class PreloadingConverter(FakeConverter):
    """A stand-in whose initialize() preloads OCR models like the real DoclingConverter."""

    def __init__(self, reader: FakeReader, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.reader = reader

    def initialize(self) -> None:
        super().initialize()
        preload_ocr_models([_pipeline(self.reader)], VERIFIED_RAPIDOCR_VERSION)


@pytest.mark.parametrize(
    "exc",
    [
        DownloadFileException(SECRET),
        requests.ConnectionError(SECRET),
        _instance(HfHubHTTPError, SECRET),
    ],
)
def test_ocr_model_download_failure_at_warm_up_is_models_unavailable(exc: Exception) -> None:
    reader = FakeReader(fail_on="_load_rec_model", error=exc)
    error = _parse_error(lambda: _parser(PreloadingConverter(reader)).warm_up())
    assert error.kind == ParseErrorKind.MODELS_UNAVAILABLE
    assert reader.calls == list(RAPIDOCR_MODEL_LOADERS)


def test_unknown_error_from_an_ocr_loader_propagates() -> None:
    reader = FakeReader(fail_on="_load_det_model", error=TypeError(SECRET))
    with pytest.raises(TypeError):
        _parser(PreloadingConverter(reader)).warm_up()


def test_models_are_loaded_at_warm_up_not_during_conversion() -> None:
    reader = FakeReader()
    converter = PreloadingConverter(reader)
    parser = _parser(converter)
    parser.warm_up()
    assert reader.calls == list(RAPIDOCR_MODEL_LOADERS)
    for _ in range(2):
        parser.parse(_validated(pdfs.lab_table_pdf()))
    assert reader.calls == list(RAPIDOCR_MODEL_LOADERS)  # no further loading
    assert converter.initialized == 1


def test_installed_rapidocr_is_the_verified_version() -> None:
    from importlib import metadata

    assert metadata.version("rapidocr") == VERIFIED_RAPIDOCR_VERSION


# --- audit hardening: PDFium pages are always closed (change 7) ----------------------------------


@pytest.mark.parametrize("failing", ["get_textpage", "count_chars"])
def test_pdfium_pages_are_closed_when_text_extraction_fails(
    monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    import pypdfium2

    closed: list[str] = []
    real_page_close = pypdfium2.PdfPage.close
    real_textpage_close = pypdfium2.PdfTextPage.close

    def page_close(self: Any) -> None:
        closed.append("page")
        real_page_close(self)

    def textpage_close(self: Any) -> None:
        closed.append("textpage")
        real_textpage_close(self)

    def fail(*args: object, **kwargs: object) -> None:
        raise pypdfium2.PdfiumError(SECRET)

    monkeypatch.setattr(pypdfium2.PdfPage, "close", page_close)
    monkeypatch.setattr(pypdfium2.PdfTextPage, "close", textpage_close)
    owner = pypdfium2.PdfPage if failing == "get_textpage" else pypdfium2.PdfTextPage
    monkeypatch.setattr(owner, failing, fail)
    error = _parse_error(lambda: _parser(FakeConverter()).parse(_validated(pdfs.lab_table_pdf())))
    assert error.kind == ParseErrorKind.PARSER_FAILED
    assert "page" in closed
    if failing == "count_chars":
        assert "textpage" in closed


# --- visibility-aware PDFium text signals (real PDFs, PDFium only) -------------------------------


def _stats(data: bytes) -> dict[int, TextLayerStats]:
    return _text_layer_stats(_validated(data))


def _non_whitespace(text: str) -> int:
    return sum(not c.isspace() for c in text)


_SCAN_LAYER = pdfs.PATIENT_LINE + "".join("  ".join(row) for row in pdfs.LAB_TABLE)
_ALL_RUNS = "".join(text for text, _, _ in pdfs.VISIBILITY_RUNS)


@pytest.mark.parametrize(
    ("builder", "expected"),
    [
        # fully visible: every non-whitespace character counted, watched characters kept
        (pdfs.lab_table_pdf, {1: TextLayerStats(359, (("–", 6), ("−", 2)), 0)}),
        (pdfs.scanned_pdf, {1: TextLayerStats(0, (), 0)}),  # image only
        (pdfs.blank_pdf, {1: TextLayerStats(0, (), 0)}),
        (
            pdfs.mixed_pdf,
            {1: TextLayerStats(359, (("–", 6), ("−", 2)), 0), 2: TextLayerStats(0, (), 0)},
        ),
        # fully invisible: an image with an invisible OCR layer, invisible text with no image,
        # and clip-only text with no image
        (pdfs.searchable_scan_pdf, {1: TextLayerStats(0, (), _non_whitespace(_SCAN_LAYER))}),
        (pdfs.invisible_text_pdf, {1: TextLayerStats(0, (), _non_whitespace(_ALL_RUNS))}),
        (pdfs.clip_only_text_pdf, {1: TextLayerStats(0, (), _non_whitespace(_ALL_RUNS))}),
    ],
)
def test_text_layer_counts_visible_and_invisible_text_separately(
    builder: Callable[[], bytes], expected: dict[int, TextLayerStats]
) -> None:
    assert _stats(builder()) == expected


def test_mixed_visibility_page_counts_visible_characters_only() -> None:
    # Visible, invisible (mode 3) and clip-only (mode 7) lines, and one line that is visible,
    # invisible, visible. Invisible runs hold 3 en dashes and 2 minus signs; one en dash is visible.
    stats = _stats(pdfs.mixed_visibility_pdf())
    assert stats == {1: text_layer_stats(pdfs.classified_text())}
    assert stats[1].visible_char_count == _non_whitespace(pdfs.visible_text())
    assert stats[1].watched_counts == (("–", 1),)
    assert stats[1].invisible_char_count == _non_whitespace(pdfs.invisible_text()) > 0
    hidden = pdfs.invisible_text()
    assert hidden.count("–") == 3 and hidden.count("−") == 2  # really present, not counted


@pytest.mark.parametrize(
    "runs",
    [
        (("Sodium 140 ", 0, True), ("hidden 135–145", 3, False), (" mmol/L", 0, False)),
        (("hidden 135–145 ", 3, True), ("Sodium 140", 0, False)),
        (("Potassium 4.1 (3.5–5.1)", 7, True), ("Sodium 140 (135–145)", 0, True)),
        (("TSH 2.5 µIU/mL", 0, True),),
        (("Potassium 4.1 (3.5–5.1)", 7, True),),  # clip only
    ],
)
def test_visibility_is_decided_per_character(runs: tuple[tuple[str, int, bool], ...]) -> None:
    stats = _stats(pdfs.mixed_visibility_pdf(runs))
    assert stats == {1: text_layer_stats(pdfs.classified_text(runs))}


def test_visible_and_invisible_watched_characters() -> None:
    visible = (("13.0–17.0 −1.2", 0, True),)
    invisible = (("13.0–17.0 −1.2", 3, True),)
    clip_only = (("13.0–17.0 −1.2", 7, True),)
    assert _stats(pdfs.mixed_visibility_pdf(visible))[1] == TextLayerStats(
        len("13.0–17.0−1.2"), (("–", 1), ("−", 1)), 0
    )
    for runs in (invisible, clip_only):
        assert _stats(pdfs.mixed_visibility_pdf(runs))[1] == TextLayerStats(
            0, (), len("13.0–17.0−1.2")
        )


@pytest.mark.parametrize(
    ("mode", "visible"),
    [
        (-1, True),
        (0, True),
        (1, True),
        (2, True),
        (4, True),
        (5, True),
        (6, True),
        (3, False),
        (7, False),
    ],
)
def test_pdfium_render_modes_other_than_3_and_7_are_visible(
    monkeypatch: pytest.MonkeyPatch, mode: int, visible: bool
) -> None:
    # PDFium returns -1 (FPDF_TEXTRENDERMODE_UNKNOWN) when it cannot tell: treated as visible.
    fake = SimpleNamespace(
        FPDFText_GetUnicode=lambda page, index: ord("–"),
        FPDFText_GetTextObject=lambda page, index: object(),
        FPDFTextObj_GetTextRenderMode=lambda obj: mode,
    )
    monkeypatch.setattr(parsing, "pdfium_c", fake)
    text_page = SimpleNamespace(count_chars=lambda: 1)
    assert list(parsing._classified_characters(text_page)) == [("–", visible)]  # type: ignore[arg-type]


def test_characters_without_a_text_object_are_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = SimpleNamespace(
        FPDFText_GetUnicode=lambda page, index: ord("x"),
        FPDFText_GetTextObject=lambda page, index: None,
        FPDFTextObj_GetTextRenderMode=lambda obj: pytest.fail("no object to ask"),
    )
    monkeypatch.setattr(parsing, "pdfium_c", fake)
    text_page = SimpleNamespace(count_chars=lambda: 2)
    assert list(parsing._classified_characters(text_page)) == [("x", True), ("x", True)]  # type: ignore[arg-type]


def test_searchable_scan_parses_as_ocr_without_false_flags() -> None:
    # Docling's view of a searchable scan: an invisible PDF line cell and OCR cells.
    parsed = SimpleNamespace(
        textline_cells=[
            SimpleNamespace(
                text="Haemoglobin 13.0-17.0",
                orig="Haemoglobin 13.0–17.0",
                from_ocr=False,
                confidence=1.0,
                rendering_mode=3,
            ),
            SimpleNamespace(
                text="Haemoglobin 13.0-17.0", orig=None, from_ocr=True, confidence=0.97
            ),
        ]
    )
    converter = FakeConverter(
        _result(
            texts={1: "Haemoglobin 13.0-17.0"},
            pages=[SimpleNamespace(page_no=1, parsed_page=parsed)],
        )
    )
    result = _parser(converter).parse(_validated(pdfs.searchable_scan_pdf()))
    page = result.document.pages[0]
    assert page.parse_method == ParseMethod.OCR and page.native_char_count == 0
    assert page.review_flags == (
        ReviewReason.INVISIBLE_TEXT_LAYER,
        ReviewReason.OCR_TEXT_UNVERIFIED,
    )


def test_mixed_visibility_page_stays_native() -> None:
    converter = FakeConverter(_result(texts={1: "Haemoglobin 13.5 g/dL (13.0–17.0)"}))
    result = _parser(converter).parse(_validated(pdfs.mixed_visibility_pdf()))
    page = result.document.pages[0]
    assert page.parse_method == ParseMethod.NATIVE
    assert page.native_char_count == text_layer_stats(pdfs.classified_text()).visible_char_count
    assert ReviewReason.DOCLING_NORMALIZED_TEXT not in page.review_flags  # the visible "–" kept
    assert page.review_flags == (ReviewReason.INVISIBLE_TEXT_LAYER,)


@pytest.mark.parametrize("builder", [pdfs.invisible_text_pdf, pdfs.clip_only_text_pdf])
def test_invisible_only_page_without_image_is_native_not_ocr(
    builder: Callable[[], bytes],
) -> None:
    # Docling's view of a page with only invisible text and no image: its PDF cells, no OCR cells.
    # Docling keeps the invisible text; it is passed through and flagged, and no OCR is claimed.
    mode = 3 if builder is pdfs.invisible_text_pdf else 7
    parsed = SimpleNamespace(
        textline_cells=[
            SimpleNamespace(
                text="Haemoglobin 13.5",
                orig=None,
                from_ocr=False,
                confidence=1.0,
                rendering_mode=mode,
            )
        ]
    )
    converter = FakeConverter(
        _result(
            texts={1: "Haemoglobin 13.5"},
            pages=[SimpleNamespace(page_no=1, parsed_page=parsed)],
        )
    )
    result = _parser(converter).parse(_validated(builder()))
    page = result.document.pages[0]
    assert page.parse_method == ParseMethod.NATIVE and page.ocr is None
    assert page.native_char_count == 0
    assert page.text == "Haemoglobin 13.5"
    assert page.review_flags == (ReviewReason.INVISIBLE_TEXT_LAYER,)
