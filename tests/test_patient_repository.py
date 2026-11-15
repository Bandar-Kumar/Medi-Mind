"""Unit tests for the minimal patient repository (Task 2.6a). No database is used.

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
