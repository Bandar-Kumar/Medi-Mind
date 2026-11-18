"""Phase 6 clinical facts: Phase 6 document type, normalized medication fields, allergy status,
event context, and every clinical date's source and exact span

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-18

Frozen once accepted (Phase 6, docs/PHASE_6_PLAN.md §4; ADR-042): literal names and values only;
it never imports application code. Every CHECK here is mirrored by app/records/models.py, and the
integration tests compare the two catalogs.

Additive only: new nullable columns on ``documents``; new columns and checks on the three clinical
subtype tables; the ``clinical_events.event_type`` check is widened (``imaging``,
``symptom_documented``), never narrowed. No existing row or value changes.

Precondition: ``medication_mentions``, ``allergy_mentions`` and ``clinical_events`` must be empty
(no code wrote them before Phase 6; ``allergy_status`` and ``event_context`` are NOT NULL with no
truthful backfill). The upgrade raises otherwise, inside its transaction, so nothing changes.

Downgrade refuses while any clinical fact or any ``clinical_doc_type`` exists: it never drops
Phase 6 data silently. (It also needs the database-name confirmation, ADR-034 guard.)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUBTYPES = ("medication_mentions", "allergy_mentions", "clinical_events")
DATE_COLUMN = {
    "medication_mentions": "mention_date",
    "allergy_mentions": "mention_date",
    "clinical_events": "event_date",
}
OLD_EVENT_TYPES = (
    "event_type IN ('admission', 'discharge', 'procedure', 'consultation', "
    "'diagnosis_documented', 'follow_up_instruction', 'other')"
)
NEW_EVENT_TYPES = (
    "event_type IN ('admission', 'discharge', 'procedure', 'consultation', "
    "'diagnosis_documented', 'follow_up_instruction', 'other', 'imaging', 'symptom_documented')"
)


def _date_span_checks(table: str) -> list[tuple[str, str]]:
    date = DATE_COLUMN[table]
    return [
        (f"ck_{table}_date_source_values", "date_source IN ('document', 'stated')"),
        (f"ck_{table}_date_source_pair", f"({date} IS NULL) = (date_source IS NULL)"),
        (
            f"ck_{table}_date_span_all_or_none",
            "(date_page_number IS NULL) = (date_char_start IS NULL) "
            "AND (date_char_start IS NULL) = (date_char_end IS NULL)",
        ),
        (
            f"ck_{table}_date_span_order",
            "date_char_start IS NULL OR (date_page_number >= 1 AND date_char_start >= 0 "
            "AND date_char_start <= date_char_end)",
        ),
        (f"ck_{table}_date_span_required", f"{date} IS NULL OR date_char_start IS NOT NULL"),
    ]


MEDICATION_CHECKS = [
    ("ck_medication_mentions_name_match_values", "name_match IN ('generic_exact', 'brand_alias')"),
    ("ck_medication_mentions_name_match_pair", "(name_norm IS NULL) = (name_match IS NULL)"),
    (
        "ck_medication_mentions_dosage_form_values",
        "dosage_form IN ('tablet', 'capsule', 'syrup', 'injection')",
    ),
    (
        "ck_medication_mentions_strength_pair",
        "(strength_value IS NULL) = (strength_unit IS NULL)",
    ),
    ("ck_medication_mentions_strength_positive", "strength_value IS NULL OR strength_value > 0"),
    (
        "ck_medication_mentions_route_norm_values",
        "route_norm IN ('oral', 'intravenous', 'intramuscular', 'subcutaneous', 'topical', "
        "'inhaled')",
    ),
    (
        "ck_medication_mentions_frequency_per_day_range",
        "frequency_per_day IS NULL OR frequency_per_day BETWEEN 1 AND 24",
    ),
    (
        "ck_medication_mentions_duration_pair",
        "(duration_value IS NULL) = (duration_unit IS NULL)",
    ),
    ("ck_medication_mentions_duration_positive", "duration_value IS NULL OR duration_value > 0"),
    (
        "ck_medication_mentions_duration_unit_values",
        "duration_unit IN ('day', 'week', 'month')",
    ),
]
ALLERGY_CHECKS = [
    (
        "ck_allergy_mentions_allergy_status_values",
        "allergy_status IN ('confirmed', 'suspected', 'none_known')",
    ),
    (
        "ck_allergy_mentions_substance_norm_matches_status",
        "(allergy_status = 'none_known') = (substance_norm IS NULL)",
    ),
    (
        "ck_allergy_mentions_severity_norm_values",
        "severity_norm IN ('mild', 'moderate', 'severe')",
    ),
]
EVENT_CHECKS = [
    ("ck_clinical_events_event_context_values", "event_context IN ('encounter', 'history')"),
]
DOCUMENT_CHECK = (
    "ck_documents_clinical_doc_type_values",
    "clinical_doc_type IN ('prescription', 'discharge_summary', 'consultation_note', "
    "'history_record', 'allergy_record', 'imaging_report')",
)


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM medication_mentions) OR EXISTS (SELECT 1 FROM allergy_mentions) "
        "OR EXISTS (SELECT 1 FROM clinical_events) THEN "
        "RAISE EXCEPTION 'migration 0005 requires empty medication_mentions, allergy_mentions "
        "and clinical_events tables'; "
        "END IF; END $$"
    )

    op.add_column("documents", sa.Column("clinical_doc_type", sa.Text(), nullable=True))
    op.create_check_constraint(op.f(DOCUMENT_CHECK[0]), "documents", DOCUMENT_CHECK[1])

    for table in SUBTYPES:
        op.add_column(table, sa.Column("date_source", sa.Text(), nullable=True))
        op.add_column(table, sa.Column("date_page_number", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("date_char_start", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("date_char_end", sa.Integer(), nullable=True))

    op.add_column("medication_mentions", sa.Column("name_match", sa.Text(), nullable=True))
    op.add_column("medication_mentions", sa.Column("dosage_form", sa.Text(), nullable=True))
    op.add_column("medication_mentions", sa.Column("strength_value", sa.Numeric(), nullable=True))
    op.add_column("medication_mentions", sa.Column("strength_unit", sa.Text(), nullable=True))
    op.add_column("medication_mentions", sa.Column("route_norm", sa.Text(), nullable=True))
    op.add_column(
        "medication_mentions", sa.Column("frequency_per_day", sa.Integer(), nullable=True)
    )
    op.add_column("medication_mentions", sa.Column("as_needed", sa.Boolean(), nullable=True))
    op.add_column("medication_mentions", sa.Column("duration_value", sa.Integer(), nullable=True))
    op.add_column("medication_mentions", sa.Column("duration_unit", sa.Text(), nullable=True))

    op.add_column("allergy_mentions", sa.Column("allergy_status", sa.Text(), nullable=False))
    op.add_column("allergy_mentions", sa.Column("substance_norm", sa.Text(), nullable=True))
    op.add_column("allergy_mentions", sa.Column("severity_norm", sa.Text(), nullable=True))

    op.add_column("clinical_events", sa.Column("event_context", sa.Text(), nullable=False))

    for table in SUBTYPES:
        for name, condition in _date_span_checks(table):
            op.create_check_constraint(op.f(name), table, condition)
    for name, condition in MEDICATION_CHECKS:
        op.create_check_constraint(op.f(name), "medication_mentions", condition)
    for name, condition in ALLERGY_CHECKS:
        op.create_check_constraint(op.f(name), "allergy_mentions", condition)
    for name, condition in EVENT_CHECKS:
        op.create_check_constraint(op.f(name), "clinical_events", condition)

    # Widen (never narrow) the event types.
    op.drop_constraint(
        op.f("ck_clinical_events_event_type_values"), "clinical_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_clinical_events_event_type_values"), "clinical_events", NEW_EVENT_TYPES
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM medication_mentions) OR EXISTS (SELECT 1 FROM allergy_mentions) "
        "OR EXISTS (SELECT 1 FROM clinical_events) "
        "OR EXISTS (SELECT 1 FROM documents WHERE clinical_doc_type IS NOT NULL) THEN "
        "RAISE EXCEPTION 'migration 0005 downgrade refused: Phase 6 clinical data exists'; "
        "END IF; END $$"
    )
    op.drop_constraint(
        op.f("ck_clinical_events_event_type_values"), "clinical_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_clinical_events_event_type_values"), "clinical_events", OLD_EVENT_TYPES
    )
    for name, _ in reversed(EVENT_CHECKS):
        op.drop_constraint(op.f(name), "clinical_events", type_="check")
    for name, _ in reversed(ALLERGY_CHECKS):
        op.drop_constraint(op.f(name), "allergy_mentions", type_="check")
    for name, _ in reversed(MEDICATION_CHECKS):
        op.drop_constraint(op.f(name), "medication_mentions", type_="check")
    for table in reversed(SUBTYPES):
        for name, _ in reversed(_date_span_checks(table)):
            op.drop_constraint(op.f(name), table, type_="check")

    op.drop_column("clinical_events", "event_context")
    for column in ("severity_norm", "substance_norm", "allergy_status"):
        op.drop_column("allergy_mentions", column)
    for column in (
        "duration_unit",
        "duration_value",
        "as_needed",
        "frequency_per_day",
        "route_norm",
        "strength_unit",
        "strength_value",
        "dosage_form",
        "name_match",
    ):
        op.drop_column("medication_mentions", column)
    for table in reversed(SUBTYPES):
        for column in ("date_char_end", "date_char_start", "date_page_number", "date_source"):
            op.drop_column(table, column)

    op.drop_constraint(op.f(DOCUMENT_CHECK[0]), "documents", type_="check")
    op.drop_column("documents", "clinical_doc_type")
