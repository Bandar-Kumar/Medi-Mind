"""Parse a validated PDF with Docling into the MediMind IR (Task 2.4).

Usage (one parser per batch; the converter and its models are loaded once and reused)::

    parser = DoclingParser.from_settings(settings)
    parser.warm_up()                      # optional: load models before the first document
    result = parser.parse(validated_pdf)  # repeat for each document

Design:
- **Lazy imports.** Importing this module loads neither Docling nor torch (Docling's converter
  import alone takes ~11 s). They are imported when the first converter is built.
- **Pinned OCR.** RapidOCR with its PyTorch backend and its default model (``lang=[]``), set
  explicitly: ``RapidOcrOptions`` would otherwise default to onnxruntime, which is not installed.
  This is the configuration measured in the Task 2.1 spike.
- **No remote services**, CPU only, a per-document ``document_timeout`` from settings.
- **Input is never modified.** Docling reads an in-memory copy of the validated bytes, under the
  neutral name ``document.pdf`` (the real filename can contain a patient's name).
- **Errors** become ``DocumentParseError`` with a stable ``kind`` (see ``ParseErrorKind``). Only
  known failure types are classified, by class name, so no Docling import is needed to check them.
  Messages contain fixed text, class names and status codes only: never Docling's error messages,
  exception text or document content. Every other exception propagates unchanged (it is a bug or an
  unknown environment problem). Partial results are refused, never returned.
- **Mapping** to the IR is in ``docling_mapping.py`` (pure, unit-tested without models).

Docling's timeout is cooperative: it is checked between page batches, so a single very slow page
can overrun it. A hard limit would need a subprocess per document (on Windows that re-imports torch
each time and loses converter reuse); it is not used unless measurements show it is needed.
"""

import errno
import io
import json
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import metadata
from typing import Any, Protocol

import pypdfium2
import pypdfium2.raw as pdfium_c

from app.core.config import Settings
from app.core.errors import DocumentParseError
from app.ingestion.docling_mapping import (
    INVISIBLE_RENDER_MODES,
    OcrEngine,
    ReviewFlag,
    TextLayerStats,
    build_page_signals,
    map_document,
    review_flags_of,
    text_layer_stats,
)
from app.ingestion.ir import ParsedDocument, ParserInfo
from app.ingestion.storage import sha256_hex
from app.ingestion.validation import ValidatedPdf

DOCLING_STREAM_NAME = "document.pdf"
OCR_ENGINE = "rapidocr"
OCR_BACKEND = "torch"
DEFAULT_NUM_THREADS = 4

# Recorded in ParserInfo.options: what produced the IR text and what may differ from the PDF.
# The IR text is Docling's *extracted and normalized* text, not the raw PDF text: docling-parse
# maps some characters (measured: en dash and minus sign to "-"), and Docling then rewrites
# typographic characters and ligatures and joins hyphenated line breaks. No unnormalized copy is
# kept; the original PDF is the only authoritative source.
TEXT_PROVENANCE_OPTIONS: dict[str, str | bool] = {
    "text_source": "docling_extracted_normalized",
    "text_normalization": (
        "docling_parse_char_normalization(e.g. en_dash,minus_sign->hyphen);"
        "docling(typographic_map,ligatures,arabic_presentation_forms,line_join,dehyphenation,strip)"
    ),
    "raw_text_preserved": False,
    "normalization_check": "pdfium_visible_text_watched_characters+visible_line_cells",
    "authoritative_source": "original_pdf",
}

# RapidOCR loads its detection, classification and recognition models lazily, on the first OCR
# call. initialize() loads them eagerly through RapidOCR's own loaders, so any model download
# happens (and fails) during warm-up, never inside a conversion. The loaders are private RapidOCR
# methods, so they are used only with the version they were verified against.
VERIFIED_RAPIDOCR_VERSION = "3.10.0"
RAPIDOCR_MODEL_LOADERS = ("_load_det_model", "_load_cls_model", "_load_rec_model")


class ParseErrorKind(StrEnum):
    TIMEOUT = "timeout"
    PARTIAL_OUTPUT = "partial_output"
    PARSER_FAILED = "parser_failed"
    MODELS_UNAVAILABLE = "models_unavailable"
    PARSER_MISCONFIGURED = "parser_misconfigured"
    PARSER_ENVIRONMENT = "parser_environment"
    UNEXPECTED_OUTPUT = "unexpected_output"  # raised by docling_mapping


# Known failures, matched by fully qualified class name anywhere in the exception's class hierarchy
# (so subclasses such as huggingface_hub's RepositoryNotFoundError match HfHubHTTPError).
_MODEL_LOADING_ERRORS = frozenset(
    {
        "huggingface_hub.errors.LocalEntryNotFoundError",
        "huggingface_hub.errors.HfHubHTTPError",
        "huggingface_hub.errors.OfflineModeIsEnabled",
        "requests.exceptions.RequestException",
        "rapidocr.utils.download_file.DownloadFileException",
    }
)
_CONFIGURATION_ERRORS = frozenset(
    {
        "docling.exceptions.OcrLanguageNotSupportedError",
        "docling.exceptions.RapidOcrModelSizeNotSupportedError",
        "docling.exceptions.AcceleratorDeviceNotAvailableError",
    }
)
_CONVERSION_ERRORS = frozenset({"docling.exceptions.ConversionError"})


def _is_one_of(exc: BaseException, qualified_names: frozenset[str]) -> bool:
    return any(
        f"{cls.__module__}.{cls.__qualname__}" in qualified_names for cls in type(exc).__mro__
    )


def _error(kind: ParseErrorKind, message: str) -> DocumentParseError:
    return DocumentParseError(message, kind=kind)


@dataclass(frozen=True)
class ParserOptions:
    timeout_seconds: float
    max_pages: int
    max_bytes: int
    num_threads: int = DEFAULT_NUM_THREADS


@dataclass(frozen=True)
class ParseResult:
    document: ParsedDocument
    docling_json: bytes = field(repr=False)  # Docling's full output (all items, tables, bboxes)
    duration_ms: int

    @property
    def review_flags(self) -> tuple[ReviewFlag, ...]:
        """The review flags stored in the IR (``document``), as (page_number, reason)."""
        return review_flags_of(self.document)


class Converter(Protocol):
    """What the parser needs from a converter (Docling's, or a stand-in in unit tests)."""

    def initialize(self) -> None: ...

    def convert(self, data: bytes, *, max_pages: int, max_bytes: int) -> Any: ...


ConverterFactory = Callable[[ParserOptions], Converter]


class DoclingParser:
    """Parses PDFs one at a time with a single, reused converter. Not thread-safe."""

    def __init__(
        self, options: ParserOptions, *, converter_factory: ConverterFactory | None = None
    ) -> None:
        self._options = options
        self._factory: ConverterFactory = converter_factory or DoclingConverter
        self._converter: Converter | None = None
        self._parser_info = _parser_info(options)
        self._ocr_engine = OcrEngine(
            engine=OCR_ENGINE, engine_version=_version("rapidocr"), backend=OCR_BACKEND
        )

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs: Any) -> "DoclingParser":
        options = ParserOptions(
            timeout_seconds=float(settings.parse_timeout_seconds),
            max_pages=settings.max_pages,
            max_bytes=settings.max_upload_bytes,
        )
        return cls(options, **kwargs)

    @property
    def parser_info(self) -> ParserInfo:
        return self._parser_info

    def warm_up(self) -> None:
        """Build the converter and load (on first use: download) the models now."""
        self._ensure_ready()

    def parse(self, pdf: ValidatedPdf) -> ParseResult:
        """Parse one validated PDF.

        Raises:
            DocumentParseError: with a ``ParseErrorKind`` value as ``kind``.
        """
        converter = self._ensure_ready()
        started = time.perf_counter()
        text_layers = _text_layer_stats(pdf)
        result = self._convert(converter, pdf.data)
        _check_status(result, self._options.timeout_seconds)
        if getattr(result, "document", None) is None:
            raise _error(ParseErrorKind.UNEXPECTED_OUTPUT, "Docling returned no document")
        signals = build_page_signals(result.pages, text_layers, pdf.page_count)
        mapped = map_document(
            result.document,
            signals,
            page_count=pdf.page_count,
            sha256=sha256_hex(pdf.data),
            parser=self._parser_info,
            ocr_engine=self._ocr_engine,
        )
        docling_json = json.dumps(result.document.export_to_dict(), ensure_ascii=False)
        return ParseResult(
            document=mapped.document,
            docling_json=docling_json.encode("utf-8"),
            duration_ms=round((time.perf_counter() - started) * 1000),
        )

    def _ensure_ready(self) -> Converter:
        if self._converter is not None:
            return self._converter
        failure: DocumentParseError | None = None
        try:
            converter = self._factory(self._options)
            converter.initialize()  # the only step that loads or downloads models
        except Exception as exc:
            failure = _classify_initialization_error(exc)
            if failure is None:
                raise  # unknown: propagate unchanged
        if failure is not None:
            raise failure
        self._converter = converter
        return converter

    def _convert(self, converter: Converter, data: bytes) -> Any:
        failure: DocumentParseError | None = None
        try:
            return converter.convert(
                data, max_pages=self._options.max_pages, max_bytes=self._options.max_bytes
            )
        except Exception as exc:
            if not _is_one_of(exc, _CONVERSION_ERRORS):
                raise  # unknown: propagate unchanged
            failure = _error(
                ParseErrorKind.PARSER_FAILED,
                f"Docling could not convert the document ({type(exc).__name__})",
            )
        raise failure


def _classify_initialization_error(exc: Exception) -> DocumentParseError | None:
    name = type(exc).__name__
    if _is_one_of(exc, _MODEL_LOADING_ERRORS):
        return _error(
            ParseErrorKind.MODELS_UNAVAILABLE,
            f"Docling models could not be loaded or downloaded ({name}); "
            "run once with network access to download them",
        )
    if _is_one_of(exc, _CONFIGURATION_ERRORS):
        return _error(ParseErrorKind.PARSER_MISCONFIGURED, f"Invalid parser configuration ({name})")
    if isinstance(exc, PermissionError) or (isinstance(exc, OSError) and exc.errno == errno.ENOSPC):
        code = f", errno {exc.errno}" if exc.errno is not None else ""
        return _error(
            ParseErrorKind.PARSER_ENVIRONMENT,
            f"The model cache is not writable or the disk is full ({name}{code})",
        )
    return None


def _value(member: Any) -> str:
    return str(getattr(member, "value", member))


def _check_status(result: Any, timeout_seconds: float) -> None:
    """Refuse anything but a clean success. Reads only status and error categories/components."""
    status = _value(result.status)
    if status == "success":
        return
    errors = list(getattr(result, "errors", None) or [])
    categories = sorted({_value(e.category) for e in errors if getattr(e, "category", None)})
    components = sorted(
        {_value(e.component_type) for e in errors if getattr(e, "component_type", None)}
    )
    if status == "partial_success" and "timeout" in categories:
        raise _error(
            ParseErrorKind.TIMEOUT, f"Parsing exceeded the {timeout_seconds:g}-second limit"
        )
    if status == "partial_success":
        raise _error(
            ParseErrorKind.PARTIAL_OUTPUT,
            "Docling converted only part of the document "
            f"(categories: {', '.join(categories) or 'none'})",
        )
    raise _error(
        ParseErrorKind.PARSER_FAILED,
        f"Docling could not convert the document (status={status}; "
        f"components: {', '.join(components) or 'none'})",
    )


def _text_layer_stats(pdf: ValidatedPdf) -> dict[int, TextLayerStats]:
    """Counts from each page's text layer, read with PDFium. pypdfium2 indexes from 0.

    Used only for detection (visible native character count, watched characters, invisible
    character count). Characters are reduced to counts while they are read: no page text is kept
    or copied into the IR.
    """
    failed = False
    layers: dict[int, TextLayerStats] = {}
    try:
        document = pypdfium2.PdfDocument(pdf.data)
        try:
            for index in range(len(document)):
                page = document[index]
                try:
                    text_page = page.get_textpage()
                    try:
                        stats = text_layer_stats(_classified_characters(text_page))
                    finally:
                        text_page.close()
                finally:
                    page.close()  # also when get_textpage() or reading the characters raises
                layers[index + 1] = stats
        finally:
            document.close()
    except pypdfium2.PdfiumError:
        failed = True
    if failed:
        raise _error(ParseErrorKind.PARSER_FAILED, "Could not read the PDF's text layer")
    if len(layers) != pdf.page_count:
        raise _error(ParseErrorKind.UNEXPECTED_OUTPUT, "Page count differs from validation")
    return layers


def _classified_characters(text_page: pypdfium2.PdfTextPage) -> Iterator[tuple[str, bool]]:
    """Yield each character of the page with whether it is visibly drawn: (character, visible).

    Each character index of PDFium's text page resolves to the text object that drew it
    (``FPDFText_GetTextObject``); that object's rendering mode decides visibility, so visible and
    invisible text on the same line are told apart character by character. Only modes 3 and 7 are
    invisible; any other value, including PDFium's "unknown" (-1), counts as visible. Characters
    PDFium generates itself (spaces, line breaks between objects) have no text object and are
    reported visible: they are whitespace, so they never count. A non-generated character whose
    object cannot be resolved is treated as visible (errs towards counting). A UTF-16 low
    surrogate is the second half of one character and is skipped, so a character outside the BMP
    counts once.
    """
    for index in range(text_page.count_chars()):  # raises PdfiumError on failure
        code = pdfium_c.FPDFText_GetUnicode(text_page, index)
        if 0xDC00 <= code <= 0xDFFF:
            continue
        text_object = pdfium_c.FPDFText_GetTextObject(text_page, index)
        visible = not (
            text_object
            and pdfium_c.FPDFTextObj_GetTextRenderMode(text_object) in INVISIBLE_RENDER_MODES
        )
        yield (chr(code) if code <= 0x10FFFF else "\ufffd"), visible


def preload_ocr_models(pipelines: Iterable[Any], rapidocr_version: str) -> None:
    """Load RapidOCR's detection, classification and recognition models now (see module notes).

    Raises:
        DocumentParseError: ``parser_misconfigured`` if the RapidOCR version is not the verified
            one, or the pipeline has no RapidOCR reader with the expected loaders.
        Model download/loading errors from RapidOCR propagate to the caller, which classifies
        them (``models_unavailable``) because this runs during initialization.
    """
    if rapidocr_version != VERIFIED_RAPIDOCR_VERSION:
        raise _error(
            ParseErrorKind.PARSER_MISCONFIGURED,
            f"RapidOCR {rapidocr_version} is installed; eager model loading is verified only for "
            f"{VERIFIED_RAPIDOCR_VERSION}",
        )
    readers = [getattr(getattr(p, "ocr_model", None), "reader", None) for p in pipelines]
    readers = [reader for reader in readers if reader is not None]
    loaders = [
        [getattr(reader, name, None) for name in RAPIDOCR_MODEL_LOADERS] for reader in readers
    ]
    if not loaders or not all(callable(loader) for group in loaders for loader in group):
        raise _error(
            ParseErrorKind.PARSER_MISCONFIGURED,
            "The Docling pipeline has no RapidOCR reader with the expected model loaders",
        )
    for group in loaders:
        for loader in group:
            loader()


def _version(distribution: str) -> str:
    return metadata.version(distribution)


def _parser_info(options: ParserOptions) -> ParserInfo:
    return ParserInfo(
        name="docling",
        version=_version("docling"),
        options={
            "docling_core_version": _version("docling-core"),
            "do_ocr": True,
            "do_table_structure": True,
            "table_mode": "accurate",
            "ocr_engine": OCR_ENGINE,
            "ocr_backend": OCR_BACKEND,
            "ocr_lang": "engine-default",
            "ocr_mode": "default",
            "document_timeout_s": f"{options.timeout_seconds:g}",
            "generate_parsed_pages": True,
            **TEXT_PROVENANCE_OPTIONS,
        },
    )


class DoclingConverter:
    """The real converter: Docling's ``DocumentConverter`` with MediMind's pinned options."""

    def __init__(self, options: ParserOptions) -> None:
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        self.pipeline_options = PdfPipelineOptions(
            do_ocr=True,
            ocr_options=RapidOcrOptions(backend=OCR_BACKEND, lang=[]),
            do_table_structure=True,
            enable_remote_services=False,
            allow_external_plugins=False,
            document_timeout=options.timeout_seconds,
            generate_parsed_pages=True,  # keep per-page cells: OCR flags and confidences
            generate_page_images=False,
            generate_picture_images=False,
            accelerator_options=AcceleratorOptions(
                device=AcceleratorDevice.CPU, num_threads=options.num_threads
            ),
        )
        self._input_format = InputFormat.PDF
        self._converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=self.pipeline_options)
            }
        )

    def initialize(self) -> None:
        self._converter.initialize_pipeline(self._input_format)  # Docling's layout/table models
        preload_ocr_models(self._converter.initialized_pipelines.values(), _version("rapidocr"))

    def convert(self, data: bytes, *, max_pages: int, max_bytes: int) -> Any:
        from docling.datamodel.base_models import DocumentStream

        stream = DocumentStream(name=DOCLING_STREAM_NAME, stream=io.BytesIO(data))
        return self._converter.convert(
            stream, raises_on_error=False, max_num_pages=max_pages, max_file_size=max_bytes
        )
