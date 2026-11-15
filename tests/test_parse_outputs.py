"""Unit tests for write-once parse-output storage (app/ingestion/parse_outputs.py).

No database, no Docling: IR documents are built by hand, files go to a temporary data directory.
All text is fictional.
"""

import json
import os
import stat
import sys
import uuid
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.errors import DocumentStorageError
from app.ingestion import parse_outputs as po
from app.ingestion.ir import ParsedDocument, ParsedPage, ParseMethod, ParserInfo, dump_ir

SHA = "c" * 64
SECRET = "Haemoglobin 13.5 g/dL Test Person (fictional)"
DOCLING = json.dumps({"schema_name": "DoclingDocument", "texts": []}).encode("utf-8")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


def _document(pages: int = 2, sha: str = SHA) -> ParsedDocument:
    return ParsedDocument(
        sha256=sha,
        page_count=pages,
        parser=ParserInfo(name="docling", version="2.150.1"),
        pages=[
            ParsedPage(
                page_number=n,
                text=f"{SECRET} page {n}",
                parse_method=ParseMethod.NATIVE,
                native_char_count=30,
            )
            for n in range(1, pages + 1)
        ],
    )


class Ids:
    def __init__(self) -> None:
        self.patient = uuid.uuid4()
        self.document = uuid.uuid4()
        self.run = uuid.uuid4()


@pytest.fixture
def ids() -> Ids:
    return Ids()


def _publish(settings: Settings, ids: Ids, **overrides: object) -> po.ParseOutputs:
    arguments: dict[str, object] = {
        "patient_id": ids.patient,
        "document_id": ids.document,
        "parse_run_id": ids.run,
        "source_sha256": SHA,
        "expected_page_count": 2,
        "document": _document(),
        "docling_json": DOCLING,
    } | overrides
    return po.publish_parse_outputs(settings, **arguments)  # type: ignore[arg-type]


def _load(settings: Settings, ids: Ids, **overrides: object) -> po.ParseOutputs | None:
    arguments: dict[str, object] = {
        "patient_id": ids.patient,
        "document_id": ids.document,
        "parse_run_id": ids.run,
        "source_sha256": SHA,
        "expected_page_count": 2,
    } | overrides
    return po.load_verified_parse_outputs(settings, **arguments)  # type: ignore[arg-type]


def _writable(path: Path) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def _temp_dirs(settings: Settings, ids: Ids) -> list[str]:
    parent = po.parse_output_dir(settings, ids.patient, ids.document).parent
    if not parent.exists():
        return []
    return [p.name for p in parent.iterdir() if p.name.startswith(po.TEMP_PREFIX)]


def _assert_sanitized(error: DocumentStorageError, settings: Settings) -> None:
    message = str(error)
    assert "Test Person" not in message and "Haemoglobin" not in message
    assert str(settings.data_dir) not in message
    assert error.__cause__ is None and error.__context__ is None


# --- publish -------------------------------------------------------------------------------------


def test_publish_writes_three_read_only_files_and_no_temp_dir(settings: Settings, ids: Ids) -> None:
    outputs = _publish(settings, ids)
    assert outputs.created is True
    directory = outputs.directory
    assert directory == settings.parsed_dir / str(ids.patient) / str(ids.document) / "v1"
    assert sorted(p.name for p in directory.iterdir()) == [
        "docling.json",
        "ir.v1.json",
        "manifest.json",
    ]
    assert all(not p.stat().st_mode & stat.S_IWRITE for p in directory.iterdir())
    assert (directory / "docling.json").read_bytes() == DOCLING  # byte for byte
    assert (directory / "ir.v1.json").read_bytes() == dump_ir(_document())
    assert outputs.document == _document()
    assert _temp_dirs(settings, ids) == []


def test_manifest_holds_identifiers_and_digests_only(settings: Settings, ids: Ids) -> None:
    outputs = _publish(settings, ids)
    raw = json.loads((outputs.directory / "manifest.json").read_bytes())
    assert list(raw) == [
        "manifest_version",
        "ir_schema_version",
        "document_id",
        "source_sha256",
        "parse_run_id",
        "files",
    ]
    assert raw["manifest_version"] == 1 and raw["ir_schema_version"] == 1
    assert raw["document_id"] == str(ids.document) and raw["parse_run_id"] == str(ids.run)
    assert list(raw["files"]) == ["ir.v1.json", "docling.json"]
    for name, digest in raw["files"].items():
        data = (outputs.directory / name).read_bytes()
        assert digest == {"sha256": po.sha256_hex(data), "size_bytes": len(data)}
    text = json.dumps(raw)
    assert "Test Person" not in text and str(ids.patient) not in text
    assert po.dump_manifest(outputs.manifest) == (outputs.directory / "manifest.json").read_bytes()


def test_publishing_again_with_the_same_run_reuses_and_restores_read_only(
    settings: Settings, ids: Ids
) -> None:
    first = _publish(settings, ids)
    _writable(first.directory / "ir.v1.json")  # an earlier attempt stopped before read-only
    again = _publish(settings, ids)
    assert again.created is False and again.manifest == first.manifest
    assert not (first.directory / "ir.v1.json").stat().st_mode & stat.S_IWRITE


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"document": _document(sha="d" * 64)}, "different source file"),
        ({"expected_page_count": 3}, "page count"),
        ({"docling_json": b"{not json"}, "not valid UTF-8 JSON"),
        ({"docling_json": b"\xff\xfe"}, "not valid UTF-8 JSON"),
    ],
)
def test_inconsistent_inputs_are_refused_before_anything_is_written(
    settings: Settings, ids: Ids, overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(DocumentStorageError) as caught:
        _publish(settings, ids, **overrides)
    assert caught.value.kind == "integrity_mismatch" and message in str(caught.value)
    assert not po.parse_output_dir(settings, ids.patient, ids.document).exists()
    assert _temp_dirs(settings, ids) == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_sha256": "C" * 64},
        {"source_sha256": "abc"},
        {"expected_page_count": 0},
        {"expected_page_count": True},
        {"parse_run_id": "not-a-uuid"},
        {"document_id": str(uuid.uuid4())},
    ],
)
def test_invalid_keys_are_refused(settings: Settings, ids: Ids, overrides: dict) -> None:
    with pytest.raises(DocumentStorageError) as caught:
        _publish(settings, ids, **overrides)
    assert caught.value.kind == "invalid_key"


# --- verification and tampering ------------------------------------------------------------------


def test_load_returns_none_when_nothing_is_published(settings: Settings, ids: Ids) -> None:
    assert _load(settings, ids) is None
    assert (
        po.read_published_manifest(
            settings, patient_id=ids.patient, document_id=ids.document, source_sha256=SHA
        )
        is None
    )


def test_load_verifies_and_returns_the_ir(settings: Settings, ids: Ids) -> None:
    _publish(settings, ids)
    loaded = _load(settings, ids)
    assert loaded is not None and loaded.created is False and loaded.document == _document()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"parse_run_id": uuid.uuid4()}, "different parse run"),
        ({"source_sha256": "d" * 64}, "different source file"),
        ({"expected_page_count": 3}, "page count"),
    ],
)
def test_load_rejects_a_different_run_source_or_page_count(
    settings: Settings, ids: Ids, overrides: dict, message: str
) -> None:
    _publish(settings, ids)
    with pytest.raises(DocumentStorageError) as caught:
        _load(settings, ids, **overrides)
    assert caught.value.kind == "integrity_mismatch" and message in str(caught.value)
    _assert_sanitized(caught.value, settings)


def test_publishing_with_another_run_never_replaces_the_directory(
    settings: Settings, ids: Ids
) -> None:
    first = _publish(settings, ids)
    before = {p.name: p.read_bytes() for p in first.directory.iterdir()}
    with pytest.raises(DocumentStorageError) as caught:
        _publish(settings, ids, parse_run_id=uuid.uuid4())
    assert caught.value.kind == "integrity_mismatch"
    assert {p.name: p.read_bytes() for p in first.directory.iterdir()} == before


@pytest.mark.parametrize("name", ["ir.v1.json", "docling.json"])
@pytest.mark.parametrize("change", ["flip_byte", "append", "truncate"])
def test_tampered_artifacts_are_rejected(
    settings: Settings, ids: Ids, name: str, change: str
) -> None:
    directory = _publish(settings, ids).directory
    path = directory / name
    data = path.read_bytes()
    _writable(path)
    if change == "flip_byte":
        path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    elif change == "append":
        path.write_bytes(data + b" ")
    else:
        path.write_bytes(data[:-1])
    with pytest.raises(DocumentStorageError) as caught:
        _load(settings, ids)
    assert caught.value.kind == "integrity_mismatch"
    assert str(caught.value) == f"{name} does not match its manifest entry"


def _rewrite_manifest(
    directory: Path, change: dict | None = None, raw: bytes | None = None
) -> None:
    path = directory / "manifest.json"
    _writable(path)
    if raw is None:
        manifest = json.loads(path.read_bytes())
        manifest.update(change or {})
        raw = json.dumps(manifest).encode("utf-8")
    path.write_bytes(raw)


@pytest.mark.parametrize(
    ("change", "raw", "message"),
    [
        (None, b"{not json", "manifest is invalid"),
        ({"extra": 1}, None, "manifest is invalid"),
        ({"manifest_version": 2}, None, "manifest is invalid"),
        ({"ir_schema_version": 2}, None, "manifest is invalid"),
        ({"files": {}}, None, "manifest is invalid"),
        ({"source_sha256": "XYZ"}, None, "manifest is invalid"),
        ({"document_id": str(uuid.uuid4())}, None, "different document"),
        ({"source_sha256": "d" * 64}, None, "different source file"),
        ({"parse_run_id": str(uuid.uuid4())}, None, "different parse run"),
    ],
)
def test_tampered_manifests_are_rejected(
    settings: Settings, ids: Ids, change: dict | None, raw: bytes | None, message: str
) -> None:
    directory = _publish(settings, ids).directory
    _rewrite_manifest(directory, change, raw)
    with pytest.raises(DocumentStorageError) as caught:
        _load(settings, ids)
    assert caught.value.kind == "integrity_mismatch" and message in str(caught.value)
    _assert_sanitized(caught.value, settings)


def test_an_ir_that_does_not_load_is_rejected(settings: Settings, ids: Ids) -> None:
    directory = _publish(settings, ids).directory
    bad_ir = b'{"schema_version": 1}'
    _writable(directory / "ir.v1.json")
    (directory / "ir.v1.json").write_bytes(bad_ir)
    manifest = json.loads((directory / "manifest.json").read_bytes())
    manifest["files"]["ir.v1.json"] = {"sha256": po.sha256_hex(bad_ir), "size_bytes": len(bad_ir)}
    _rewrite_manifest(directory, raw=json.dumps(manifest).encode("utf-8"))
    with pytest.raises(DocumentStorageError) as caught:
        _load(settings, ids)
    assert caught.value.kind == "integrity_mismatch"
    assert str(caught.value) == "The stored IR is not valid IR"


@pytest.mark.parametrize("layout", ["extra_file", "missing_file", "subdirectory"])
def test_unexpected_directory_layouts_are_rejected(
    settings: Settings, ids: Ids, layout: str
) -> None:
    directory = _publish(settings, ids).directory
    if layout == "extra_file":
        (directory / "notes.txt").write_bytes(b"x")
    elif layout == "missing_file":
        _writable(directory / "docling.json")
        (directory / "docling.json").unlink()
    else:
        (directory / "nested").mkdir()
    with pytest.raises(DocumentStorageError) as caught:
        _load(settings, ids)
    assert caught.value.kind == "integrity_mismatch"
    with pytest.raises(DocumentStorageError) as pre:
        po.read_published_manifest(
            settings, patient_id=ids.patient, document_id=ids.document, source_sha256=SHA
        )
    assert pre.value.kind == "integrity_mismatch"


def test_read_published_manifest_checks_document_and_source_only(
    settings: Settings, ids: Ids
) -> None:
    directory = _publish(settings, ids).directory
    manifest = po.read_published_manifest(
        settings, patient_id=ids.patient, document_id=ids.document, source_sha256=SHA
    )
    assert manifest is not None and manifest.parse_run_id == ids.run
    with pytest.raises(DocumentStorageError) as caught:
        po.read_published_manifest(
            settings, patient_id=ids.patient, document_id=ids.document, source_sha256="d" * 64
        )
    assert caught.value.kind == "integrity_mismatch"
    # The pre-check does not read the artifacts: a tampered artifact still passes it.
    _writable(directory / "docling.json")
    (directory / "docling.json").write_bytes(b"{}")
    assert (
        po.read_published_manifest(
            settings, patient_id=ids.patient, document_id=ids.document, source_sha256=SHA
        )
        == manifest
    )


# --- concurrency, interruption, leftovers --------------------------------------------------------


def test_lost_rename_race_reuses_a_matching_winner(
    settings: Settings, ids: Ids, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_rename = po._rename_once

    def racing(data_dir: Path, temp: Path, final: Path) -> bool:
        monkeypatch.setattr(po, "_rename_once", real_rename)
        _publish(settings, ids)  # the winner, same run
        return real_rename(data_dir, temp, final)

    monkeypatch.setattr(po, "_rename_once", racing)
    outputs = _publish(settings, ids)
    assert outputs.created is False
    assert _temp_dirs(settings, ids) == []


def test_lost_rename_race_against_another_run_is_a_mismatch(
    settings: Settings, ids: Ids, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_rename = po._rename_once
    winner_run = uuid.uuid4()

    def racing(data_dir: Path, temp: Path, final: Path) -> bool:
        monkeypatch.setattr(po, "_rename_once", real_rename)
        _publish(settings, ids, parse_run_id=winner_run)
        return real_rename(data_dir, temp, final)

    monkeypatch.setattr(po, "_rename_once", racing)
    with pytest.raises(DocumentStorageError) as caught:
        _publish(settings, ids)
    assert caught.value.kind == "integrity_mismatch"
    assert _temp_dirs(settings, ids) == []
    manifest = json.loads(
        (po.parse_output_dir(settings, ids.patient, ids.document) / "manifest.json").read_bytes()
    )
    assert manifest["parse_run_id"] == str(winner_run)  # the winner's directory is untouched


def test_interrupted_write_leaves_no_directory_and_no_temp(
    settings: Settings, ids: Ids, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}
    real_fsync = os.fsync

    def failing_fsync(fd: int) -> None:
        calls["n"] += 1
        if calls["n"] == 2:  # the second file (docling.json)
            raise OSError(28, "No space left on device: /private/path/Test Person")
        real_fsync(fd)

    monkeypatch.setattr(po.os, "fsync", failing_fsync)
    with pytest.raises(DocumentStorageError) as caught:
        _publish(settings, ids)
    assert caught.value.kind == "write_failed"
    assert "Test Person" not in str(caught.value) and "/private" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert not po.parse_output_dir(settings, ids.patient, ids.document).exists()
    assert _temp_dirs(settings, ids) == []


def test_failed_rename_without_a_winner_is_write_failed(
    settings: Settings, ids: Ids, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refusing_rename(source: object, target: object) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(po.os, "rename", refusing_rename)
    with pytest.raises(DocumentStorageError) as caught:
        _publish(settings, ids)
    assert caught.value.kind == "write_failed"
    assert not po.parse_output_dir(settings, ids.patient, ids.document).exists()
    assert _temp_dirs(settings, ids) == []


def test_leftover_temp_directories_from_a_crash_are_ignored(settings: Settings, ids: Ids) -> None:
    parent = po.parse_output_dir(settings, ids.patient, ids.document).parent
    leftover = parent / f"{po.TEMP_PREFIX}crashed"
    leftover.mkdir(parents=True)
    (leftover / "ir.v1.json").write_bytes(b"partial")
    outputs = _publish(settings, ids)
    assert outputs.created is True and _load(settings, ids) is not None
    assert (leftover / "ir.v1.json").read_bytes() == b"partial"  # never read, never removed


# --- Windows / NTFS ------------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Windows rename semantics")
def test_ntfs_rename_never_replaces_an_existing_directory(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_bytes(b"1")
    os.chmod(source / "file", stat.S_IREAD)  # a read-only file inside does not block the rename
    for target_has_files in (True, False):
        target = tmp_path / f"target-{target_has_files}"
        target.mkdir()
        if target_has_files:
            (target / "other").write_bytes(b"2")
        with pytest.raises(FileExistsError):
            os.rename(source, target)
    os.rename(source, tmp_path / "renamed")
    assert (tmp_path / "renamed" / "file").read_bytes() == b"1"


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions")
def test_a_junction_at_the_output_directory_is_refused(settings: Settings, ids: Ids) -> None:
    import _winapi

    outside = settings.data_dir.parent / "outside"
    outside.mkdir(parents=True)
    final = po.parse_output_dir(settings, ids.patient, ids.document)
    final.parent.mkdir(parents=True)
    _winapi.CreateJunction(str(outside), str(final))
    with pytest.raises(DocumentStorageError) as caught:
        _load(settings, ids)
    assert caught.value.kind == "outside_data_dir"
    with pytest.raises(DocumentStorageError) as publish:
        _publish(settings, ids)
    assert publish.value.kind == "outside_data_dir"
    assert list(outside.iterdir()) == []
