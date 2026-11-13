"""Reference data, extracted facts (joined inheritance), review and conflicts

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-13

Frozen migration (Task 1.7): literal names and values only; it must never import application
code, so later model changes cannot rewrite history. Every CHECK, foreign key and index here is
mirrored by app/records/models.py, and an integration test compares the two catalogs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analyte_catalog",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("loinc_code", sa.Text(), nullable=True),
        sa.Column("default_unit", sa.Text(), nullable=False),
        sa.Column("plausible_min", sa.Numeric(), nullable=True),
        sa.Column("plausible_max", sa.Numeric(), nullable=True),
        sa.CheckConstraint(
            "plausible_min IS NULL OR plausible_max IS NULL OR plausible_min < plausible_max",
            name=op.f("ck_analyte_catalog_plausible_range"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analyte_catalog")),
        sa.UniqueConstraint("canonical_name", name=op.f("uq_analyte_catalog_canonical_name")),
        sa.UniqueConstraint("loinc_code", name=op.f("uq_analyte_catalog_loinc_code")),
    )
    op.create_table(
        "analyte_aliases",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("analyte_id", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "alias = lower(btrim(alias))", name=op.f("ck_analyte_aliases_alias_normalized")
        ),
        sa.ForeignKeyConstraint(
            ["analyte_id"],
            ["analyte_catalog.id"],
            name=op.f("fk_analyte_aliases_analyte_id_analyte_catalog"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analyte_aliases")),
        sa.UniqueConstraint("alias", name=op.f("uq_analyte_aliases_alias")),
    )
    op.create_index(
        "ix_analyte_aliases_analyte_id", "analyte_aliases", ["analyte_id"], unique=False
    )
    op.create_table(
        "unit_conversions",
        sa.Column("analyte_id", sa.Integer(), nullable=False),
        sa.Column("from_unit", sa.Text(), nullable=False),
        sa.Column("to_unit", sa.Text(), nullable=False),
        sa.Column("factor", sa.Numeric(), nullable=False),
        sa.Column("addend", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("factor <> 0", name=op.f("ck_unit_conversions_factor_nonzero")),
        sa.CheckConstraint(
            "from_unit <> to_unit", name=op.f("ck_unit_conversions_different_units")
        ),
        sa.ForeignKeyConstraint(
            ["analyte_id"],
            ["analyte_catalog.id"],
            name=op.f("fk_unit_conversions_analyte_id_analyte_catalog"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "analyte_id", "from_unit", "to_unit", name=op.f("pk_unit_conversions")
        ),
    )
    op.create_table(
        "extracted_facts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("fact_type", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("source_excerpt", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("bbox", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("assertion_type", sa.Text(), nullable=False),
        sa.Column("negated", sa.Boolean(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column(
            "review_status", sa.Text(), server_default=sa.text("'extracted'"), nullable=False
        ),
        sa.Column(
            "review_reasons",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("duplicate_group_id", sa.Uuid(), nullable=True),
        sa.Column("extractor_version", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "assertion_type IN ('direct_fact', 'inference', 'unknown')",
            name=op.f("ck_extracted_facts_assertion_type_values"),
        ),
        sa.CheckConstraint(
            "fact_type IN ('observation', 'medication', 'allergy', 'event')",
            name=op.f("ck_extracted_facts_fact_type_values"),
        ),
        sa.CheckConstraint(
            "review_status <> 'needs_review' OR cardinality(review_reasons) > 0",
            name=op.f("ck_extracted_facts_needs_review_has_reasons"),
        ),
        sa.CheckConstraint(
            "review_status IN ('extracted', 'validated', 'needs_review', 'verified', 'corrected', 'rejected')",
            name=op.f("ck_extracted_facts_review_status_values"),
        ),
        sa.CheckConstraint(
            "subject IN ('patient', 'family', 'other')",
            name=op.f("ck_extracted_facts_subject_values"),
        ),
        sa.CheckConstraint(
            "(char_start IS NULL) = (char_end IS NULL)",
            name=op.f("ck_extracted_facts_char_span_pair"),
        ),
        sa.CheckConstraint(
            "char_start IS NULL OR (char_start >= 0 AND char_start <= char_end)",
            name=op.f("ck_extracted_facts_char_span_order"),
        ),
        sa.CheckConstraint(
            "length(btrim(source_excerpt)) > 0",
            name=op.f("ck_extracted_facts_source_excerpt_present"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "page_number"],
            ["pages.document_id", "pages.page_number"],
            name=op.f("fk_extracted_facts_document_id_page_number_pages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "patient_id"],
            ["documents.id", "documents.patient_id"],
            name=op.f("fk_extracted_facts_document_id_patient_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extracted_facts")),
        sa.UniqueConstraint("id", "patient_id", name=op.f("uq_extracted_facts_id_patient_id")),
    )
    op.create_index(
        "ix_extracted_facts_document_id_page_number",
        "extracted_facts",
        ["document_id", "page_number"],
        unique=False,
    )
    op.create_index(
        "ix_extracted_facts_document_id_patient_id",
        "extracted_facts",
        ["document_id", "patient_id"],
        unique=False,
    )
    op.create_index(
        "ix_extracted_facts_duplicate_group_id",
        "extracted_facts",
        ["duplicate_group_id"],
        unique=False,
        postgresql_where=sa.text("duplicate_group_id IS NOT NULL"),
    )
    op.create_index(
        "ix_extracted_facts_patient_id_fact_type_review_status",
        "extracted_facts",
        ["patient_id", "fact_type", "review_status"],
        unique=False,
    )
    op.create_table(
        "observations",
        sa.Column("fact_id", sa.Uuid(), nullable=False),
        sa.Column("analyte_raw", sa.Text(), nullable=False),
        sa.Column("analyte_id", sa.Integer(), nullable=True),
        sa.Column("value_raw", sa.Text(), nullable=False),
        sa.Column("value_num", sa.Numeric(), nullable=True),
        sa.Column("comparator", sa.Text(), nullable=True),
        sa.Column("value_qualitative", sa.Text(), nullable=True),
        sa.Column("unit_raw", sa.Text(), nullable=True),
        sa.Column("unit_norm", sa.Text(), nullable=True),
        sa.Column("value_num_norm", sa.Numeric(), nullable=True),
        sa.Column("ref_range_raw", sa.Text(), nullable=True),
        sa.Column("ref_low", sa.Numeric(), nullable=True),
        sa.Column("ref_high", sa.Numeric(), nullable=True),
        sa.Column("flag_as_printed", sa.Text(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("date_precision", sa.Text(), nullable=True),
        sa.Column("date_raw", sa.Text(), nullable=True),
        sa.Column("specimen", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "comparator IN ('<', '<=', '>', '>=')", name=op.f("ck_observations_comparator_values")
        ),
        sa.CheckConstraint(
            "date_precision IN ('day', 'month', 'year')",
            name=op.f("ck_observations_effective_date_precision_values"),
        ),
        sa.CheckConstraint(
            "date_precision IS NULL OR date_precision = 'day' OR (date_precision = 'month' AND extract(day FROM effective_date) = 1) OR (date_precision = 'year' AND extract(month FROM effective_date) = 1 AND extract(day FROM effective_date) = 1)",
            name=op.f("ck_observations_effective_date_normalized"),
        ),
        sa.CheckConstraint(
            "(effective_date IS NULL) = (date_precision IS NULL)",
            name=op.f("ck_observations_effective_date_pair"),
        ),
        sa.CheckConstraint(
            "comparator IS NULL OR value_num IS NOT NULL",
            name=op.f("ck_observations_comparator_number"),
        ),
        sa.CheckConstraint(
            "ref_low IS NULL OR ref_high IS NULL OR ref_low <= ref_high",
            name=op.f("ck_observations_ref_range_order"),
        ),
        sa.CheckConstraint(
            "value_num_norm IS NULL OR unit_norm IS NOT NULL",
            name=op.f("ck_observations_normalized_value_unit"),
        ),
        sa.ForeignKeyConstraint(
            ["analyte_id"],
            ["analyte_catalog.id"],
            name=op.f("fk_observations_analyte_id_analyte_catalog"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fact_id"],
            ["extracted_facts.id"],
            name=op.f("fk_observations_fact_id_extracted_facts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("fact_id", name=op.f("pk_observations")),
    )
    op.create_index("ix_observations_analyte_id", "observations", ["analyte_id"], unique=False)
    op.create_table(
        "medication_mentions",
        sa.Column("fact_id", sa.Uuid(), nullable=False),
        sa.Column("name_raw", sa.Text(), nullable=False),
        sa.Column("name_norm", sa.Text(), nullable=True),
        sa.Column("strength_raw", sa.Text(), nullable=True),
        sa.Column("dose_raw", sa.Text(), nullable=True),
        sa.Column("route_raw", sa.Text(), nullable=True),
        sa.Column("frequency_raw", sa.Text(), nullable=True),
        sa.Column("duration_raw", sa.Text(), nullable=True),
        sa.Column("instructions_raw", sa.Text(), nullable=True),
        sa.Column("mention_context", sa.Text(), nullable=False),
        sa.Column("mention_date", sa.Date(), nullable=True),
        sa.Column("date_precision", sa.Text(), nullable=True),
        sa.Column("date_raw", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "date_precision IN ('day', 'month', 'year')",
            name=op.f("ck_medication_mentions_mention_date_precision_values"),
        ),
        sa.CheckConstraint(
            "date_precision IS NULL OR date_precision = 'day' OR (date_precision = 'month' AND extract(day FROM mention_date) = 1) OR (date_precision = 'year' AND extract(month FROM mention_date) = 1 AND extract(day FROM mention_date) = 1)",
            name=op.f("ck_medication_mentions_mention_date_normalized"),
        ),
        sa.CheckConstraint(
            "mention_context IN ('prescribed', 'discharge_list', 'history', 'discontinued', 'other')",
            name=op.f("ck_medication_mentions_mention_context_values"),
        ),
        sa.CheckConstraint(
            "(mention_date IS NULL) = (date_precision IS NULL)",
            name=op.f("ck_medication_mentions_mention_date_pair"),
        ),
        sa.ForeignKeyConstraint(
            ["fact_id"],
            ["extracted_facts.id"],
            name=op.f("fk_medication_mentions_fact_id_extracted_facts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("fact_id", name=op.f("pk_medication_mentions")),
    )
    op.create_table(
        "allergy_mentions",
        sa.Column("fact_id", sa.Uuid(), nullable=False),
        sa.Column("substance_raw", sa.Text(), nullable=False),
        sa.Column("reaction_raw", sa.Text(), nullable=True),
        sa.Column("severity_raw", sa.Text(), nullable=True),
        sa.Column("mention_date", sa.Date(), nullable=True),
        sa.Column("date_precision", sa.Text(), nullable=True),
        sa.Column("date_raw", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "date_precision IN ('day', 'month', 'year')",
            name=op.f("ck_allergy_mentions_mention_date_precision_values"),
        ),
        sa.CheckConstraint(
            "date_precision IS NULL OR date_precision = 'day' OR (date_precision = 'month' AND extract(day FROM mention_date) = 1) OR (date_precision = 'year' AND extract(month FROM mention_date) = 1 AND extract(day FROM mention_date) = 1)",
            name=op.f("ck_allergy_mentions_mention_date_normalized"),
        ),
        sa.CheckConstraint(
            "(mention_date IS NULL) = (date_precision IS NULL)",
            name=op.f("ck_allergy_mentions_mention_date_pair"),
        ),
        sa.ForeignKeyConstraint(
            ["fact_id"],
            ["extracted_facts.id"],
            name=op.f("fk_allergy_mentions_fact_id_extracted_facts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("fact_id", name=op.f("pk_allergy_mentions")),
    )
    op.create_table(
        "clinical_events",
        sa.Column("fact_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("description_raw", sa.Text(), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("date_precision", sa.Text(), nullable=True),
        sa.Column("date_raw", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "date_precision IN ('day', 'month', 'year')",
            name=op.f("ck_clinical_events_event_date_precision_values"),
        ),
        sa.CheckConstraint(
            "date_precision IS NULL OR date_precision = 'day' OR (date_precision = 'month' AND extract(day FROM event_date) = 1) OR (date_precision = 'year' AND extract(month FROM event_date) = 1 AND extract(day FROM event_date) = 1)",
            name=op.f("ck_clinical_events_event_date_normalized"),
        ),
        sa.CheckConstraint(
            "event_type IN ('admission', 'discharge', 'procedure', 'consultation', 'diagnosis_documented', 'follow_up_instruction', 'other')",
            name=op.f("ck_clinical_events_event_type_values"),
        ),
        sa.CheckConstraint(
            "(event_date IS NULL) = (date_precision IS NULL)",
            name=op.f("ck_clinical_events_event_date_pair"),
        ),
        sa.ForeignKeyConstraint(
            ["fact_id"],
            ["extracted_facts.id"],
            name=op.f("fk_clinical_events_fact_id_extracted_facts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("fact_id", name=op.f("pk_clinical_events")),
    )
    op.create_table(
        "review_decisions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("fact_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("corrected_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(decision = 'correct') = (corrected_values IS NOT NULL)",
            name=op.f("ck_review_decisions_corrected_values_match"),
        ),
        sa.CheckConstraint(
            "decision IN ('verify', 'correct', 'reject')",
            name=op.f("ck_review_decisions_decision_values"),
        ),
        sa.ForeignKeyConstraint(
            ["fact_id", "patient_id"],
            ["extracted_facts.id", "extracted_facts.patient_id"],
            name=op.f("fk_review_decisions_fact_id_patient_id_extracted_facts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_decisions")),
    )
    op.create_index(
        "ix_review_decisions_fact_id_patient_id",
        "review_decisions",
        ["fact_id", "patient_id"],
        unique=False,
    )
    op.create_table(
        "conflicts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("conflict_type", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'open'"), nullable=False),
        sa.Column("detected_by_rule", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "conflict_type IN ('medication_dose_mismatch', 'allergy_contradiction', 'value_mismatch_same_date', 'other')",
            name=op.f("ck_conflicts_conflict_type_values"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'acknowledged')", name=op.f("ck_conflicts_status_values")
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_conflicts_patient_id_patients"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conflicts")),
        sa.UniqueConstraint("id", "patient_id", name=op.f("uq_conflicts_id_patient_id")),
    )
    op.create_index(
        "ix_conflicts_patient_id_status", "conflicts", ["patient_id", "status"], unique=False
    )
    op.create_table(
        "conflict_members",
        sa.Column("conflict_id", sa.Uuid(), nullable=False),
        sa.Column("fact_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conflict_id", "patient_id"],
            ["conflicts.id", "conflicts.patient_id"],
            name=op.f("fk_conflict_members_conflict_id_patient_id_conflicts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["fact_id", "patient_id"],
            ["extracted_facts.id", "extracted_facts.patient_id"],
            name=op.f("fk_conflict_members_fact_id_patient_id_extracted_facts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("conflict_id", "fact_id", name=op.f("pk_conflict_members")),
    )
    op.create_index(
        "ix_conflict_members_fact_id_patient_id",
        "conflict_members",
        ["fact_id", "patient_id"],
        unique=False,
    )


def downgrade() -> None:
    # Destroys data: every table of this revision is dropped (reverse dependency order).
    op.drop_table("conflict_members")
    op.drop_table("conflicts")
    op.drop_table("review_decisions")
    op.drop_table("clinical_events")
    op.drop_table("allergy_mentions")
    op.drop_table("medication_mentions")
    op.drop_table("observations")
    op.drop_table("extracted_facts")
    op.drop_table("unit_conversions")
    op.drop_table("analyte_aliases")
    op.drop_table("analyte_catalog")
