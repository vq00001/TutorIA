from datetime import datetime, timedelta, timezone
from io import BytesIO
from uuid import uuid4

import pytest
from backend.services.ledger import LedgerError, append_record, expire_records, read_records
from backend.services.operations_backup import (
    OperationError,
    decrypt_stream,
    encrypt_stream,
    restore,
)
from cryptography.exceptions import InvalidTag


def record(at=None):
    return dict(
        id=str(uuid4()),
        scope="document",
        subjectId=str(uuid4()),
        requestedAt=(at or datetime.now(timezone.utc)).isoformat(),
    )


def test_ledger_atomic_idempotence_and_no_plaintext(tmp_path):
    key = b"k" * 32
    item = record()
    append_record(tmp_path, key, item)
    append_record(tmp_path, key, item)
    records = read_records(tmp_path, key)
    assert len(records) == 1 and records[0]["subjectId"] == item["subjectId"]
    encrypted = (tmp_path / (item["id"] + ".ledger")).read_bytes()
    assert item["subjectId"].encode() not in encrypted
    assert len(encrypted.splitlines()) == 1
    assert not list(tmp_path.glob("*.pending"))


def test_ledger_rejects_content_and_corruption(tmp_path):
    key = b"k" * 32
    item = record()
    with pytest.raises(LedgerError, match="INVALID_LEDGER_RECORD"):
        append_record(tmp_path, key, {**item, "prompt": "PRIVATE SOURCE"})
    append_record(tmp_path, key, item)
    path = tmp_path / (item["id"] + ".ledger")
    content = bytearray(path.read_bytes())
    content[25] = ord("A") if content[25] != ord("A") else ord("B")
    path.write_bytes(content)
    with pytest.raises(LedgerError):
        read_records(tmp_path, key)


def test_ledger_expiry_uses_authenticated_time_not_file_mtime(tmp_path):
    at = datetime.now(timezone.utc)
    old, new = record(at - timedelta(days=31)), record(at)
    append_record(tmp_path, b"k" * 32, old)
    append_record(tmp_path, b"k" * 32, new)
    assert [r["id"] for r in read_records(tmp_path, b"k" * 32, at)] == [new["id"]]
    assert expire_records(tmp_path, b"k" * 32, at) == 1
    assert not (tmp_path / (old["id"] + ".ledger")).exists()


def metadata():
    return dict(
        formatVersion=1,
        backupId=str(uuid4()),
        createdAt=datetime.now(timezone.utc).isoformat(),
        postgresMajor=18,
        vectorVersion="0.8.7",
        owners=["postgres"],
        alembicRevision="0013",
    )


def test_backup_envelope_roundtrip_authenticates_all_archive_bytes():
    source = (b"COPY synthetic private original\x00" * 100000) + b"END"
    output = BytesIO()
    encrypt_stream(BytesIO(source), output, b"k" * 32, metadata())
    encrypted = output.getvalue()
    assert b"private original" not in encrypted
    restored = BytesIO()
    result = decrypt_stream(BytesIO(encrypted), restored, b"k" * 32)
    assert result["postgresMajor"] == 18 and restored.getvalue() == source
    damaged = bytearray(encrypted)
    damaged[-30] ^= 1
    with pytest.raises(InvalidTag):
        decrypt_stream(BytesIO(damaged), BytesIO(), b"k" * 32)
    with pytest.raises(InvalidTag):
        decrypt_stream(BytesIO(encrypted), BytesIO(), b"w" * 32)


def test_backup_metadata_tampering_is_not_accepted():
    output = BytesIO()
    encrypt_stream(BytesIO(b"synthetic archive"), output, b"k" * 32, metadata())
    encrypted = output.getvalue().replace(b"0.8.7", b"0.8.8")
    with pytest.raises(InvalidTag):
        decrypt_stream(BytesIO(encrypted), BytesIO(), b"k" * 32)


def test_restore_refuses_production_name_before_touching_database(tmp_path):
    with pytest.raises(OperationError, match="RESTORE_STAGING_DATABASE_REQUIRED"):
        restore(
            tmp_path / "missing", "postgresql://user:secret@db/prod", b"k" * 32, tmp_path, b"l" * 32
        )
