"""Tests for the local Streamlit UI (Task 2.9b) with Streamlit's ``AppTest``.

Every service the UI calls is replaced by a stand-in over in-memory fictional data: no database,
parser or model is used. The UI must only call services, show fixed reasons (never exception
messages), keep demo mode synthetic-only, and show identity and type warnings as specified.
"""

import ast
import datetime as dt
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from app.core.errors import DocumentProcessingError, DocumentValidationError, PatientError
from app.records.repositories import DeletedDocument, DocumentSummary, PatientSummary
from app.retrieval.evidence import AnswerStatus, Evidence, EvidenceAnswer, RetrievalMode
from app.services import document_pipeline as pipe
from app.services import library, ui_runtime, upload_document
from app.services.check_identity import IdentityCheckResult
from app.services.classify_document import ClassifyResult
from app.services.library import DocumentState
from app.ui import texts

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = str(ROOT / "app" / "ui" / "streamlit_app.py")
SECRET = "SECRET-MESSAGE-Asha"  # an exception message: must never reach the page
NOW = dt.datetime(2026, 11, 17, 10, 0, tzinfo=dt.UTC)


class Backend:
    """In-memory stand-ins for every service the UI calls; records each call."""

    def __init__(self) -> None:
        self.real = PatientSummary(uuid.uuid4(), "Ravi Realperson", None, False, NOW)
        self.synthetic = PatientSummary(
            uuid.uuid4(), "Asha Fictional", dt.date(1985, 3, 25), True, NOW
        )
        self.patients = [self.synthetic, self.real]
        self.documents: dict[uuid.UUID, list[DocumentSummary]] = {}
        self.states: dict[uuid.UUID, DocumentState] = {}
        self.calls: list[tuple] = []
        self.fail: dict[str, BaseException] = {}
        self.pipeline_result: pipe.DocumentPipelineResult | None = None
        self.answer: EvidenceAnswer | None = None

    def add_document(self, patient, *, identity="match", doc_type="lab_report", **state):
        doc_id = uuid.uuid4()
        status = state.pop("status", "processing")
        summary = DocumentSummary(
            doc_id, patient.id, f"report-{len(self.states)}.pdf", 2, status,
            doc_type, None, None, NOW, NOW, Decimal("0.875"), identity,
        )  # fmt: skip
        self.documents.setdefault(patient.id, []).append(summary)
        self.states[doc_id] = DocumentState(
            doc_id, summary.processing_status, state.get("pages", 2), state.get("chunks", 3),
            state.get("classified", True), state.get("checked", True), identity,
            state.get("blocked"),
        )  # fmt: skip
        return summary

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail:
            raise self.fail[name]

    # library
    def list_patients(self, *, synthetic_only=False):
        self.calls.append(("list_patients", synthetic_only))
        self._maybe_fail("list_patients")
        return tuple(p for p in self.patients if p.is_synthetic or not synthetic_only)

    def get_patient(self, patient_id, *, synthetic_only=False):
        self.calls.append(("get_patient", patient_id, synthetic_only))
        for p in self.patients:
            if p.id == patient_id and (p.is_synthetic or not synthetic_only):
                return p
        raise PatientError(SECRET, kind="patient_not_found")

    def create_patient(self, display_name, *, is_synthetic, date_of_birth=None):
        self.calls.append(("create_patient", display_name, is_synthetic, date_of_birth))
        self._maybe_fail("create_patient")
        patient = PatientSummary(uuid.uuid4(), display_name, date_of_birth, is_synthetic, NOW)
        self.patients.append(patient)
        return SimpleNamespace(id=patient.id, is_synthetic=is_synthetic)

    def update_patient(self, patient_id, **changes):
        self.calls.append(("update_patient", patient_id, changes))
        return SimpleNamespace(id=patient_id)

    def delete_patient(self, patient_id):
        self.calls.append(("delete_patient", patient_id))
        self._maybe_fail("delete_patient")

    def list_documents(self, patient_id):
        self.calls.append(("list_documents", patient_id))
        return tuple(self.documents.get(patient_id, ()))

    def get_document_state(self, patient_id, document_id):
        self.calls.append(("get_document_state", patient_id, document_id))
        return self.states[document_id]

    def rename_document(self, patient_id, document_id, name):
        self.calls.append(("rename_document", patient_id, document_id, name))
        return self.documents[patient_id][0]

    def delete_document(self, patient_id, document_id):
        self.calls.append(("delete_document", patient_id, document_id))
        return DeletedDocument(document_id, 2, 5)

    # upload, pipeline, search
    def upload(self, settings, patient_id, data, *, original_filename):
        self.calls.append(("upload", patient_id, original_filename, len(data)))
        self._maybe_fail("upload")
        doc = self.add_document(self.synthetic, status="uploaded", pages=0, chunks=0)
        return SimpleNamespace(document=SimpleNamespace(id=doc.id), duplicate=False)

    def run_document_pipeline(
        self, settings, parser, embedder, patient_id, document_id, *, on_stage
    ):
        self.calls.append(("run_document_pipeline", patient_id, document_id))
        result = self.pipeline_result or _result(document_id)
        for stage in result.stages:
            on_stage(document_id, stage)
        return result

    def run_patient_pipeline(self, settings, parser, embedder, patient_id, *, on_stage):
        self.calls.append(("run_patient_pipeline", patient_id))
        return tuple(_result(d.id) for d in self.documents.get(patient_id, ()))

    def search(self, settings, patient_id, question, mode, *, embedder, reranker):
        self.calls.append(("search", patient_id, question, mode))
        self._maybe_fail("search")
        return self.answer


def _result(document_id, identity="match", index=("succeeded", None)):
    classify = ClassifyResult(document_id, uuid.uuid4(), "lab_report", 0.875, ("x",), "2", 1)
    check = IdentityCheckResult(
        document_id, uuid.uuid4(), identity, ("name_match",), "2", "processing", 1
    )
    return pipe.DocumentPipelineResult(
        document_id,
        (
            pipe.StageResult("parse", "succeeded"),
            pipe.StageResult("classify", "succeeded", detail=classify),
            pipe.StageResult("identity", "succeeded", detail=check),
            pipe.StageResult("index", index[0], reason=index[1]),
        ),
    )


@pytest.fixture
def backend(monkeypatch):
    def install(*, demo_mode=False) -> Backend:
        b = Backend()
        settings = SimpleNamespace(
            demo_mode=demo_mode, embedding_model_revision=None, reranker_model_revision=None
        )
        monkeypatch.setattr(ui_runtime, "load_settings", lambda: settings)
        monkeypatch.setattr(ui_runtime, "setup_logging", lambda s: None)
        monkeypatch.setattr(ui_runtime, "make_parser", lambda s: object())
        monkeypatch.setattr(ui_runtime, "make_embedder", lambda s: object())
        monkeypatch.setattr(ui_runtime, "make_reranker", lambda s: None)
        monkeypatch.setattr(ui_runtime, "search", b.search)
        for name in (
            "list_patients", "get_patient", "create_patient", "update_patient", "delete_patient",
            "list_documents", "get_document_state", "rename_document", "delete_document",
        ):  # fmt: skip
            monkeypatch.setattr(library, name, getattr(b, name))
        monkeypatch.setattr(upload_document, "upload_document", b.upload)
        monkeypatch.setattr(pipe, "run_document_pipeline", b.run_document_pipeline)
        monkeypatch.setattr(pipe, "run_patient_pipeline", b.run_patient_pipeline)
        st.cache_resource.clear()
        return b

    yield install
    st.cache_resource.clear()


def _app(page: str | None = None) -> AppTest:
    at = AppTest.from_file(SCRIPT, default_timeout=30)
    at.run()
    if page is not None:
        at.sidebar.radio[0].set_value(page).run()
    return at


def _button(at: AppTest, label: str):
    (button,) = [b for b in at.button if b.label == label]
    return button


def _texts(at: AppTest) -> str:
    parts = []
    for kind in ("error", "warning", "info", "success", "caption", "text", "markdown"):
        parts += [str(e.value) for e in getattr(at, kind)]
    return "\n".join(parts)


def _cells(at: AppTest) -> str:
    """Every cell of the first table on the page (pandas' str() truncates columns)."""
    return repr(at.dataframe[0].value.to_dict("records"))


def _no_secret(at: AppTest) -> None:
    assert SECRET not in _texts(at)
    assert not at.exception


# --- architecture --------------------------------------------------------------------------------


def test_the_ui_imports_only_services_streamlit_and_the_standard_library() -> None:
    allowed_app = ("app.services", "app.ui")
    for path in (ROOT / "app" / "ui").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith("app"):
                    assert name.startswith(allowed_app), f"{path.name}: {name}"
                else:
                    top = name.split(".")[0]
                    assert top == "streamlit" or top in __import__("sys").stdlib_module_names, (
                        f"{path.name}: {name}"
                    )


def test_streamlit_is_local_only_and_hides_error_details() -> None:
    import tomllib

    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["server"]["address"] == "127.0.0.1"
    assert config["browser"]["gatherUsageStats"] is False
    assert config["client"]["showErrorDetails"] == "none"
    assert config["server"]["maxUploadSize"] == 20  # MB: the default MEDIMIND_MAX_UPLOAD_BYTES


# --- patients ------------------------------------------------------------------------------------


def test_patients_are_listed_and_the_first_is_selected(backend) -> None:
    b = backend()
    at = _app()
    assert at.sidebar.selectbox[0].value == str(b.synthetic.id)
    assert ("list_patients", False) in b.calls
    assert "Asha Fictional" in _cells(at)
    _no_secret(at)


def test_creating_a_patient_selects_it(backend) -> None:
    b = backend()
    at = _app()
    at.text_input[0].input("Kabir Testwala")
    _button(at, "Create patient").click().run()
    created = [c for c in b.calls if c[0] == "create_patient"]
    assert created == [("create_patient", "Kabir Testwala", False, None)]
    assert at.sidebar.selectbox[0].value == str(b.patients[-1].id)
    _no_secret(at)


def test_a_refused_create_shows_the_fixed_reason_not_the_message(backend) -> None:
    b = backend()
    b.fail["create_patient"] = PatientError(SECRET, kind="invalid_display_name")
    at = _app()
    at.text_input[0].input("   ")
    _button(at, "Create patient").click().run()
    assert texts.REASONS["invalid_display_name"] in [e.value for e in at.error]
    _no_secret(at)


def test_a_patient_with_documents_cannot_be_deleted_and_says_why(backend) -> None:
    b = backend()
    b.fail["delete_patient"] = PatientError(SECRET, kind="patient_not_empty")
    at = _app()
    assert _button(at, "Delete patient").disabled  # needs the confirmation first
    at.checkbox(key="del_pat").check().run()
    _button(at, "Delete patient").click().run()
    assert ("delete_patient", b.synthetic.id) in b.calls
    assert texts.REASONS["patient_not_empty"] in [e.value for e in at.error]
    _no_secret(at)


def test_a_database_failure_is_shown_as_a_fixed_reason(backend) -> None:
    from sqlalchemy.exc import OperationalError

    b = backend()
    b.fail["list_patients"] = OperationalError("SELECT", {}, Exception(SECRET))
    at = _app()
    assert texts.REASONS["database_error"] in [e.value for e in at.error]
    _no_secret(at)


# --- demo mode -----------------------------------------------------------------------------------


def test_demo_mode_shows_synthetic_patients_only_and_a_banner(backend) -> None:
    b = backend(demo_mode=True)
    at = _app()
    assert texts.DEMO_BANNER in [e.value for e in at.info]
    assert at.sidebar.selectbox[0].options == ["Asha Fictional"]  # displayed labels
    assert at.sidebar.selectbox[0].value == str(b.synthetic.id)
    assert ("list_patients", True) in b.calls and ("list_patients", False) not in b.calls
    assert all(c[2] is True for c in b.calls if c[0] == "get_patient")
    assert "Ravi Realperson" not in _cells(at)


def test_demo_mode_creates_synthetic_patients_only(backend) -> None:
    b = backend(demo_mode=True)
    at = _app()
    assert not [c for c in at.checkbox if c.label.startswith("Synthetic")]  # no choice offered
    at.text_input[0].input("Demo Person")
    _button(at, "Create patient").click().run()
    assert [c[2] for c in b.calls if c[0] == "create_patient"] == [True]


def test_demo_mode_disables_upload(backend) -> None:
    b = backend(demo_mode=True)
    at = _app("Documents")
    assert texts.DEMO_UPLOAD_DISABLED in [e.value for e in at.info]
    assert not [b for b in at.button if b.label == "Upload"]
    assert not [c for c in b.calls if c[0] == "upload"]


def test_normal_mode_creates_nothing_automatically(backend) -> None:
    b = backend()
    _app("Documents")
    assert not [c for c in b.calls if c[0] in ("create_patient", "upload")]


# --- documents -----------------------------------------------------------------------------------


def test_documents_are_listed_for_the_selected_patient_only(backend) -> None:
    b = backend()
    b.add_document(b.synthetic)
    b.add_document(b.real)
    at = _app("Documents")
    listed = [c[1] for c in b.calls if c[0] in ("list_documents", "get_document_state")]
    assert listed and set(listed) == {b.synthetic.id}
    table = _cells(at)
    assert "Matched" in table and "rule score 0.875" in table
    assert "Parsed and indexed, awaiting extraction" in table
    _no_secret(at)


def test_upload_goes_through_the_service_and_selects_the_new_document(backend) -> None:
    b = backend()
    b.add_document(b.synthetic)

    def script():
        import uuid
        from types import SimpleNamespace

        from app.ui import views

        views.handle_upload(
            SimpleNamespace(demo_mode=False), uuid.UUID(int=7), b"%PDF-1.4", "a.pdf"
        )

    at = AppTest.from_function(script)
    at.run()
    assert ("upload", uuid.UUID(int=7), "a.pdf", 8) in b.calls
    new_id = str(b.documents[b.synthetic.id][-1].id)
    assert at.session_state["_next_document_id"] == new_id
    assert any("Uploaded" in e.value for e in at.success)


def test_a_rejected_upload_shows_the_validation_reason(backend) -> None:
    b = backend()
    b.fail["upload"] = DocumentValidationError(SECRET, reason="encrypted")

    def script():
        import uuid
        from types import SimpleNamespace

        from app.ui import views

        views.handle_upload(SimpleNamespace(demo_mode=False), uuid.UUID(int=7), b"x", "a.pdf")

    at = AppTest.from_function(script)
    at.run()
    assert [e.value for e in at.error] == [texts.REASONS["encrypted"]]
    _no_secret(at)


def test_handle_upload_refuses_in_demo_mode(backend) -> None:
    b = backend()

    def script():
        import uuid
        from types import SimpleNamespace

        from app.ui import views

        views.handle_upload(SimpleNamespace(demo_mode=True), uuid.UUID(int=7), b"x", "a.pdf")

    AppTest.from_function(script).run()
    assert not [c for c in b.calls if c[0] == "upload"]


def test_identity_mismatch_is_shown_prominently(backend) -> None:
    b = backend()
    b.add_document(b.synthetic, identity="mismatch", status="needs_review", chunks=0,
                   blocked="identity_mismatch")  # fmt: skip
    at = _app("Documents")
    assert texts.IDENTITY_TEXTS["mismatch"][1] in [e.value for e in at.error]
    assert "MISMATCH" in _cells(at)
    assert "Needs review" in _cells(at)
    assert not [b for b in at.button if "override" in b.label.lower() or "keep" in b.label.lower()]


def test_identity_unknown_and_type_unknown_are_explained(backend) -> None:
    b = backend()
    b.add_document(b.synthetic, identity="unknown", doc_type="unknown")
    at = _app("Documents")
    shown = [e.value for e in at.warning] + [e.value for e in at.info]
    assert texts.IDENTITY_TEXTS["unknown"][1] in shown
    assert texts.TYPE_UNKNOWN in shown
    assert "Identity not confirmed" in _cells(at)


def test_a_match_is_not_presented_as_proof_of_identity(backend) -> None:
    b = backend()
    b.add_document(b.synthetic)
    at = _app("Documents")
    (message,) = [e.value for e in at.success]
    assert "not proof of identity" in message


def test_an_unfinished_parse_is_explained_without_recovery(backend) -> None:
    b = backend()
    b.add_document(b.synthetic, status="processing", pages=0, chunks=0,
                   classified=False, checked=False)  # fmt: skip
    at = _app("Documents")
    assert texts.REASONS["already_running"] in [e.value for e in at.warning]
    assert "Parse not finished" in _cells(at)
    assert not [b for b in at.button if "recover" in b.label.lower() or "retry" in b.label.lower()]


def test_deleting_a_document_needs_confirmation_and_explains_files_stay(backend) -> None:
    b = backend()
    doc = b.add_document(b.synthetic)
    at = _app("Documents")
    assert texts.DELETE_DOCUMENT_WARNING in [e.value for e in at.warning]
    assert _button(at, "Delete document").disabled
    at.checkbox(key="del_doc").check().run()
    _button(at, "Delete document").click().run()
    assert ("delete_document", b.synthetic.id, doc.id) in b.calls
    assert any("Files on disk were not deleted" in e.value for e in at.success)


def test_rename_goes_through_the_service_with_the_patient_scope(backend) -> None:
    b = backend()
    doc = b.add_document(b.synthetic)
    at = _app("Documents")
    (rename,) = [t for t in at.text_input if t.label.startswith("Rename")]
    rename.input("new-name.pdf")
    _button(at, "Rename").click().run()
    assert ("rename_document", b.synthetic.id, doc.id, "new-name.pdf") in b.calls


# --- pipeline ------------------------------------------------------------------------------------


def test_processing_one_document_shows_every_stage_with_its_status(backend) -> None:
    b = backend()
    doc = b.add_document(b.synthetic, status="uploaded", pages=0, chunks=0,
                         classified=False, checked=False)  # fmt: skip
    b.pipeline_result = _result(doc.id, identity="mismatch", index=("blocked", "identity_mismatch"))
    at = _app("Documents")
    _button(at, "Process this document").click().run()
    assert ("run_document_pipeline", b.synthetic.id, doc.id) in b.calls
    rows = at.table[0].value.to_dict("records")
    assert [(r["Stage"], r["Result"]) for r in rows] == [
        ("Parse", "✅ Succeeded"),
        ("Classify", "✅ Succeeded"),
        ("Identity check", "✅ Succeeded"),
        ("Index for search", "⛔ Blocked"),
    ]
    assert rows[3]["Explanation"] == texts.REASONS["identity_mismatch"]
    assert texts.IDENTITY_TEXTS["mismatch"][1] in [e.value for e in at.error]
    _no_secret(at)


def test_processing_a_patient_runs_the_patient_pipeline(backend) -> None:
    b = backend()
    b.add_document(b.synthetic)
    b.add_document(b.synthetic)
    at = _app("Documents")
    _button(at, "Process all documents of this patient").click().run()
    assert ("run_patient_pipeline", b.synthetic.id) in b.calls
    assert len(at.table) == 2  # one stage table per document


@pytest.mark.parametrize(
    ("status", "badge"),
    [("succeeded", "✅"), ("already_done", "☑️"), ("blocked", "⛔"), ("failed", "❌")],
)
def test_every_stage_status_has_a_distinct_badge(status, badge) -> None:
    assert texts.status_badge(status).startswith(badge)


def test_every_pipeline_reason_kind_has_a_fixed_explanation() -> None:
    from app.records.chunks import GATE_REFUSALS

    kinds = {
        "already_parsed", "already_classified", "already_checked", "already_indexed",
        "not_parsed", "already_running", "database_error", "unexpected",
    } | GATE_REFUSALS  # fmt: skip
    assert kinds <= texts.REASONS.keys()
    assert texts.reason_text("some_new_kind") == "Reason code: some_new_kind"


# --- evidence search -----------------------------------------------------------------------------


def _evidence(document_id, identity="match"):
    return Evidence(
        document_id, 1, uuid.uuid4(), "text", None, "Haemoglobin 13.1 gm/dL", 0.91,
        "report-0.pdf", None, "native", identity,
    )  # fmt: skip


def test_search_shows_cited_excerpts_with_the_identity_label(backend) -> None:
    b = backend()
    doc = b.add_document(b.synthetic, identity="unknown")
    b.answer = EvidenceAnswer(
        AnswerStatus.ANSWERED, (_evidence(doc.id, "unknown"),), 0.91, RetrievalMode.LEXICAL, False
    )
    at = _app("Evidence search")
    at.text_input[0].input("haemoglobin")
    _button(at, "Search").click().run()
    assert ("search", b.synthetic.id, "haemoglobin", "lexical") in b.calls  # the only mode
    shown = [e.value for e in at.text]
    assert "[report-0.pdf, p. 1]" in shown and "Haemoglobin 13.1 gm/dL" in shown
    assert "identity not confirmed" in [e.value for e in at.warning]
    assert "pipeline_results" not in at.session_state or not at.session_state["pipeline_results"]


def test_search_shows_an_abstention_and_nothing_else(backend) -> None:
    b = backend()
    b.answer = EvidenceAnswer(
        AnswerStatus.INSUFFICIENT_EVIDENCE, (), None, RetrievalMode.LEXICAL, False
    )
    at = _app("Evidence search")
    at.text_input[0].input("anything")
    _button(at, "Search").click().run()
    assert [e.value for e in at.warning] == [b.answer.message]
    assert not at.text


def test_a_search_failure_shows_its_fixed_reason(backend) -> None:
    from app.core.errors import RetrievalError

    b = backend()
    b.fail["search"] = RetrievalError(SECRET, kind="revision_not_configured")
    at = _app("Evidence search")
    at.text_input[0].input("anything")
    _button(at, "Search").click().run()
    assert [e.value for e in at.error] == [texts.REASONS["revision_not_configured"]]
    _no_secret(at)


def test_without_a_patient_the_document_and_search_pages_ask_for_one(backend) -> None:
    b = backend()
    b.patients = []
    at = _app("Documents")
    assert "Select or create a patient" in " ".join(e.value for e in at.info)
    assert not [c for c in b.calls if c[0] in ("list_documents", "search")]


# --- service additions (no Streamlit) ------------------------------------------------------------


def test_library_filters_real_patients_out_in_demo_mode(monkeypatch) -> None:
    real = PatientSummary(uuid.uuid4(), "Real", None, False, NOW)
    fake = PatientSummary(uuid.uuid4(), "Fake", None, True, NOW)
    monkeypatch.setattr(library, "_list_patients", lambda: (real, fake))
    monkeypatch.setattr(library, "_get_patient", lambda pid: real if pid == real.id else fake)
    assert library.list_patients(synthetic_only=True) == (fake,)
    assert library.list_patients() == (real, fake)
    with pytest.raises(PatientError) as caught:
        library.get_patient(real.id, synthetic_only=True)
    assert caught.value.kind == "patient_not_found"
    assert library.get_patient(real.id) is real


def test_document_state_comes_from_the_gate(monkeypatch) -> None:
    pid, did = uuid.uuid4(), uuid.uuid4()
    summary = SimpleNamespace(processing_status="processing")
    target = SimpleNamespace(
        page_rows=0, chunk_count=0, classified=False, identity_checked=False,
        identity_check="unknown", blocked_reason="identity_not_checked",
    )  # fmt: skip
    calls = []
    monkeypatch.setattr(library, "_get_document", lambda p, d: calls.append((p, d)) or summary)
    monkeypatch.setattr(library, "get_index_target", lambda p, d: calls.append((p, d)) or target)
    state = library.get_document_state(pid, did)
    assert calls == [(pid, did), (pid, did)]
    assert state.parse_unfinished and not state.parsed and not state.indexed
    assert state.index_blocked_reason == "identity_not_checked"


def test_document_state_scoping_error_propagates(monkeypatch) -> None:
    def refuse(p, d):
        raise DocumentProcessingError("x", kind="document_not_found")

    monkeypatch.setattr(library, "_get_document", refuse)
    with pytest.raises(DocumentProcessingError):
        library.get_document_state(uuid.uuid4(), uuid.uuid4())


@pytest.mark.parametrize(
    ("embedding", "reranker", "modes"),
    [
        (None, None, ("lexical",)),
        ("a" * 40, None, ("hybrid", "dense", "lexical")),
        ("a" * 40, "b" * 40, ("hybrid_rerank", "hybrid", "dense", "lexical")),
    ],
)
def test_only_configured_retrieval_modes_are_offered(embedding, reranker, modes) -> None:
    settings = SimpleNamespace(embedding_model_revision=embedding, reranker_model_revision=reranker)
    assert ui_runtime.available_modes(settings) == modes


def test_an_unconfigured_embedder_refuses_with_a_fixed_kind() -> None:
    from app.core.errors import RetrievalError

    embedder = ui_runtime.make_embedder(SimpleNamespace(embedding_model_revision=None))
    with pytest.raises(RetrievalError) as caught:
        embedder.embed_passages(["x"])
    assert ui_runtime.error_kind(caught.value) == "revision_not_configured"


def test_search_builds_the_cli_configuration_and_passes_only_needed_models(monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(
        ui_runtime,
        "search_evidence",
        lambda pid, q, *, config, embedder, reranker: seen.update(
            config=config, embedder=embedder, reranker=reranker
        ),
    )
    settings = SimpleNamespace(
        retrieval_candidates=50, retrieval_rrf_k=60, retrieval_rerank_top=20, retrieval_top_k=5,
        retrieval_abstain_threshold=None,
    )  # fmt: skip
    ui_runtime.search(settings, uuid.uuid4(), "q", "lexical", embedder="E", reranker="R")
    assert seen["config"].mode is RetrievalMode.LEXICAL and seen["config"].top_k == 5
    assert seen["embedder"] is None and seen["reranker"] is None
