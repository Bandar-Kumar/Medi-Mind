"""Core tables: vector extension, patients, documents, pages, pipeline_runs

Revision ID: 0001
Revises: <base>
Create Date: 2026-11-13

Frozen migration (Task 1.7): literal names and values only; it must never import application
code, so later model changes cannot rewrite history. Every CHECK, foreign key and index here is
mirrored by app/records/models.py, and an integration test compares the two catalogs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "patients",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(display_name)) BETWEEN 1 AND 200",
            name=op.f("ck_patients_display_name_length"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_patients")),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("doc_type", sa.Text(), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("doc_type_confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column("document_date_precision", sa.Text(), nullable=True),
        sa.Column("document_date_raw", sa.Text(), nullable=True),
        sa.Column(
            "processing_status", sa.Text(), server_default=sa.text("'uploaded'"), nullable=False
        ),
        sa.Column("identity_check", sa.Text(), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("supersedes_document_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "doc_type IN ('lab_report', 'prescription', 'discharge_summary', 'other', 'unknown')",
            name=op.f("ck_documents_doc_type_values"),
        ),
        sa.CheckConstraint(
            "document_date_precision IN ('day', 'month', 'year')",
            name=op.f("ck_documents_document_date_precision_values"),
        ),
        sa.CheckConstraint(
            "document_date_precision IS NULL OR document_date_precision = 'day' OR (document_date_precision = 'month' AND extract(day FROM document_date) = 1) OR (document_date_precision = 'year' AND extract(month FROM document_date) = 1 AND extract(day FROM document_date) = 1)",
            name=op.f("ck_documents_document_date_normalized"),
        ),
        sa.CheckConstraint(
            "identity_check IN ('match', 'mismatch', 'unknown')",
            name=op.f("ck_documents_identity_check_values"),
        ),
        sa.CheckConstraint(
            "processing_status IN ('uploaded', 'processing', 'processed', 'failed', 'needs_review')",
            name=op.f("ck_documents_processing_status_values"),
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_documents_sha256_hex")),
        sa.CheckConstraint(
            "(document_date IS NULL) = (document_date_precision IS NULL)",
            name=op.f("ck_documents_document_date_pair"),
        ),
        sa.CheckConstraint(
            "doc_type_confidence IS NULL OR doc_type_confidence BETWEEN 0 AND 1",
            name=op.f("ck_documents_doc_type_confidence_range"),
        ),
        sa.CheckConstraint(
            "length(original_filename) BETWEEN 1 AND 255",
            name=op.f("ck_documents_original_filename_length"),
        ),
        sa.CheckConstraint(
            "page_count IS NULL OR page_count >= 1", name=op.f("ck_documents_page_count_positive")
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_documents_size_bytes_positive")),
        sa.CheckConstraint(
            "supersedes_document_id <> id", name=op.f("ck_documents_not_self_superseding")
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_documents_patient_id_patients"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_document_id", "patient_id"],
            ["documents.id", "documents.patient_id"],
            name=op.f("fk_documents_supersedes_document_id_patient_id_documents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("id", "patient_id", name=op.f("uq_documents_id_patient_id")),
        sa.UniqueConstraint("patient_id", "sha256", name=op.f("uq_documents_patient_id_sha256")),
        sa.UniqueConstraint("storage_path", name=op.f("uq_documents_storage_path")),
        sa.UniqueConstraint(
            "supersedes_document_id", name=op.f("uq_documents_supersedes_document_id")
        ),
    )
    op.create_table(
        "pages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("parse_method", sa.Text(), nullable=False),
        sa.Column("has_tables", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "parse_method IN ('native', 'ocr')", name=op.f("ck_pages_parse_method_values")
        ),
        sa.CheckConstraint("page_number >= 1", name=op.f("ck_pages_page_number_positive")),
        sa.ForeignKeyConstraint(
            ["document_id", "patient_id"],
            ["documents.id", "documents.patient_id"],
            name=op.f("fk_pages_document_id_patient_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pages")),
        sa.UniqueConstraint(
            "document_id", "page_number", name=op.f("uq_pages_document_id_page_number")
        ),
    )
    op.create_index(
        "ix_pages_document_id_patient_id", "pages", ["document_id", "patient_id"], unique=False
    )
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "component_versions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("error_kind", sa.Text(), nullable=True),
        sa.Column("error_class", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name=op.f("ck_pipeline_runs_finished_matches"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'skipped')",
            name=op.f("ck_pipeline_runs_status_values"),
        ),
        sa.CheckConstraint(
            "cost_usd IS NULL OR cost_usd >= 0", name=op.f("ck_pipeline_runs_cost_usd_positive")
        ),
        sa.CheckConstraint(
            "document_id IS NULL OR patient_id IS NOT NULL",
            name=op.f("ck_pipeline_runs_document_needs_patient"),
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name=op.f("ck_pipeline_runs_duration_ms_positive"),
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name=op.f("ck_pipeline_runs_finished_after_started"),
        ),
        sa.CheckConstraint(
            "length(stage) BETWEEN 1 AND 64", name=op.f("ck_pipeline_runs_stage_length")
        ),
        sa.CheckConstraint(
            "tokens_in IS NULL OR tokens_in >= 0", name=op.f("ck_pipeline_runs_tokens_in_positive")
        ),
        sa.CheckConstraint(
            "tokens_out IS NULL OR tokens_out >= 0",
            name=op.f("ck_pipeline_runs_tokens_out_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "patient_id"],
            ["documents.id", "documents.patient_id"],
            name=op.f("fk_pipeline_runs_document_id_patient_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_pipeline_runs_patient_id_patients"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pipeline_runs")),
    )
    op.create_index(
        "ix_pipeline_runs_document_id_started_at",
        "pipeline_runs",
        ["document_id", "started_at"],
        unique=False,
    )
    op.create_index("ix_pipeline_runs_patient_id", "pipeline_runs", ["patient_id"], unique=False)


def downgrade() -> None:
    # Destroys data: every table of this revision is dropped (reverse dependency order).
    op.drop_table("pipeline_runs")
    op.drop_table("pages")
    op.drop_table("documents")
    op.drop_table("patients")
    op.execute("DROP EXTENSION IF EXISTS vector")
