"""MediMind local UI: patients, documents and processing, evidence search (Task 2.9b).

Thin by design (ADR-036 R3): every action is one service call, imported as a module so the
services stay the only place for SQL, rules, the indexing gate and orchestration. This module
only lays out widgets, calls services and explains their fixed codes (``app.ui.texts``).

- ``st.session_state`` holds identifiers and the last pipeline results (identifiers, fixed kinds,
  counts) only: never names, dates of birth, filenames or document text.
- Errors are shown by their fixed kind (``ui_runtime.error_kind``), never by message, and logged
  as kind and class name only.
- Long operations run synchronously inside the request, with progress from the pipeline's
  ``on_stage`` callback; nothing runs in the background.
"""

import datetime as dt
import logging
import uuid
from collections.abc import Callable
from typing import Any

import streamlit as st

from app.services import document_pipeline, library, ui_runtime, upload_document
from app.ui import texts

logger = logging.getLogger(__name__)


PAGES = ("Patients", "Documents", "Evidence search")
PATIENT_KEY = "patient_id"  # str(UUID) of the selected patient
DOCUMENT_KEY = "document_id"  # str(UUID) of the selected document
RESULTS_KEY = "pipeline_results"  # list[DocumentPipelineResult] of the last run


# --- shared helpers ------------------------------------------------------------------------------


def attempt[T](action: Callable[[], T]) -> T | None:
    """Run one service call; on failure show its fixed explanation (never the message)."""
    try:
        return action()
    except Exception as exc:
        kind = ui_runtime.error_kind(exc)
        logger.warning("UI action failed: kind=%s error_class=%s", kind, type(exc).__name__)
        st.error(texts.reason_text(kind))
        return None


@st.cache_resource(show_spinner=False)
def _logging_ready(_settings: Any) -> bool:
    ui_runtime.setup_logging(_settings)
    return True


@st.cache_resource(show_spinner="Preparing the parser...")
def _parser(_settings: Any) -> Any:
    return ui_runtime.make_parser(_settings)


@st.cache_resource(show_spinner=False)
def _embedder(_settings: Any) -> Any:
    return ui_runtime.make_embedder(_settings)


@st.cache_resource(show_spinner=False)
def _reranker(_settings: Any) -> Any:
    return ui_runtime.make_reranker(_settings)


def _request_selection(key: str, value: str | None) -> None:
    """Select ``value`` (or clear the selection) on the next run, before the widget is drawn.

    Streamlit forbids changing a widget's key once that widget is drawn in the current run.
    """
    st.session_state[f"_next_{key}"] = value or ""


def _apply_requested_selection(key: str) -> None:
    requested = st.session_state.pop(f"_next_{key}", None)
    if requested is None:
        return
    if requested:
        st.session_state[key] = requested
    else:
        st.session_state.pop(key, None)


def _selected(key: str) -> uuid.UUID | None:
    value = st.session_state.get(key)
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None


def _when(value: dt.datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


# --- entry point ---------------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title="MediMind (local prototype)", page_icon="🩺", layout="wide")
    settings = attempt(ui_runtime.load_settings)
    if settings is None:
        st.stop()
    _logging_ready(settings)
    st.title("MediMind")
    st.caption("Local prototype. Information management only: no diagnosis or medical advice.")
    if settings.demo_mode:
        st.info(texts.DEMO_BANNER, icon="🧪")

    patient = _patient_selector(settings)
    page = st.sidebar.radio("Page", PAGES, key="page")
    if page == "Patients":
        patients_page(settings, patient)
    elif patient is None:
        st.info("Select or create a patient on the Patients page first.")
    elif page == "Documents":
        documents_page(settings, patient)
    else:
        search_page(settings, patient)


def _patient_selector(settings: Any) -> Any:
    """The sidebar selector; returns the selected patient (re-read, scoped) or None."""
    _apply_requested_selection(PATIENT_KEY)
    patients = attempt(lambda: library.list_patients(synthetic_only=settings.demo_mode))
    if not patients:
        st.sidebar.write("No patients yet." if patients == () else "Patients unavailable.")
        st.session_state.pop(PATIENT_KEY, None)
        return None
    names = {str(p.id): p.display_name for p in patients}  # for display only, never stored
    if st.session_state.get(PATIENT_KEY) not in names:
        st.session_state[PATIENT_KEY] = next(iter(names))
    st.sidebar.selectbox(
        "Patient", list(names), format_func=lambda pid: names.get(pid, pid), key=PATIENT_KEY
    )
    patient_id = _selected(PATIENT_KEY)
    if patient_id is None:
        return None
    return attempt(lambda: library.get_patient(patient_id, synthetic_only=settings.demo_mode))


# --- patients ------------------------------------------------------------------------------------


def patients_page(settings: Any, patient: Any) -> None:
    st.header("Patients")
    patients = attempt(lambda: library.list_patients(synthetic_only=settings.demo_mode)) or ()
    if patients:
        st.dataframe(
            [
                {
                    "Name": p.display_name,
                    "Date of birth": p.date_of_birth.isoformat() if p.date_of_birth else "",
                    "Synthetic": "yes" if p.is_synthetic else "no",
                    "Created": _when(p.created_at),
                }
                for p in patients
            ],
            hide_index=True,
        )
    else:
        st.write("No patients yet. Create one below.")

    with st.form("create_patient", clear_on_submit=True):
        st.subheader("Create a patient")
        name = st.text_input("Display name")
        has_dob = st.checkbox("Set a date of birth")
        dob = st.date_input("Date of birth", value=None, min_value=dt.date(1900, 1, 1))
        if settings.demo_mode:
            st.caption("Demo mode: the patient is created as synthetic (fictional).")
            synthetic = True
        else:
            synthetic = st.checkbox("Synthetic (fictional) patient", value=False)
        if st.form_submit_button("Create patient"):
            created = attempt(
                lambda: library.create_patient(
                    name, is_synthetic=synthetic, date_of_birth=dob if has_dob else None
                )
            )
            if created is not None:
                _request_selection(PATIENT_KEY, str(created.id))
                st.rerun()

    if patient is not None:
        _patient_details(patient)


def _patient_details(patient: Any) -> None:
    st.subheader("Selected patient")
    st.text(f"Name: {patient.display_name}")
    st.text(f"Date of birth: {patient.date_of_birth.isoformat() if patient.date_of_birth else '-'}")
    st.text(f"Synthetic: {'yes' if patient.is_synthetic else 'no'}")
    st.caption(
        "Editing the profile does not re-check documents that were already checked "
        "(no automatic re-check)."
    )
    with st.form("update_patient"):
        name = st.text_input("New display name (leave empty to keep)")
        dob_choice = st.radio(
            "Date of birth", ("Keep", "Set", "Clear"), horizontal=True, key="dob_choice"
        )
        dob = st.date_input("New date of birth", value=None, min_value=dt.date(1900, 1, 1))
        if st.form_submit_button("Save changes"):
            changes: dict[str, Any] = {}
            if name.strip():
                changes["display_name"] = name
            if dob_choice == "Set" and dob is not None:
                changes["date_of_birth"] = dob
            elif dob_choice == "Clear":
                changes["date_of_birth"] = None
            if not changes:
                st.info("Nothing to change.")
            elif attempt(lambda: library.update_patient(patient.id, **changes)) is not None:
                st.success("Patient updated.")
                st.rerun()

    with st.expander("Delete this patient"):
        st.write("Only a patient with no documents can be deleted.")
        confirm = st.checkbox("I understand this patient profile will be deleted.", key="del_pat")
        if st.button("Delete patient", disabled=not confirm):
            done = attempt(lambda: library.delete_patient(patient.id) or True)
            if done:
                _request_selection(PATIENT_KEY, None)
                st.rerun()


# --- documents -----------------------------------------------------------------------------------


def documents_page(settings: Any, patient: Any) -> None:
    st.header("Documents")
    _upload(settings, patient)
    documents = attempt(lambda: library.list_documents(patient.id))
    if documents is None:
        return
    if not documents:
        st.write("No documents yet.")
        return
    states = {
        d.id: attempt(lambda d=d: library.get_document_state(patient.id, d.id)) for d in documents
    }
    st.dataframe([_row(d, states.get(d.id)) for d in documents], hide_index=True)

    st.caption(texts.PROCESSING_NOTE)
    if st.button("Process all documents of this patient"):
        _run_pipeline(settings, patient.id, documents=documents)

    names = {str(d.id): d.original_filename for d in documents}  # display only
    _apply_requested_selection(DOCUMENT_KEY)
    if st.session_state.get(DOCUMENT_KEY) not in names:
        st.session_state[DOCUMENT_KEY] = next(iter(names))
    st.selectbox("Document", list(names), format_func=lambda i: names.get(i, i), key=DOCUMENT_KEY)
    document_id = _selected(DOCUMENT_KEY)
    document = next((d for d in documents if d.id == document_id), None)
    if document is not None:
        _document_detail(settings, patient, document, states.get(document.id))
    _show_results(documents)


def _row(document: Any, state: Any) -> dict[str, Any]:
    classified = bool(state and state.classified)
    checked = bool(state and state.identity_checked)
    return {
        "File": document.original_filename,
        "Type": texts.type_label(
            document.doc_type, document.doc_type_confidence, classified=classified
        ),
        "Identity": texts.identity_badge(document.identity_check, checked=checked),
        "Status": texts.processing_label(
            document.processing_status,
            parsed=bool(state and state.parsed),
            indexed=bool(state and state.indexed),
        ),
        "Pages": document.page_count,
        "Uploaded": _when(document.created_at),
        "Updated": _when(document.updated_at),
    }


def _upload(settings: Any, patient: Any) -> None:
    if settings.demo_mode:
        st.info(texts.DEMO_UPLOAD_DISABLED)
        return
    with st.form("upload", clear_on_submit=True):
        file = st.file_uploader("Upload a PDF for this patient", type=["pdf"])
        if st.form_submit_button("Upload") and file is not None:
            handle_upload(settings, patient.id, file.getvalue(), file.name)


def handle_upload(settings: Any, patient_id: uuid.UUID, data: bytes, filename: str) -> None:
    """Store one uploaded PDF for the selected patient and select it. Never in demo mode."""
    if settings.demo_mode:
        st.info(texts.DEMO_UPLOAD_DISABLED)
        return
    result = attempt(
        lambda: upload_document.upload_document(
            settings, patient_id, data, original_filename=filename
        )
    )
    if result is None:
        return
    _request_selection(DOCUMENT_KEY, str(result.document.id))
    if result.duplicate:
        st.info("This exact file was already uploaded for this patient; selected it.")
    else:
        st.success("Uploaded. Select 'Process' to parse, classify, check and index it.")


def _document_detail(settings: Any, patient: Any, document: Any, state: Any) -> None:
    st.subheader("Selected document")
    st.text(f"File: {document.original_filename}")
    if state is None:
        return
    st.text(
        f"Status: {texts.processing_label(document.processing_status, parsed=state.parsed, indexed=state.indexed)}"  # noqa: E501
    )
    st.text(f"Pages stored: {state.page_rows}   Search chunks: {state.chunk_count}")

    # classification and identity are shown independently (ADR-036 R2)
    if not state.classified:
        st.caption(f"Type: {texts.TYPE_NOT_CLASSIFIED}")
    elif document.doc_type == "unknown":
        st.info(texts.TYPE_UNKNOWN)
    else:
        st.caption(
            f"Type: {texts.type_label(document.doc_type, document.doc_type_confidence, classified=True)}"  # noqa: E501
            f" — {texts.RULE_SCORE_NOTE}"
        )
    if not state.identity_checked:
        st.caption(texts.IDENTITY_NOT_CHECKED)
    else:
        level, message = texts.IDENTITY_TEXTS.get(
            state.identity_check, ("info", state.identity_check)
        )
        getattr(st, level)(message)
    if state.parse_unfinished:
        st.warning(texts.reason_text("already_running"))
    elif state.parsed and not state.indexed and state.index_blocked_reason:
        st.info(f"Not indexed: {texts.reason_text(state.index_blocked_reason)}")

    if st.button("Process this document"):
        _run_pipeline(settings, patient.id, document_id=document.id)

    with st.form("rename"):
        new_name = st.text_input("Rename (file name shown in the library)")
        if st.form_submit_button("Rename") and new_name.strip():
            if attempt(lambda: library.rename_document(patient.id, document.id, new_name)):
                st.success("Renamed.")
                st.rerun()

    with st.expander("Delete this document"):
        st.warning(texts.DELETE_DOCUMENT_WARNING)
        confirm = st.checkbox("I understand; delete this document's records.", key="del_doc")
        if st.button("Delete document", disabled=not confirm):
            deleted = attempt(lambda: library.delete_document(patient.id, document.id))
            if deleted is not None:
                _request_selection(DOCUMENT_KEY, None)
                st.session_state[RESULTS_KEY] = None
                st.success(
                    f"Deleted: {deleted.pages_deleted} page rows and {deleted.runs_deleted} "
                    "processing records. Files on disk were not deleted."
                )


def _run_pipeline(
    settings: Any,
    patient_id: uuid.UUID,
    *,
    document_id: uuid.UUID | None = None,
    documents: Any = (),
) -> None:
    """Run the pipeline now (synchronously) and keep its results for display."""
    with st.status("Processing... this page waits until it finishes.", expanded=True) as status:

        def on_stage(doc_id: uuid.UUID, stage: Any) -> None:
            status.write(
                f"`{str(doc_id)[:8]}` {texts.stage_name(stage.stage)}: "
                f"{texts.status_badge(stage.status)}"
            )

        parser, embedder = _parser(settings), _embedder(settings)
        if document_id is not None:
            result = attempt(
                lambda: document_pipeline.run_document_pipeline(
                    settings, parser, embedder, patient_id, document_id, on_stage=on_stage
                )
            )
            results = [result] if result is not None else None
        else:
            many = attempt(
                lambda: document_pipeline.run_patient_pipeline(
                    settings, parser, embedder, patient_id, on_stage=on_stage
                )
            )
            results = list(many) if many is not None else None
        if results is None:
            status.update(label="Processing could not start.", state="error")
            return
        failed = any(s.status == "failed" for r in results for s in r.stages)
        status.update(
            label="Processing finished" + (" with failures." if failed else "."),
            state="error" if failed else "complete",
            expanded=True,
        )
    st.session_state[RESULTS_KEY] = results
    st.caption("The table above shows the state before this run; it refreshes on the next action.")


def _show_results(documents: Any) -> None:
    results = st.session_state.get(RESULTS_KEY)
    if not results:
        return
    names = {d.id: d.original_filename for d in documents}
    st.subheader("Last processing run")
    for result in results:
        label = names.get(result.document_id, f"Document {str(result.document_id)[:8]}")
        with st.expander(label, expanded=True):
            st.table(
                [
                    {
                        "Stage": texts.stage_name(s.stage),
                        "Result": texts.status_badge(s.status),
                        "Explanation": texts.reason_text(s.reason),
                    }
                    for s in result.stages
                ]
            )
            classify = result.stage("classify").detail
            identity = result.stage("identity").detail
            if classify is not None:
                st.caption(
                    f"Classified as {classify.doc_type} (rule score {classify.score:.3f}, "
                    f"rules v{classify.rules_version}). {texts.RULE_SCORE_NOTE}"
                )
            if identity is not None:
                level, message = texts.IDENTITY_TEXTS.get(identity.outcome, ("info", ""))
                getattr(st, level)(message)
                st.caption(
                    f"Identity rules v{identity.rules_version}; reasons: "
                    f"{', '.join(identity.reason_codes)}"
                )


# --- evidence search -----------------------------------------------------------------------------


def search_page(settings: Any, patient: Any) -> None:
    st.header("Evidence search")
    st.caption(texts.NO_ADVICE_NOTE)
    modes = ui_runtime.available_modes(settings)
    with st.form("search"):
        question = st.text_input("Question about this patient's documents")
        mode = st.selectbox("Retrieval mode", modes)
        submitted = st.form_submit_button("Search")
    if not submitted or not question.strip():
        return
    answer = attempt(
        lambda: ui_runtime.search(
            settings,
            patient.id,
            question,
            mode,
            embedder=_embedder(settings),
            reranker=_reranker(settings),
        )
    )
    if answer is None:
        return
    _show_answer(answer)  # rendered now, never kept in session state


def _show_answer(answer: Any) -> None:
    if answer.message:
        st.warning(answer.message)  # abstention or out of scope: nothing is guessed
        return
    if not answer.threshold_calibrated:
        st.caption("The abstention threshold is not calibrated: low-scoring pages are still shown.")
    for number, passage in enumerate(answer.passages, start=1):
        date = passage.document_date.isoformat() if passage.document_date else "date unknown"
        st.markdown(
            f"**{number}.** page {passage.page_number} · {date} · score {passage.score:.4f}"
        )
        st.text(f"[{passage.original_filename}, p. {passage.page_number}]")
        if passage.identity_warning:
            st.warning(passage.identity_warning)
        if passage.ocr_warning:
            st.warning(passage.ocr_warning)
        st.text(passage.excerpt)
