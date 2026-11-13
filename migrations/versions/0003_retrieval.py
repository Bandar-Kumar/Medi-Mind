"""Retrieval: chunks with vector(384) embedding and generated tsvector

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-13

Frozen migration (Task 1.7): literal names and values only; it must never import application
code, so later model changes cannot rewrite history. Every CHECK, foreign key and index here is
mirrored by app/records/models.py, and an integration test compares the two catalogs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("chunk_type", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple'::regconfig, content)", persisted=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "chunk_type IN ('text', 'table_rows')", name=op.f("ck_chunks_chunk_type_values")
        ),
        sa.CheckConstraint(
            "(embedding IS NULL) = (embedding_model IS NULL)",
            name=op.f("ck_chunks_embedding_model_pair"),
        ),
        sa.CheckConstraint("chunk_index >= 0", name=op.f("ck_chunks_chunk_index_positive")),
        sa.ForeignKeyConstraint(
            ["document_id", "page_number"],
            ["pages.document_id", "pages.page_number"],
            name=op.f("fk_chunks_document_id_page_number_pages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "patient_id"],
            ["documents.id", "documents.patient_id"],
            name=op.f("fk_chunks_document_id_patient_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.UniqueConstraint(
            "document_id", "chunk_index", name=op.f("uq_chunks_document_id_chunk_index")
        ),
    )
    op.create_index(
        "ix_chunks_document_id_page_number", "chunks", ["document_id", "page_number"], unique=False
    )
    op.create_index(
        "ix_chunks_document_id_patient_id", "chunks", ["document_id", "patient_id"], unique=False
    )
    op.create_index("ix_chunks_patient_id", "chunks", ["patient_id"], unique=False)
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], unique=False, postgresql_using="gin")


def downgrade() -> None:
    # Destroys data: every table of this revision is dropped (reverse dependency order).
    op.drop_table("chunks")
