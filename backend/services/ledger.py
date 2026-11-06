"""Authenticated external deletion ledger; only UUIDs, scope and times are allowed."""

import base64
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AAD = b"tutoria-ledger-v1"
MAX_LINE = 8192


class LedgerError(RuntimeError):
    pass


def decode_key(value):
    try:
        key = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise LedgerError("INVALID_LEDGER_KEY") from exc
    if len(key) != 32:
        raise LedgerError("INVALID_LEDGER_KEY")
    return key


def instant(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise LedgerError("INVALID_LEDGER_TIME") from exc
    if result.tzinfo is None:
        raise LedgerError("INVALID_LEDGER_TIME")
    return result.astimezone(timezone.utc)


def normalize(record):
    if not isinstance(record, dict) or set(record) - {
        "id",
        "scope",
        "subjectId",
        "requestedAt",
        "completedAt",
        "expiresAt",
    }:
        raise LedgerError("INVALID_LEDGER_RECORD")
    try:
        identifier = str(UUID(str(record["id"])))
        subject = str(UUID(str(record["subjectId"])))
        scope = record["scope"]
        requested = instant(record["requestedAt"])
    except (KeyError, ValueError, TypeError) as exc:
        raise LedgerError("INVALID_LEDGER_RECORD") from exc
    if scope not in ("document", "course", "account"):
        raise LedgerError("INVALID_LEDGER_SCOPE")
    result = {
        "id": identifier,
        "scope": scope,
        "subjectId": subject,
        "requestedAt": requested.isoformat().replace("+00:00", "Z"),
    }
    if "completedAt" in record:
        completed = instant(record["completedAt"])
        if completed < requested:
            raise LedgerError("INVALID_LEDGER_TIME")
        result["completedAt"] = completed.isoformat().replace("+00:00", "Z")
    expiry = (
        instant(record["expiresAt"]) if "expiresAt" in record else requested + timedelta(days=30)
    )
    if expiry != requested + timedelta(days=30):
        raise LedgerError("INVALID_LEDGER_RETENTION")
    result["expiresAt"] = expiry.isoformat().replace("+00:00", "Z")
    return result


def _directory(path, create=False):
    path = Path(path).absolute()
    if path.is_symlink():
        raise LedgerError("UNSAFE_LEDGER_PATH")
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise LedgerError("LEDGER_DIRECTORY_MISSING")
    return path


def _sync_directory(directory):
    if os.name == "posix":
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


@contextmanager
def ledger_lock(path):
    directory = _directory(path, create=True)
    lock = directory / ".ledger.lock"
    if lock.is_symlink():
        raise LedgerError("UNSAFE_LEDGER_PATH")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        yield directory
    finally:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def append_record(path, key, record):
    with ledger_lock(path) as directory:
        return _append_locked(directory, key, record)


def _append_locked(path, key, record):
    if len(key) != 32:
        raise LedgerError("INVALID_LEDGER_KEY")
    record = normalize(record)
    payload = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    nonce = os.urandom(12)
    line = base64.b64encode(nonce + AESGCM(key).encrypt(nonce, payload, AAD)) + b"\n"
    directory = _directory(path, create=True)
    target = directory / (record["id"] + ".ledger")
    if target.is_symlink():
        raise LedgerError("UNSAFE_LEDGER_PATH")
    if target.exists():
        old = next(
            (
                r
                for r in read_records(directory, key, include_expired=True)
                if r["id"] == record["id"]
            ),
            None,
        )
        if old and any(
            old[k] != record[k] for k in ("scope", "subjectId", "requestedAt", "expiresAt")
        ):
            raise LedgerError("LEDGER_ID_CONFLICT")
    temporary = directory / ("." + record["id"] + "." + uuid4().hex + ".pending")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(fd, "ab", closefd=False) as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        os.close(fd)
        fd = None
        os.replace(temporary, target)
        _sync_directory(directory)
    finally:
        if fd is not None:
            os.close(fd)
        if temporary.exists():
            temporary.unlink()
    return record


def read_records(path, key, at=None, include_expired=False):
    directory = _directory(path)
    at = at or datetime.now(timezone.utc)
    records = {}
    for target in sorted(directory.glob("*.ledger")):
        if target.is_symlink():
            raise LedgerError("UNSAFE_LEDGER_PATH")
        try:
            identifier = str(UUID(target.stem))
        except ValueError as exc:
            raise LedgerError("UNEXPECTED_LEDGER_FILE") from exc
        if target.stem != identifier:
            raise LedgerError("UNEXPECTED_LEDGER_FILE")
        with target.open("rb") as stream:
            while line := stream.readline(MAX_LINE + 1):
                if len(line) > MAX_LINE or not line.endswith(b"\n"):
                    raise LedgerError("TRUNCATED_LEDGER_RECORD")
                try:
                    raw = base64.b64decode(line.strip(), validate=True)
                    record = normalize(json.loads(AESGCM(key).decrypt(raw[:12], raw[12:], AAD)))
                except (InvalidTag, ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise LedgerError("UNAUTHENTICATED_LEDGER_RECORD") from exc
                if record["id"] != identifier:
                    raise LedgerError("LEDGER_ID_CONFLICT")
                old = records.get(identifier)
                if old and any(
                    old[k] != record[k] for k in ("scope", "subjectId", "requestedAt", "expiresAt")
                ):
                    raise LedgerError("LEDGER_ID_CONFLICT")
                if include_expired or instant(record["expiresAt"]) > at:
                    records[identifier] = record
    return sorted(records.values(), key=lambda r: (r["requestedAt"], r["id"]))


def expire_records(path, key, at=None):
    with ledger_lock(path):
        return _expire_locked(path, key, at)


def _expire_locked(path, key, at=None):
    directory = _directory(path)
    at = at or datetime.now(timezone.utc)
    records = read_records(directory, key, at, include_expired=True)
    expired = [r for r in records if instant(r["expiresAt"]) <= at]
    for record in expired:
        target = directory / (record["id"] + ".ledger")
        if target.is_symlink():
            raise LedgerError("UNSAFE_LEDGER_PATH")
        target.unlink()
    if expired:
        _sync_directory(directory)
    return len(expired)
