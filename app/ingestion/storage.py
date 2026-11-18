"""SHA-256 hashing and content-addressed, write-once storage of original documents.

Layout: ``<data_dir>/uploads/<patient_id>/<sha256>.pdf``. The path is built only from a UUID and
a validated hash, so the uploaded filename never reaches the file system; the filename is kept
only as sanitized metadata (``safe_display_filename``).

Write-once, atomic publishing (``write_new_file``):
1. containment check, then ``mkdir``, then the containment check again;
2. write a temporary ``.part`` file in the target directory (same volume, inside ``data_dir``),
   ``fsync`` it, re-read it and compare its SHA-256;
3. publish with ``os.link(temp, target)``. Creating a hard link fails if the target exists, so
   this is an atomic "create only if absent" on NTFS and POSIX. ``os.replace`` is never used: it
   would silently overwrite an existing original;
4. confirm the target is the same file with the same hash, remove the temporary name, and only
   then mark the file read-only.
If the target already exists (earlier upload, or a concurrent writer winning step 3), its hash is
compared: same bytes means ``created=False``, and the read-only flag is reapplied if an earlier
attempt failed before setting it (only the permission changes, never the content); different bytes
raise ``integrity_mismatch`` and the existing file is left untouched.

Read-only is a safeguard against accidental modification, not a security boundary: anyone with
the account's permissions can clear it. Hard links need NTFS or a POSIX file system; FAT/exFAT and
some network shares fail with ``write_failed`` (documented limitation).

Errors are ``DocumentStorageError`` with a stable ``kind``. Messages never contain the original
filename, document content or the OS error text, and are raised outside ``except`` blocks so no
chained exception carries them.
"""

import hashlib
import os
import re
import stat
import tempfile
import time
import unicodedata
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.core.config import Settings
from app.core.errors import DocumentStorageError

ORIGINAL_SUFFIX = ".pdf"
# Removing the temporary link can fail on Windows while a concurrent caller has the same
# content-addressed file open for verification; it is retried briefly (2026-11-18).
UNLINK_ATTEMPTS = 50
UNLINK_PAUSE_SECONDS = 0.01
MAX_FILENAME_CHARS = 255  # documents.original_filename CHECK: length 1..255
MAX_KEPT_EXTENSION_CHARS = 16
FALLBACK_FILENAME = "unnamed.pdf"

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)


class StorageErrorKind(StrEnum):
    INTEGRITY_MISMATCH = "integrity_mismatch"
    OUTSIDE_DATA_DIR = "outside_data_dir"
    INVALID_KEY = "invalid_key"
    WRITE_FAILED = "write_failed"


@dataclass(frozen=True)
class StoredOriginal:
    path: Path
    storage_path: str  # POSIX, relative to data_dir: the value for documents.storage_path
    sha256: str
    size_bytes: int
    created: bool  # False: identical bytes were already stored


def _error(kind: StorageErrorKind, message: str) -> DocumentStorageError:
    return DocumentStorageError(message, kind=kind)


def sha256_hex(data: bytes) -> str:
    """Lowercase hexadecimal SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def original_path(settings: Settings, patient_id: uuid.UUID, sha256: str) -> Path:
    """Deterministic storage path of an original. Validates both inputs itself."""
    if not isinstance(patient_id, uuid.UUID):
        raise _error(StorageErrorKind.INVALID_KEY, "patient_id must be a UUID")
    if not isinstance(sha256, str) or not _SHA256_HEX.fullmatch(sha256):
        raise _error(
            StorageErrorKind.INVALID_KEY, "sha256 must be 64 lowercase hexadecimal characters"
        )
    return settings.uploads_dir / str(patient_id) / f"{sha256}{ORIGINAL_SUFFIX}"


def storage_path_for_db(settings: Settings, path: Path) -> str:
    """The path relative to ``data_dir`` in POSIX form, e.g. ``uploads/<patient>/<sha>.pdf``."""
    if not path.is_relative_to(settings.data_dir):
        raise _error(StorageErrorKind.OUTSIDE_DATA_DIR, "The path is outside the data directory")
    return path.relative_to(settings.data_dir).as_posix()


def store_original(settings: Settings, patient_id: uuid.UUID, data: bytes) -> StoredOriginal:
    """Store the exact uploaded bytes write-once under their content address.

    Raises:
        DocumentStorageError: see ``StorageErrorKind``.
    """
    digest = sha256_hex(data)
    path = original_path(settings, patient_id, digest)
    created = write_new_file(settings.data_dir, path, data)
    return StoredOriginal(
        path=path,
        storage_path=storage_path_for_db(settings, path),
        sha256=digest,
        size_bytes=len(data),
        created=created,
    )


def write_new_file(data_dir: Path, path: Path, data: bytes) -> bool:
    """Atomically create ``path`` with ``data``; never overwrite. Returns True if created.

    An existing file with identical bytes returns False; different bytes raise
    ``integrity_mismatch``.
    """
    digest = sha256_hex(data)
    _check_contained(data_dir, path)  # before any directory is created
    if os.path.lexists(path):
        _verify_existing(path, digest)
        _ensure_read_only(path)  # an earlier attempt may have failed before marking it read-only
        return False

    mkdir_failed = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        mkdir_failed = True
    if mkdir_failed:
        raise _error(StorageErrorKind.WRITE_FAILED, "Could not create the storage directory")
    _check_contained(data_dir, path)  # again, immediately before writing

    write_failed = False
    try:
        created = _publish(data_dir, path, data, digest)
    except OSError:
        write_failed = True
    if write_failed:
        raise _error(StorageErrorKind.WRITE_FAILED, "Could not write the file to storage")
    if not created:  # a concurrent writer published first
        _verify_existing(path, digest)
        _ensure_read_only(path)
    return created


def _publish(data_dir: Path, path: Path, data: bytes, digest: str) -> bool:
    """Steps 2–4 of the module docstring. The temporary file is removed on every path."""
    temp: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
        temp = Path(temp_name)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _check_contained(data_dir, temp)
        if _file_sha256(temp) != digest:
            raise _error(
                StorageErrorKind.INTEGRITY_MISMATCH, "The temporary copy differs from the upload"
            )
        identity = _identity(temp.lstat())
        _check_contained(data_dir, path)  # immediately before publishing
        try:
            os.link(temp, path)
        except FileExistsError:
            return False  # never overwritten; the caller verifies the existing file
        _verify_published(path, identity, digest)
        _unlink_with_retry(temp)
        temp = None
        os.chmod(path, stat.S_IREAD)  # after verification; a safeguard, not a security boundary
        return True
    finally:
        if temp is not None:
            _remove_quietly(temp)


def _verify_published(path: Path, identity: tuple[int, int], digest: str) -> None:
    """Confirm the new name is our verified file. On failure, remove it only if it still is."""
    info = path.lstat()
    if stat.S_ISREG(info.st_mode) and _identity(info) == identity and _file_sha256(path) == digest:
        return
    # Remove the destination only if it is still exactly the file this call created (same device
    # and file ID). A destination replaced by another process is never removed.
    if _identity(path.lstat()) == identity:
        path.unlink()
    raise _error(StorageErrorKind.INTEGRITY_MISMATCH, "The stored file could not be verified")


def _verify_existing(path: Path, digest: str) -> None:
    unreadable = False
    matches = False
    try:
        info = path.lstat()
        matches = stat.S_ISREG(info.st_mode) and _file_sha256(path) == digest
    except OSError:
        unreadable = True
    if unreadable:
        raise _error(StorageErrorKind.WRITE_FAILED, "Could not read the existing stored file")
    if not matches:
        raise _error(
            StorageErrorKind.INTEGRITY_MISMATCH,
            "A different file already exists at the content address; it was not overwritten",
        )


def _ensure_read_only(path: Path) -> None:
    """Reapply the read-only safeguard to a verified, existing original. Never rewrites it.

    Only the permission bits change; the content and file identity stay the same. Called after
    ``_verify_existing``, which confirmed ``path`` is a regular file (not a symlink) with the
    expected bytes.
    """
    failed = False
    try:
        if path.lstat().st_mode & stat.S_IWRITE:
            os.chmod(path, stat.S_IREAD)
    except OSError:
        failed = True
    if failed:
        raise _error(StorageErrorKind.WRITE_FAILED, "Could not mark the stored file read-only")


def _check_contained(data_dir: Path, target: Path) -> None:
    """``target`` must lie under ``data_dir``, with no symlink or junction redirecting it."""
    outside = _error(StorageErrorKind.OUTSIDE_DATA_DIR, "The path is outside the data directory")
    # Compare absolute forms: mkstemp returns an absolute path even for a relative data_dir (the
    # default "data"). absolute() neither normalizes ".." nor follows links, so the checks below
    # keep their meaning.
    data_dir, target = data_dir.absolute(), target.absolute()
    if not target.is_relative_to(data_dir):
        raise outside
    relative = target.relative_to(data_dir)
    if ".." in relative.parts:
        raise outside
    current = data_dir
    for part in relative.parts:
        current = current / part
        if current.is_symlink() or current.is_junction():
            raise outside
    if not _resolved(target).is_relative_to(_resolved(data_dir)):
        raise outside


_EXTENDED_PREFIX = "\\\\?\\"  # the four characters \\?\
_EXTENDED_UNC_PREFIX = "\\\\?\\UNC\\"  # \\?\UNC\


def strip_extended_prefix(text: str) -> str:
    """Remove Windows' extended-length prefix: ``\\\\?\\C:\\x`` -> ``C:\\x``,
    ``\\\\?\\UNC\\srv\\share`` -> ``\\\\srv\\share``. Any other text is returned unchanged."""
    if text.startswith(_EXTENDED_UNC_PREFIX):
        return "\\\\" + text[len(_EXTENDED_UNC_PREFIX) :]
    if text.startswith(_EXTENDED_PREFIX):
        return text[len(_EXTENDED_PREFIX) :]
    return text


def _resolved(path: Path) -> Path:
    """``path.resolve()`` without Windows' extended-length prefix.

    While another writer holds the file, Windows can return the extended-length form of a path
    that is otherwise identical, which a plain ``is_relative_to`` comparison would wrongly treat
    as outside the data directory (found 2026-11-18 by the concurrent-upload test). Only the
    prefix is removed: links are still followed by ``resolve()`` and checked component by
    component above.
    """
    return Path(strip_extended_prefix(str(path.resolve())))


def _unlink_with_retry(path: Path) -> None:
    """Remove the temporary link of a published, verified file.

    On Windows a hard link cannot be removed (found 2026-11-18 by the concurrent-upload test):
    - while the file is read-only: the attribute belongs to the file, so a concurrent caller that
      found the published original and re-applied the read-only safeguard also protects this
      link. The link is made writable and removed; the caller re-applies read-only right after;
    - while another handle has the file open without delete sharing (a concurrent caller verifying
      the same original): retried briefly.
    A final failure propagates as before.
    """
    for attempt in range(UNLINK_ATTEMPTS):
        try:
            path.unlink()
            return
        except PermissionError:
            if attempt == UNLINK_ATTEMPTS - 1:
                raise
            if not os.access(path, os.W_OK):
                os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
                continue
            time.sleep(UNLINK_PAUSE_SECONDS)


def _file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _identity(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass  # best effort; a leftover ".part" file never has a final name


def safe_display_filename(name: str) -> str:
    """A sanitized filename for metadata and display only. Never used to build a path.

    Keeps the last path component, normalizes Unicode (NFC), removes control and format
    characters (such as the right-to-left override U+202E), collapses whitespace, strips
    surrounding spaces and dots, prefixes Windows device names (``CON.pdf`` → ``_CON.pdf``) and
    truncates to 255 characters, keeping a short extension. Always 1–255 characters.
    """
    base = re.split(r"[\\/]", name)[-1]
    base = unicodedata.normalize("NFC", base)
    base = "".join(ch for ch in base if unicodedata.category(ch) not in ("Cc", "Cf"))
    base = " ".join(base.split()).strip(" .")
    if not base:
        return FALLBACK_FILENAME
    # Windows treats "CON", "con.pdf" and "CON .tar.gz" alike: the part before the first dot.
    if base.split(".", 1)[0].rstrip(" ").upper() in _WINDOWS_DEVICE_NAMES:
        base = "_" + base
    if len(base) > MAX_FILENAME_CHARS:
        dot = base.rfind(".")
        extension = base[dot:] if 0 < dot and len(base) - dot <= MAX_KEPT_EXTENSION_CHARS else ""
        stem = base[: MAX_FILENAME_CHARS - len(extension)].rstrip(" .")
        base = stem + extension if stem else FALLBACK_FILENAME
    return base
