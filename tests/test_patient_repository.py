"""Unit tests for the patient repository (Tasks 2.6a, 1.8a). No database is used.

Validation must reject bad input before any session exists: ``_no_database`` fails the test if a
session factory is ever called. The happy path uses a small fake session that records what would
be inserted. All names are fictional.
"""

import datetime as dt
import logging
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.errors import PatientError
from app.core.logging import JsonFormatter
from app.records import repositories as repo
from app.records.models import Document, Patient

NAME = "Asha Fictional"
ZWJ = chr(0x200D)
ZWNJ = chr(0x200C)
LRM = chr(0x200E)
RLM = chr(0x200F)
ZWSP = chr(0x200B)
BOM = chr(0xFEFF)
BIDI_CONTROLS = [chr(c) for c in (0x202A, 0x202B, 0x202C, 0x202D, 0x202E)] + [
    chr(c) for c in (0x2066, 0x2067, 0x2068, 0x2069)
]


def _no_database():
    raise AssertionError("validation must fail before a database session is created")


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Patient] = []
        self.committed = False

    def add(self, obj: Patient) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        for obj in self.added:
            obj.id = uuid.uuid4()

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


def _create(session: FakeSession, name: str = NAME, **kw) -> repo.PatientRef:
    kw.setdefault("is_synthetic", True)
    return repo.create_patient(name, factory=lambda: session, **kw)


def _rejected(kind: str, name: object = NAME, **kw) -> PatientError:
    kw.setdefault("is_synthetic", True)
    with pytest.raises(PatientError) as caught:
        repo.create_patient(name, factory=_no_database, **kw)
    assert caught.value.kind == kind
    return caught.value


# --- display name --------------------------------------------------------------------------------


def test_valid_patient_is_inserted_with_surrounding_whitespace_stripped(fake_session) -> None:
    born = dt.date(1980, 5, 17)
    ref = _create(fake_session, f" \t{NAME}\n ", date_of_birth=born)
    [patient] = fake_session.added
    assert patient.display_name == NAME
    assert patient.date_of_birth == born and patient.is_synthetic is True
    assert fake_session.committed and ref == repo.PatientRef(id=patient.id, is_synthetic=True)


def test_patient_ref_does_not_carry_the_display_name(fake_session) -> None:
    ref = _create(fake_session)
    assert NAME not in repr(ref)
    assert not hasattr(ref, "display_name")


@pytest.mark.parametrize(
    "name",
    [
        f"Ra{ZWJ}m",  # zero-width joiner (Indic conjuncts)
        f"Ra{ZWNJ}m",  # zero-width non-joiner
        f"Name {LRM}x",  # a simple direction mark is not an embedding/override/isolate
        f"Name {RLM}x",
        "Zoë Ñandú",
        "क्षमा फ़िक्शनल",
        chr(0x1D707) * repo.MAX_DISPLAY_NAME_CHARS,  # supplementary characters count as one each
    ],
)
def test_legitimate_names_are_stored_exactly_without_normalization(fake_session, name) -> None:
    _create(fake_session, name)
    assert fake_session.added[0].display_name == name


def test_combining_sequences_are_not_normalized(fake_session) -> None:
    decomposed = "Zoe" + chr(0x0308)  # e + combining diaeresis; NFC would make one character
    _create(fake_session, decomposed)
    assert fake_session.added[0].display_name == decomposed


def test_length_limit_is_200_characters_after_stripping(fake_session) -> None:
    _create(fake_session, "  " + "a" * 200 + "  ")
    assert len(fake_session.added[0].display_name) == 200
    error = _rejected(repo.INVALID_DISPLAY_NAME, "a" * 201)
    assert "200" in str(error)


@pytest.mark.parametrize("name", ["", " ", " \t\n ", chr(0x3000)])
def test_empty_or_whitespace_only_names_are_rejected(name) -> None:
    _rejected(repo.INVALID_DISPLAY_NAME, name)


@pytest.mark.parametrize("char", [chr(0), "\t", "\n", "\r", chr(0x7F), chr(0x85), chr(0x9B)])
def test_control_characters_inside_the_name_are_rejected(char) -> None:
    _rejected(repo.INVALID_DISPLAY_NAME, f"Asha{char}Fictional")


@pytest.mark.parametrize("char", [chr(0xD800), chr(0xDBFF), chr(0xDC00), chr(0xDFFF)])
def test_lone_surrogates_are_rejected(char) -> None:
    _rejected(repo.INVALID_DISPLAY_NAME, f"Asha{char}")


@pytest.mark.parametrize("char", BIDI_CONTROLS)
def test_bidi_embedding_override_and_isolate_controls_are_rejected(char) -> None:
    _rejected(repo.INVALID_DISPLAY_NAME, f"Asha{char}lanoitciF")


@pytest.mark.parametrize("char", [ZWSP, BOM])
@pytest.mark.parametrize("template", ["Asha{}Fictional", "{}Asha Fictional", "Asha Fictional{}"])
def test_zero_width_space_and_bom_are_rejected_anywhere(char, template) -> None:
    assert char.strip() == char  # not whitespace: stripping never removes them
    _rejected(repo.INVALID_DISPLAY_NAME, template.format(char))


def test_only_the_two_invisible_characters_are_added_not_all_format_characters() -> None:
    assert repo._INVISIBLE_CHARACTERS == {ZWSP, BOM}
    assert {ZWJ, ZWNJ, LRM, RLM}.isdisjoint(repo._REJECTED_NAME_CHARACTERS)


@pytest.mark.parametrize("name", [None, 42, b"Asha", ["Asha"]])
def test_non_text_names_are_rejected(name) -> None:
    _rejected(repo.INVALID_DISPLAY_NAME, name)


def test_errors_never_echo_the_display_name() -> None:
    secret = "Secret Fictional Person"
    for bad in (secret + chr(0), secret + chr(0x202E), secret * 20):
        error = _rejected(repo.INVALID_DISPLAY_NAME, bad)
        assert "Secret" not in str(error) and "Secret" not in repr(error.args)
        assert error.__cause__ is None and error.__context__ is None


# --- date of birth and is_synthetic -------------------------------------------------------------


def test_date_of_birth_may_be_omitted_or_today(fake_session) -> None:
    _create(fake_session)
    _create(fake_session, date_of_birth=dt.date.today())
    assert [p.date_of_birth for p in fake_session.added] == [None, dt.date.today()]


@pytest.mark.parametrize(
    "value",
    [
        dt.date.today() + dt.timedelta(days=1),
        dt.datetime(1980, 5, 17, 10, 30),  # a datetime is a date subclass: still rejected
        "1980-05-17",
        19800517,
    ],
)
def test_invalid_dates_of_birth_are_rejected_without_echoing_them(value) -> None:
    error = _rejected(repo.INVALID_DATE_OF_BIRTH, date_of_birth=value)
    assert "1980" not in str(error) and str(dt.date.today().year + 1) not in str(error)


@pytest.mark.parametrize("value", [None, 1, 0, "true"])
def test_is_synthetic_must_be_a_bool(value) -> None:
    with pytest.raises(TypeError):
        repo.create_patient(NAME, is_synthetic=value, factory=_no_database)


def test_is_synthetic_has_no_default() -> None:
    with pytest.raises(TypeError):
        repo.create_patient(NAME, factory=_no_database)  # type: ignore[call-arg]


def test_real_bool_false_is_accepted(fake_session) -> None:
    assert _create(fake_session, is_synthetic=False).is_synthetic is False


def test_creation_log_holds_the_identifier_only(fake_session, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    ref = _create(fake_session, date_of_birth=dt.date(1980, 5, 17))
    assert str(ref.id) in caplog.text
    assert "Asha" not in caplog.text and "1980" not in caplog.text


# --- patient foreign-key mapping in create_document ----------------------------------------------


def test_fk_constant_matches_the_documents_patient_foreign_key() -> None:
    [fk] = [
        c for c in Document.__table__.foreign_key_constraints if c.referred_table.name == "patients"
    ]
    assert fk.column_keys == ["patient_id"]
    assert fk.name == repo._DOCUMENT_PATIENT_FK


def _integrity_error(sqlstate: str | None, constraint: str | None) -> IntegrityError:
    orig = SimpleNamespace(sqlstate=sqlstate, diag=SimpleNamespace(constraint_name=constraint))
    return IntegrityError("INSERT ...", {}, orig)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("sqlstate", "constraint", "expected"),
    [
        ("23503", "fk_documents_patient_id_patients", True),
        ("23503", "fk_documents_supersedes_document_id_patient_id_documents", False),
        ("23503", None, False),
        ("23505", "fk_documents_patient_id_patients", False),
        ("23505", "uq_documents_patient_id_sha256", False),
        (None, None, False),
    ],
)
def test_only_the_patient_foreign_key_violation_is_mapped(sqlstate, constraint, expected) -> None:
    assert repo._is_patient_fk_violation(_integrity_error(sqlstate, constraint)) is expected


# --- update_patient and list_patients (Task 1.8a) ------------------------------------------------

PATIENT_ID = uuid.uuid4()
CREATED = dt.datetime(2026, 11, 16, 9, 30, tzinfo=dt.UTC)


class StatementSession:
    """Records executed statements and returns a fixed result. No database."""

    def __init__(self, result: object) -> None:
        self.result = result
        self.statements: list = []
        self.committed = False

    def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(one_or_none=lambda: self.result, all=lambda: self.result)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


def _returned(is_synthetic: bool = True) -> SimpleNamespace:
    return SimpleNamespace(id=PATIENT_ID, is_synthetic=is_synthetic)


def _update(session: StatementSession, **kw) -> repo.PatientRef:
    return repo.update_patient(PATIENT_ID, factory=lambda: session, **kw)


def _set_values(session: StatementSession) -> dict:
    [statement] = session.statements
    params = statement.compile().params
    return {k: v for k, v in params.items() if k in ("display_name", "date_of_birth")}


def test_update_sets_only_the_given_field_and_returns_the_ref() -> None:
    session = StatementSession(_returned())
    ref = _update(session, display_name=f"  {NAME} ")
    assert _set_values(session) == {"display_name": NAME}  # stripped, date untouched
    assert ref == repo.PatientRef(id=PATIENT_ID, is_synthetic=True) and session.committed


def test_none_clears_the_date_of_birth_and_omitting_it_keeps_it() -> None:
    cleared = StatementSession(_returned())
    _update(cleared, date_of_birth=None)
    assert _set_values(cleared) == {"date_of_birth": None}
    both = StatementSession(_returned())
    _update(both, display_name=NAME, date_of_birth=dt.date(1980, 5, 17))
    assert _set_values(both) == {"display_name": NAME, "date_of_birth": dt.date(1980, 5, 17)}


def test_an_update_with_nothing_to_change_never_opens_a_session() -> None:
    with pytest.raises(ValueError):
        repo.update_patient(PATIENT_ID, factory=_no_database)


@pytest.mark.parametrize(
    ("kw", "kind"),
    [
        ({"display_name": ""}, repo.INVALID_DISPLAY_NAME),
        ({"display_name": "a" * 201}, repo.INVALID_DISPLAY_NAME),
        ({"display_name": "Asha" + chr(0) + "Fictional"}, repo.INVALID_DISPLAY_NAME),
        ({"display_name": "Asha" + chr(0x202E) + "Fictional"}, repo.INVALID_DISPLAY_NAME),
        ({"display_name": ZWSP + NAME}, repo.INVALID_DISPLAY_NAME),
        ({"display_name": None}, repo.INVALID_DISPLAY_NAME),  # a name cannot be cleared
        ({"date_of_birth": dt.date.today() + dt.timedelta(days=1)}, repo.INVALID_DATE_OF_BIRTH),
        ({"date_of_birth": dt.datetime(1980, 5, 17, 10, 30)}, repo.INVALID_DATE_OF_BIRTH),
        ({"date_of_birth": "1980-05-17"}, repo.INVALID_DATE_OF_BIRTH),
        ({"display_name": NAME, "date_of_birth": "1980-05-17"}, repo.INVALID_DATE_OF_BIRTH),
    ],
)
def test_invalid_updates_are_rejected_before_the_database(kw, kind) -> None:
    with pytest.raises(PatientError) as caught:
        repo.update_patient(PATIENT_ID, factory=_no_database, **kw)
    error = caught.value
    assert error.kind == kind
    assert "Asha" not in str(error) and "1980" not in str(error)
    assert error.__cause__ is None and error.__context__ is None


@pytest.mark.parametrize("field", ["is_synthetic", "id", "created_at"])
def test_immutable_fields_are_not_parameters(field) -> None:
    with pytest.raises(TypeError):
        repo.update_patient(PATIENT_ID, factory=_no_database, **{field: True})


def test_updating_an_unknown_patient_is_patient_not_found_and_not_committed() -> None:
    session = StatementSession(None)
    with pytest.raises(PatientError) as caught:
        _update(session, display_name=NAME)
    assert caught.value.kind == repo.PATIENT_NOT_FOUND and not session.committed
    assert str(PATIENT_ID) in str(caught.value) and "Asha" not in str(caught.value)


def _summary_rows() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            id=uuid.uuid4(),
            display_name=name,
            date_of_birth=born,
            is_synthetic=True,
            created_at=CREATED,
        )
        for name, born in ((NAME, dt.date(1980, 5, 17)), ("Ravi Fictional", None))
    ]


def test_list_patients_returns_summaries_in_the_queried_order() -> None:
    rows = _summary_rows()
    session = StatementSession(rows)
    patients = repo.list_patients(factory=lambda: session)
    assert patients == tuple(repo.PatientSummary(**vars(row)) for row in rows)
    assert str(session.statements[0]).endswith("ORDER BY patients.created_at, patients.id")


def test_patient_summary_repr_hides_the_name_and_date_of_birth() -> None:
    summary = repo.PatientSummary(**vars(_summary_rows()[0]))
    assert NAME not in repr(summary) and "1980" not in repr(summary)
    assert str(summary.id) in repr(summary)


def test_update_and_list_logs_hold_identifiers_counts_and_field_names_only(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    _update(StatementSession(_returned()), display_name=NAME, date_of_birth=dt.date(1980, 5, 17))
    repo.list_patients(factory=lambda: StatementSession(_summary_rows()))
    formatted = "\n".join(JsonFormatter().format(record) for record in caplog.records)
    for text in (caplog.text, formatted):
        assert str(PATIENT_ID) in text and "fields=date_of_birth,display_name" in text
        assert "count=2" in text
        assert "Asha" not in text and "Ravi" not in text and "1980" not in text


# --- delete_patient (Task 1.8b) ------------------------------------------------------------------


class RaisingSession(StatementSession):
    """Raises a fixed error from ``execute``, like a foreign-key violation would."""

    def __init__(self, error: Exception) -> None:
        super().__init__(None)
        self.error = error

    def execute(self, statement):
        self.statements.append(statement)
        raise self.error


def test_restrict_fk_constants_match_the_models() -> None:
    from app.records.models import Base

    restrict = {
        fk.name
        for table in Base.metadata.tables.values()
        for fk in table.foreign_key_constraints
        if fk.referred_table.name == "patients" and fk.ondelete == "RESTRICT"
    }
    assert restrict == repo._PATIENT_RESTRICT_FKS


@pytest.mark.parametrize(
    ("sqlstate", "constraint", "mapped"),
    [
        ("23503", "fk_documents_patient_id_patients", True),
        ("23503", "fk_pipeline_runs_patient_id_patients", True),
        ("23503", "fk_conflicts_patient_id_patients", True),
        ("23503", "fk_documents_supersedes_document_id_patient_id_documents", False),
        ("23503", None, False),
        ("23505", "fk_documents_patient_id_patients", False),
        (None, None, False),
    ],
)
def test_only_the_three_restrict_fks_mean_patient_not_empty(sqlstate, constraint, mapped) -> None:
    session = RaisingSession(_integrity_error(sqlstate, constraint))
    if mapped:
        with pytest.raises(PatientError) as caught:
            repo.delete_patient(PATIENT_ID, factory=lambda: session)
        assert caught.value.kind == repo.PATIENT_NOT_EMPTY
        assert caught.value.__cause__ is None and caught.value.__context__ is None
    else:
        with pytest.raises(IntegrityError):
            repo.delete_patient(PATIENT_ID, factory=lambda: session)
    assert not session.committed


def test_deleting_an_unknown_patient_is_patient_not_found() -> None:
    session = StatementSession(None)
    session.execute = lambda statement: SimpleNamespace(scalar_one_or_none=lambda: None)
    with pytest.raises(PatientError) as caught:
        repo.delete_patient(PATIENT_ID, factory=lambda: session)
    assert caught.value.kind == repo.PATIENT_NOT_FOUND and not session.committed


def test_deleting_an_empty_patient_commits_and_logs_the_id_only(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    session = StatementSession(None)
    session.execute = lambda statement: SimpleNamespace(scalar_one_or_none=lambda: PATIENT_ID)
    repo.delete_patient(PATIENT_ID, factory=lambda: session)
    assert session.committed and f"Patient deleted: patient_id={PATIENT_ID}" in caplog.text
