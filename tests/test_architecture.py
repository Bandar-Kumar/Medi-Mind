"""Architecture guard for ADR-016: the records layer is the only database access path.

Outside ``app/records/``, code may not:
- import SQLAlchemy, psycopg or Alembic directly;
- import anything from ``app.records`` except a short allow-list per module: the sanitizing
  helpers, the health probes and the migrate command. Models (``app.records.models``), sessions
  and engines stay inside the records layer.

So no service, UI or core module can open an engine or a session and run unscoped queries.
Patient-scoped repositories (Task 1.8) live inside ``app/records/``. Their public functions take
``patient_id`` first, except ``create_patient`` and ``list_patients`` (checked below).
"""

import ast
import inspect
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"
RECORDS_DIR = APP_DIR / "records"

FORBIDDEN_TOP_LEVEL_PACKAGES = {"sqlalchemy", "psycopg", "alembic"}
# The only names that code outside app/records/ may import, per records module.
ALLOWED_RECORDS_NAMES: dict[str, set[str]] = {
    "app.records.db": {
        "probe_database",  # authenticated health check (app/core/health.py)
        "DbErrorKind",  # health check severity
        "describe_db_error",  # entry-point sanitization (app/main.py)
        "is_database_error",
    },
    "app.records.migrations": {
        "schema_status",  # database_schema health check (read-only)
        "SchemaState",
        "run_upgrade",  # `python -m app migrate` (upgrade only)
    },
    "app.records.repositories": {
        "PageRecord",  # app/services/parsed_pages.py (Task 2.5c)
        "ProcessingErrorKind",
        "persist_parsed_pages",
        "DocumentRef",  # app/services/process_document.py (Task 2.5d)
        "ParseClaim",
        "claim_document_for_parsing",
        "parse_run_belongs_to_document",
        "record_parse_failure",
        "PATIENT_NOT_FOUND",  # app/services/upload_document.py (Task 2.6b)
        "create_document",
        "patient_exists",
        "ALREADY_CLASSIFIED",  # app/services/classify_document.py (Task 2.7)
        "DOCUMENT_NOT_FOUND",
        "NOT_PARSED",
        "list_document_pages",
        "record_classification",
        "record_classification_failure",
        "ALREADY_CHECKED",  # app/services/check_identity.py (Task 2.8)
        "get_patient",
        "record_identity_check",
        "record_identity_failure",
        "DeletedDocument",  # app/services/library.py (Task 2.9a): thin wrappers for the UI
        "DocumentSummary",
        "PatientRef",
        "PatientSummary",
        "create_patient",
        "delete_document",
        "delete_patient",
        "get_document",
        "list_documents",
        "list_patients",
        "rename_document",
        "update_patient",
    },
    "app.records.facts": {  # Phase 3 services (docs/PHASE_3_PLAN.md); the pure gate rule too
        "ALREADY_EXTRACTED",
        "AnalyteSeed",
        "ConflictDetector",
        "ConflictRecord",
        "DetectedConflict",
        "EXTRACT_GATE_REFUSALS",
        "ExtractionRunRecord",
        "FactRecord",
        "GroupFact",
        "NOT_PARSED",
        "ObservationRecord",
        "ReportDate",
        "ReviewRecord",
        "SeedResult",
        "acknowledge_conflict",
        "complete_document_if_ready",
        "extraction_applies",
        "get_extraction_target",
        "get_fact",
        "list_conflicts",
        "list_document_facts",
        "list_extraction_runs",
        "record_extraction",
        "record_extraction_failure",
        "record_extraction_skipped",
        "record_review_decision",
        "reextract_and_supersede",
        "reference_analyte_ids",
        "seed_reference_data",
    },
    "app.records.clinical": {  # Phase 6 services (docs/PHASE_6_PLAN.md; ADR-042)
        "LAB_REPORT",
        "LAB_REPORT_REFUSAL",
        "ClinicalDecision",
        "ClinicalDocumentView",
        "ClinicalFactRecord",
        "ClinicalFactView",
        "get_clinical_fact",
        "get_clinical_target",
        "list_clinical_documents",
        "list_clinical_facts",
        "record_clinical_extraction",
        "record_clinical_failure",
        "record_clinical_skipped",
        "stored_page_texts",  # answer-time citation checks (Phase 6 acceptance fix)
    },
    "app.records.longitudinal": {  # Phase 4 reads (docs/PHASE_4_PLAN.md; ADR-039)
        "TimelineDocumentRecord",
        "count_facts_by_status",
        "list_facts_by_ids",
        "list_timeline_documents",
        "list_usable_facts",
    },
    "app.records.chunks": {
        "ALREADY_INDEXED",  # app/services/index_document.py (Milestone R1.6)
        "GATE_REFUSALS",  # the indexing gate's refusal kinds (Task 2.8)
        "NOT_PARSED",
        "ChunkRecord",
        "get_index_target",
        "insert_document_chunks",
        "list_unindexed_documents",
        "ChunkHit",  # app/services/search_evidence.py (Milestone R1.7)
        "dense_search",
        "lexical_search",
        "page_contexts",
        "stored_chunks",  # app/services/copilot.py: citation validation (Phase 5, ADR-040)
    },
}


def _python_files_outside_records() -> list[Path]:
    return [p for p in APP_DIR.rglob("*.py") if RECORDS_DIR not in p.parents]


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_TOP_LEVEL_PACKAGES:
                    found.append(f"import {alias.name}")
                if alias.name == "app.records" or alias.name.startswith("app.records."):
                    found.append(f"import {alias.name} (use the allowed names only)")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in FORBIDDEN_TOP_LEVEL_PACKAGES:
                found.append(f"from {node.module} import ...")
            if node.module == "app.records":
                found.append("from app.records import ... (import the allowed names only)")
            elif node.module.startswith("app.records."):
                allowed = ALLOWED_RECORDS_NAMES.get(node.module, set())
                for alias in node.names:
                    if alias.name not in allowed:
                        found.append(f"from {node.module} import {alias.name}")
    return found


def test_only_the_records_layer_touches_the_database() -> None:
    problems = {
        str(path.relative_to(APP_DIR.parent)): issues
        for path in _python_files_outside_records()
        if (issues := _violations(path))
    }
    assert problems == {}, f"Database access outside app/records/ (ADR-016): {problems}"


def test_sessions_engines_and_models_are_not_on_the_allow_list() -> None:
    allowed = set().union(*ALLOWED_RECORDS_NAMES.values())
    for name in ("session_scope", "get_engine", "create_db_engine", "build_database_url"):
        assert name not in allowed
    assert "app.records.models" not in ALLOWED_RECORDS_NAMES


def test_the_guard_detects_a_violation(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text(
        "from app.records.db import session_scope\n"
        "from sqlalchemy.orm import Session\n"
        "from app.records.models import Patient\n"
        "from alembic import command\n"
        "import app.records.migrations\n",
        encoding="utf-8",
    )
    assert _violations(bad) == [
        "from app.records.db import session_scope",
        "from sqlalchemy.orm import ...",
        "from app.records.models import Patient",
        "from alembic import ...",
        "import app.records.migrations (use the allowed names only)",
    ]


INGESTION_DIR = APP_DIR / "ingestion"
# Ingestion is database-free: not even the names allowed above for health/migrate.
INGESTION_FORBIDDEN_PREFIXES = ("sqlalchemy", "psycopg", "alembic", "pgvector")
INGESTION_FORBIDDEN_APP_PACKAGES = ("app.records", "app.services")


def _ingestion_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return [
        name
        for name in names
        if name.split(".")[0] in INGESTION_FORBIDDEN_PREFIXES
        or any(name == p or name.startswith(p + ".") for p in INGESTION_FORBIDDEN_APP_PACKAGES)
    ]


def test_ingestion_is_independent_of_the_database() -> None:
    files = list(INGESTION_DIR.rglob("*.py"))
    assert files, "app/ingestion/ not found"
    problems = {
        str(path.relative_to(APP_DIR.parent)): issues
        for path in files
        if (issues := _ingestion_violations(path))
    }
    assert problems == {}, f"app/ingestion/ must not touch the database: {problems}"


def test_the_ingestion_guard_detects_a_violation(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text(
        "from app.records.db import probe_database\n"
        "import app.services.documents\n"
        "from sqlalchemy import select\n"
        "import pgvector\n"
        "from app.core.errors import DocumentStorageError\n",
        encoding="utf-8",
    )
    assert _ingestion_violations(bad) == [
        "app.records.db",
        "app.services.documents",
        "sqlalchemy",
        "pgvector",
    ]


HEAVY_MODULES = ("docling", "docling_core", "rapidocr", "torch", "transformers")


def test_importing_the_app_does_not_load_docling_or_torch() -> None:
    """Docling and torch are heavy (seconds to import, ~1 GB with models): load them lazily.

    A fresh interpreter imports the entry point and every app module that exists; none of the
    HEAVY modules may appear in sys.modules afterwards. docling_core alone takes ~1.3 s.
    """
    import subprocess
    import sys

    modules = sorted(
        ".".join(p.relative_to(APP_DIR.parent).with_suffix("").parts)
        for p in APP_DIR.rglob("*.py")
        if p.name != "__main__.py"
    )
    code = (
        "import importlib, sys\n"
        f"for name in {modules!r}:\n"
        "    importlib.import_module(name.removesuffix('.__init__'))\n"
        f"heavy = sorted(m for m in {HEAVY_MODULES!r} if m in sys.modules)\n"
        "print(','.join(heavy))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=APP_DIR.parent,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "", f"heavy modules imported eagerly: {result.stdout}"


# --- patient scoping of the repository API (ADR-016; Task 1.8a) ---------------------------------

# The only public repository functions that do not take ``patient_id`` first: they create or list
# the patient scopes themselves (ADR-016, ADR-033). Every other one must be patient-scoped.
UNSCOPED_REPOSITORY_FUNCTIONS = frozenset({"create_patient", "list_patients"})
# The complete public repository API (Task 1.8f). Adding a function must update this list
# deliberately, so its patient scoping is reviewed.
PUBLIC_REPOSITORY_API = frozenset(
    {
        "create_patient",
        "list_patients",
        "patient_exists",
        "update_patient",
        "delete_patient",
        "create_document",
        "list_documents",
        "get_document",
        "rename_document",
        "delete_document",
        "claim_document_for_parsing",
        "persist_parsed_pages",
        "record_parse_failure",
        "parse_run_belongs_to_document",
        "list_document_pages",  # Task 2.7 (ADR-036)
        "record_classification",
        "record_classification_failure",
        "get_patient",  # Task 2.8 (ADR-036)
        "record_identity_check",
        "record_identity_failure",
    }
)


def _public_repository_functions() -> dict[str, object]:
    """Functions defined in ``app.records.repositories`` itself, without a leading underscore.

    Imported names (``select``, ``session_scope``, ...) and private helpers are excluded.
    """
    from app.records import repositories

    return {
        name: function
        for name, function in inspect.getmembers(repositories, inspect.isfunction)
        if not name.startswith("_") and function.__module__ == repositories.__name__
    }


def test_only_the_approved_repository_functions_are_unscoped() -> None:
    unscoped = {
        name
        for name, function in _public_repository_functions().items()
        if next(iter(inspect.signature(function).parameters), None) != "patient_id"
    }
    assert unscoped == UNSCOPED_REPOSITORY_FUNCTIONS


def test_every_patient_operation_except_the_exceptions_is_patient_scoped() -> None:
    functions = _public_repository_functions()
    assert UNSCOPED_REPOSITORY_FUNCTIONS <= functions.keys()  # the exceptions really exist
    for name in ("patient_exists", "update_patient", "delete_patient"):
        first = next(iter(inspect.signature(functions[name]).parameters))
        assert first == "patient_id", name
    for name, function in functions.items():
        parameters = list(inspect.signature(function).parameters)
        if "document_id" in parameters:
            assert parameters[0] == "patient_id", name


def test_the_public_repository_api_is_exactly_the_reviewed_list() -> None:
    assert set(_public_repository_functions()) == PUBLIC_REPOSITORY_API


def test_every_document_operation_takes_patient_id_then_document_id() -> None:
    functions = _public_repository_functions()
    for name in (
        "get_document",
        "rename_document",
        "delete_document",
        "list_document_pages",
        "record_classification",
        "record_classification_failure",
        "record_identity_check",
        "record_identity_failure",
    ):
        assert list(inspect.signature(functions[name]).parameters)[:2] == [
            "patient_id",
            "document_id",
        ], name


# --- patient scoping of the chunk repository (ADR-016; Milestone R1.4) ---------------------------

PUBLIC_CHUNK_API = frozenset(
    {
        "get_index_target",
        "list_unindexed_documents",
        "insert_document_chunks",
        "dense_search",
        "lexical_search",
        "page_contexts",
        "stored_chunks",  # Phase 5 citation validation (ADR-040)
    }
)


def test_every_chunk_repository_function_takes_patient_id_first() -> None:
    from app.records import chunks

    functions = {
        name: function
        for name, function in inspect.getmembers(chunks, inspect.isfunction)
        if not name.startswith("_") and function.__module__ == chunks.__name__
    }
    assert set(functions) == PUBLIC_CHUNK_API
    for name, function in functions.items():
        assert next(iter(inspect.signature(function).parameters)) == "patient_id", name


PUBLIC_LONGITUDINAL_API = frozenset(
    {"list_usable_facts", "list_facts_by_ids", "list_timeline_documents", "count_facts_by_status"}
)


def test_every_longitudinal_repository_function_is_read_only_and_patient_scoped() -> None:
    from app.records import longitudinal

    functions = {
        name: function
        for name, function in inspect.getmembers(longitudinal, inspect.isfunction)
        if not name.startswith("_") and function.__module__ == longitudinal.__name__
    }
    assert set(functions) == PUBLIC_LONGITUDINAL_API
    for name, function in functions.items():
        assert next(iter(inspect.signature(function).parameters)) == "patient_id", name
    source = (RECORDS_DIR / "longitudinal.py").read_text(encoding="utf-8")
    for write in ("insert(", "update(", "delete(", ".add(", ".merge("):
        assert write not in source, write  # Phase 4 reads only; nothing is stored


def test_longitudinal_logic_is_pure() -> None:
    """app/longitudinal/: no database, records, services, UI or model libraries (ADR-003)."""
    for path in (APP_DIR / "longitudinal").rglob("*.py"):
        for module in _imports(path):
            assert not module.startswith(
                ("sqlalchemy", "psycopg", "app.records", "app.services", "app.ui", "torch")
            ), f"{path.name} imports {module}"
            assert not module.startswith(("transformers", "docling", "streamlit")), module


def test_retrieval_logic_contains_no_database_access() -> None:
    """app/retrieval/ is pure: no SQLAlchemy, no records layer (services orchestrate)."""
    for path in (APP_DIR / "retrieval").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                assert not module.startswith(("sqlalchemy", "psycopg", "app.records")), (
                    f"{path.name} imports {module}"
                )


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_classification_rules_are_pure() -> None:
    """app/ingestion/classification.py: no records, services, database, files or models."""
    modules = _imports(INGESTION_DIR / "classification.py")
    assert modules <= {"re", "collections.abc", "dataclasses", "typing"}, modules


def test_identity_rules_are_pure() -> None:
    """app/ingestion/identity.py: standard library only (Task 2.8)."""
    modules = _imports(INGESTION_DIR / "identity.py")
    allowed = {"datetime", "re", "unicodedata", "collections.abc", "dataclasses", "typing"}
    assert modules <= allowed, modules


def test_classification_and_identity_do_not_depend_on_each_other() -> None:
    """ADR-036 R2: independent stages, both reading the parsed pages only."""
    for path, other in (
        (INGESTION_DIR / "classification.py", "app.ingestion.identity"),
        (INGESTION_DIR / "identity.py", "app.ingestion.classification"),
        (APP_DIR / "services" / "classify_document.py", "app.services.check_identity"),
        (APP_DIR / "services" / "check_identity.py", "app.services.classify_document"),
    ):
        assert other not in _imports(path), path.name


# --- Phase 3: extraction (docs/PHASE_3_PLAN.md §2.4, §2.6) ----------------------------------

EXTRACTION_FORBIDDEN = (
    "sqlalchemy",
    "psycopg",
    "app.records",
    "app.services",
    "app.ui",
    "app.retrieval",
    "docling",
    "torch",
    "transformers",
    "app.ingestion.identity",
    "app.ingestion.classification",
    "app.ingestion.docling_mapping",
    "app.ingestion.parsing",
)


def test_extraction_is_pure_domain_logic() -> None:
    """app/extraction/: no database, services, UI, models, Docling, or the frozen Phase 2 rules."""
    for path in (APP_DIR / "extraction").rglob("*.py"):
        for module in _imports(path):
            assert not module.startswith(EXTRACTION_FORBIDDEN), f"{path.name} imports {module}"


def test_frozen_phase2_rule_files_are_untouched_by_extraction() -> None:
    """The extraction subject guard is its own; it never imports the frozen identity rules."""
    for path in (APP_DIR / "extraction").rglob("*.py"):
        assert "app.ingestion.identity" not in _imports(path)


# --- patient scoping of the extraction repository (Phase 3; ADR-016) -------------------------

UNSCOPED_FACT_FUNCTIONS = frozenset(
    {
        "extraction_gate_refusal",  # pure rule, no database access
        "extraction_applies",  # pure rule, no database access
        "seed_reference_data",  # reference tables only, no patient data
        "reference_analyte_ids",  # reference tables only, read-only
        "close_conflicts_for_deleted_document",  # runs inside delete_document's transaction
    }
)


def test_every_fact_repository_function_takes_patient_id_first() -> None:
    from app.records import facts

    functions = {
        name: function
        for name, function in inspect.getmembers(facts, inspect.isfunction)
        if not name.startswith("_") and function.__module__ == facts.__name__
    }
    assert UNSCOPED_FACT_FUNCTIONS <= functions.keys()
    for name, function in functions.items():
        parameters = list(inspect.signature(function).parameters)
        if name in UNSCOPED_FACT_FUNCTIONS:
            assert (
                "patient_id" not in parameters[:1] or name == "close_conflicts_for_deleted_document"
            )
            continue
        assert parameters[0] == "patient_id", name
        if "document_id" in parameters:
            assert parameters[1] == "document_id", name


def test_records_layer_never_imports_domain_rules() -> None:
    """Conflict comparison is injected as a detector; records stays free of domain logic."""
    for path in RECORDS_DIR.glob("*.py"):
        for module in _imports(path):
            assert not module.startswith(("app.extraction", "app.services", "app.ingestion")), (
                f"{path.name} imports {module}"
            )
