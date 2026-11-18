"""Unit tests for hashing and content-addressed storage (app/ingestion/storage.py)."""

import hashlib
import os
import stat
import sys
import uuid
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.errors import DocumentStorageError
from app.ingestion import storage
from app.ingestion.storage import (
    FALLBACK_FILENAME,
    StorageErrorKind,
    original_path,
    safe_display_filename,
    sha256_hex,
    storage_path_for_db,
    store_original,
)
from tests.fixtures import pdfs

PATIENT_A = uuid.UUID("0190a6f0-0000-7000-8000-00000000000a")
PATIENT_B = uuid.UUID("0190a6f0-0000-7000-8000-00000000000b")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


def _part_files(settings: Settings) -> list[Path]:
    return list(settings.data_dir.rglob("*.part")) if settings.data_dir.exists() else []


def _storage_error(caught: pytest.ExceptionInfo[DocumentStorageError], kind: StorageErrorKind):
    assert caught.value.kind == kind
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert "SECRET" not in str(caught.value)


# --- hashing and paths -----------------------------------------------------------------------


def test_sha256_matches_hashlib_and_is_stable() -> None:
    data = pdfs.native_pdf()
    assert sha256_hex(data) == hashlib.sha256(data).hexdigest()
    assert sha256_hex(data) == sha256_hex(pdfs.native_pdf())  # ReportLab invariant mode


def test_path_is_content_addressed_and_deterministic(settings: Settings) -> None:
    sha = "0123456789abcdef" * 4
    path = original_path(settings, PATIENT_A, sha)
    assert path == settings.data_dir / "uploads" / str(PATIENT_A) / f"{sha}.pdf"
    assert storage_path_for_db(settings, path) == f"uploads/{PATIENT_A}/{sha}.pdf"


@pytest.mark.parametrize(
    ("patient_id", "sha"),
    [
        (PATIENT_A, "A" * 64),  # uppercase
        (PATIENT_A, "a" * 63),
        (PATIENT_A, "a" * 65),
        (PATIENT_A, "../" + "a" * 61),
        (PATIENT_A, "a" * 64 + "\n"),
        (str(PATIENT_A), "a" * 64),  # a string, not a UUID
    ],
)
def test_original_path_validates_its_own_inputs(
    settings: Settings, patient_id: object, sha: str
) -> None:
    with pytest.raises(DocumentStorageError) as caught:
        original_path(settings, patient_id, sha)  # type: ignore[arg-type]
    _storage_error(caught, StorageErrorKind.INVALID_KEY)


def test_storage_path_outside_data_dir_is_refused(settings: Settings, tmp_path: Path) -> None:
    with pytest.raises(DocumentStorageError) as caught:
        storage_path_for_db(settings, tmp_path / "elsewhere.pdf")
    _storage_error(caught, StorageErrorKind.OUTSIDE_DATA_DIR)


# --- storing originals -----------------------------------------------------------------------


def test_store_keeps_exact_bytes_read_only_and_leaves_no_temp_files(settings: Settings) -> None:
    data = pdfs.native_pdf(2)
    stored = store_original(settings, PATIENT_A, data)
    assert stored.created
    assert stored.path.read_bytes() == data
    assert stored.sha256 == hashlib.sha256(stored.path.read_bytes()).hexdigest()
    assert stored.size_bytes == len(data)
    assert stored.storage_path == f"uploads/{PATIENT_A}/{stored.sha256}.pdf"
    assert not stored.path.stat().st_mode & stat.S_IWRITE  # read-only
    assert _part_files(settings) == []


def test_retry_reapplies_read_only_when_it_failed_after_publishing(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = pdfs.native_pdf()
    real_chmod = os.chmod

    def failing_chmod(path: object, mode: int) -> None:
        raise OSError("SECRET permission detail")

    # First attempt: published and verified, but marking it read-only fails.
    monkeypatch.setattr(storage.os, "chmod", failing_chmod)
    with pytest.raises(DocumentStorageError) as caught:
        store_original(settings, PATIENT_A, data)
    _storage_error(caught, StorageErrorKind.WRITE_FAILED)
    path = original_path(settings, PATIENT_A, sha256_hex(data))
    before = path.stat()
    assert before.st_mode & stat.S_IWRITE  # published, but still writable

    # Retry: the identical file is reported as already stored and the safeguard is reapplied.
    monkeypatch.setattr(storage.os, "chmod", real_chmod)
    retry = store_original(settings, PATIENT_A, data)
    after = path.stat()
    assert retry.created is False
    assert not after.st_mode & stat.S_IWRITE  # now read-only
    assert after.st_ino == before.st_ino  # same file: never rewritten or replaced
    assert path.read_bytes() == data
    assert _part_files(settings) == []


def test_storing_again_is_idempotent_and_does_not_touch_the_file(settings: Settings) -> None:
    data = pdfs.native_pdf()
    first = store_original(settings, PATIENT_A, data)
    before = first.path.stat()
    second = store_original(settings, PATIENT_A, data)
    assert (second.created, second.path) == (False, first.path)
    after = first.path.stat()
    assert (after.st_mtime_ns, after.st_ino) == (before.st_mtime_ns, before.st_ino)


def test_same_bytes_for_two_patients_are_stored_separately(settings: Settings) -> None:
    data = pdfs.native_pdf()
    a = store_original(settings, PATIENT_A, data)
    b = store_original(settings, PATIENT_B, data)
    assert a.path != b.path and a.storage_path != b.storage_path
    assert b.created and b.path.read_bytes() == data


def test_a_different_existing_file_is_never_overwritten(settings: Settings) -> None:
    data = pdfs.native_pdf()
    path = original_path(settings, PATIENT_A, sha256_hex(data))
    path.parent.mkdir(parents=True)
    path.write_bytes(b"different bytes")
    with pytest.raises(DocumentStorageError) as caught:
        store_original(settings, PATIENT_A, data)
    _storage_error(caught, StorageErrorKind.INTEGRITY_MISMATCH)
    assert path.read_bytes() == b"different bytes"
    assert _part_files(settings) == []


def test_write_failure_cleans_up_and_hides_os_text(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_fsync(fd: int) -> None:
        raise OSError("SECRET disk detail")

    monkeypatch.setattr(storage.os, "fsync", failing_fsync)
    data = pdfs.native_pdf()
    with pytest.raises(DocumentStorageError) as caught:
        store_original(settings, PATIENT_A, data)
    _storage_error(caught, StorageErrorKind.WRITE_FAILED)
    assert not original_path(settings, PATIENT_A, sha256_hex(data)).exists()
    assert _part_files(settings) == []


@pytest.mark.parametrize("winner_bytes", ["same", "different"])
def test_concurrent_writer_wins_the_link_race(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, winner_bytes: str
) -> None:
    data = pdfs.native_pdf()
    final = original_path(settings, PATIENT_A, sha256_hex(data))
    other = data if winner_bytes == "same" else b"another process wrote this"

    def link_after_someone_else(src: object, dst: object) -> None:
        Path(dst).write_bytes(other)  # another process publishes first...
        raise FileExistsError  # ...so our link fails instead of overwriting

    monkeypatch.setattr(storage.os, "link", link_after_someone_else)
    if winner_bytes == "same":
        assert store_original(settings, PATIENT_A, data).created is False
    else:
        with pytest.raises(DocumentStorageError) as caught:
            store_original(settings, PATIENT_A, data)
        _storage_error(caught, StorageErrorKind.INTEGRITY_MISMATCH)
    assert final.read_bytes() == other  # never overwritten
    assert _part_files(settings) == []


def test_failed_post_link_check_removes_only_our_own_file(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = pdfs.native_pdf()
    real_hash = storage._file_sha256
    final = original_path(settings, PATIENT_A, sha256_hex(data))

    def hash_that_fails_for_the_final_name(path: Path) -> str:
        return "0" * 64 if path == final else real_hash(path)

    monkeypatch.setattr(storage, "_file_sha256", hash_that_fails_for_the_final_name)
    with pytest.raises(DocumentStorageError) as caught:
        store_original(settings, PATIENT_A, data)
    _storage_error(caught, StorageErrorKind.INTEGRITY_MISMATCH)
    assert not final.exists()  # it was the file this call created, so it was removed
    assert _part_files(settings) == []


def test_failed_post_link_check_never_removes_a_replaced_destination(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = pdfs.native_pdf()
    final = original_path(settings, PATIENT_A, sha256_hex(data))

    def link_then_replaced(src: object, dst: object) -> None:
        Path(dst).write_bytes(data)  # a different file (other identity) now holds the name

    monkeypatch.setattr(storage.os, "link", link_then_replaced)
    with pytest.raises(DocumentStorageError) as caught:
        store_original(settings, PATIENT_A, data)
    _storage_error(caught, StorageErrorKind.INTEGRITY_MISMATCH)
    assert final.read_bytes() == data  # not ours: left in place
    assert _part_files(settings) == []


def _redirect(link: Path, target: Path) -> None:
    """Create a junction (Windows) or symlink; skip the test if the OS refuses."""
    target.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
        else:
            os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("cannot create a junction or symlink here")


def test_a_relative_data_dir_stores_normally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The default setting is the relative "data"; mkstemp returns an absolute path, which the
    # containment check once refused as outside_data_dir (found on the 2026-11-18 UI run).
    monkeypatch.chdir(tmp_path)
    relative = Settings(data_dir=Path("data"))
    stored = store_original(relative, PATIENT_A, pdfs.native_pdf())
    assert stored.created
    assert stored.storage_path == f"uploads/{PATIENT_A}/{stored.sha256}.pdf"
    assert _part_files(relative) == []


def test_a_relative_data_dir_still_refuses_a_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    relative = Settings(data_dir=Path("data"))
    relative.data_dir.mkdir()
    _redirect(relative.data_dir / "uploads", tmp_path / "outside")
    with pytest.raises(DocumentStorageError) as caught:
        store_original(relative, PATIENT_A, pdfs.native_pdf())
    _storage_error(caught, StorageErrorKind.OUTSIDE_DATA_DIR)
    assert list((tmp_path / "outside").rglob("*")) == []


def test_redirect_outside_data_dir_is_refused(settings: Settings, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    settings.data_dir.mkdir()
    _redirect(settings.data_dir / "uploads", outside)
    with pytest.raises(DocumentStorageError) as caught:
        store_original(settings, PATIENT_A, pdfs.native_pdf())
    _storage_error(caught, StorageErrorKind.OUTSIDE_DATA_DIR)
    assert list(outside.rglob("*")) == []  # nothing written through the redirect


def test_redirect_created_during_mkdir_is_caught_before_writing(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    real_mkdir = Path.mkdir

    def mkdir_then_redirect(self: Path, *args: object, **kwargs: object) -> None:
        # Simulates another process swapping in a junction between the first containment check
        # and the write: runs once, then restores the real mkdir.
        monkeypatch.setattr(Path, "mkdir", real_mkdir)
        real_mkdir(settings.data_dir, exist_ok=True)
        _redirect(settings.uploads_dir, outside)
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", mkdir_then_redirect)
    with pytest.raises(DocumentStorageError) as caught:
        store_original(settings, PATIENT_A, pdfs.native_pdf())
    _storage_error(caught, StorageErrorKind.OUTSIDE_DATA_DIR)
    assert [p for p in outside.rglob("*") if p.is_file()] == []


# --- filenames (metadata only) ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("../../x.pdf", "x.pdf"),
        ("C:\\Users\\a\\Test Person.pdf", "Test Person.pdf"),
        ("/etc/passwd", "passwd"),
        ("lab\x00re\x1fport\n.pdf", "labreport.pdf"),
        ("invoice\u202efdp.exe", "invoicefdp.exe"),  # right-to-left override removed
        ("  many   spaces .pdf  ", "many spaces .pdf"),
        ("", FALLBACK_FILENAME),
        ("...", FALLBACK_FILENAME),
        ("dir/", FALLBACK_FILENAME),
        ("\x01\x02", FALLBACK_FILENAME),
        ("Cafe\u0301.pdf", "Caf\u00e9.pdf"),  # NFC
    ],
)
def test_safe_display_filename(name: str, expected: str) -> None:
    assert safe_display_filename(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "CON",
        "con.pdf",
        "PRN.txt",
        "AUX",
        "NUL.pdf",
        "COM1",
        "com9.pdf",
        "LPT1",
        "Lpt9.tar.gz",
        "CON .pdf",
    ],
)
def test_windows_device_names_are_prefixed(name: str) -> None:
    result = safe_display_filename(name)
    assert result.startswith("_") and result[1:] == name.strip()


@pytest.mark.parametrize("name", ["CONSOLE.pdf", "COM10.pdf", "LPT0.pdf", "icon.pdf", "COM.pdf"])
def test_similar_but_ordinary_names_are_unchanged(name: str) -> None:
    assert safe_display_filename(name) == name


def test_long_names_are_truncated_keeping_a_short_extension() -> None:
    result = safe_display_filename("a" * 300 + ".pdf")
    assert len(result) == 255 and result.endswith(".pdf")
    no_short_extension = safe_display_filename("b" * 300 + "." + "c" * 40)
    assert len(no_short_extension) == 255 and no_short_extension == "b" * 255
    device_long = safe_display_filename("CON." + "d" * 300 + ".pdf")
    assert (
        len(device_long) == 255 and device_long.startswith("_CON.") and device_long.endswith(".pdf")
    )


@pytest.mark.parametrize(
    "name", ["x" * 1000, "a/" * 200 + "b", "\u202e" * 300 + "z.pdf", "é" * 400]
)
def test_filenames_always_fit_the_database_column(name: str) -> None:
    result = safe_display_filename(name)
    assert 1 <= len(result) <= 255
    assert "/" not in result and "\\" not in result


# --- concurrency on Windows (found 2026-11-18 by the concurrent-upload integration test) -------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (r"\\?\C:\data\x.pdf", r"C:\data\x.pdf"),
        (r"\\?\UNC\server\share\x.pdf", r"\\server\share\x.pdf"),
        (r"C:\data\x.pdf", r"C:\data\x.pdf"),
        ("/home/user/data/x.pdf", "/home/user/data/x.pdf"),
    ],
)
def test_the_extended_length_prefix_is_stripped(text: str, expected: str) -> None:
    assert storage.strip_extended_prefix(text) == expected


def test_an_extended_length_resolve_is_still_contained(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows returns the extended form while another writer holds the file; not "outside".
    real = Path.resolve

    def extended(self: Path, strict: bool = False) -> Path:
        resolved = real(self, strict)
        if sys.platform != "win32" or self.suffix != ".pdf":
            return resolved  # only the busy file gets the prefix; the data directory does not
        return Path(r"\\?" + "\\" + str(resolved))

    monkeypatch.setattr(Path, "resolve", extended)
    stored = store_original(settings, PATIENT_A, pdfs.native_pdf())
    assert stored.created and _part_files(settings) == []


def test_a_concurrent_read_only_mark_does_not_fail_a_published_upload(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Another caller found the published original and re-applied read-only before this caller
    # removed its temporary link (the read-only attribute belongs to the file, not the link).
    real = storage._verify_published

    def verify_then_mark_read_only(path: Path, identity, digest) -> None:
        real(path, identity, digest)
        os.chmod(path, stat.S_IREAD)

    monkeypatch.setattr(storage, "_verify_published", verify_then_mark_read_only)
    stored = store_original(settings, PATIENT_A, pdfs.native_pdf())
    assert stored.created and _part_files(settings) == []
    assert not stored.path.stat().st_mode & stat.S_IWRITE  # the safeguard is re-applied
