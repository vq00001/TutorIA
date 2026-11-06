"""Encrypted logical backups and gated restoration into a fresh staging database.

This operational CLI requires PostgreSQL 18 client binaries and an administrative
restore connection. It never restores into a populated database or serves traffic.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Timer
from uuid import UUID, uuid4

import psycopg
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from backend.services.ledger import LedgerError, decode_key, instant, ledger_lock, read_records

MAGIC = b"TUTORIA-BACKUP-V1\n"
CHUNK = 1024 * 1024


class OperationError(RuntimeError):
    pass


def _header(metadata):
    data = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    if len(data) > 16384:
        raise OperationError("INVALID_BACKUP_METADATA")
    return MAGIC + struct.pack(">I", len(data)) + data


def encrypt_stream(source, target, key, metadata):
    if len(key) != 32:
        raise OperationError("INVALID_BACKUP_KEY")
    header = _header(metadata)
    nonce = os.urandom(12)
    target.write(header + nonce)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    while data := source.read(CHUNK):
        target.write(encryptor.update(data))
    target.write(encryptor.finalize())
    target.write(encryptor.tag)


def read_header(stream):
    if stream.read(len(MAGIC)) != MAGIC:
        raise OperationError("INVALID_BACKUP_FORMAT")
    raw = stream.read(4)
    if len(raw) != 4:
        raise OperationError("TRUNCATED_BACKUP")
    length = struct.unpack(">I", raw)[0]
    if not 1 <= length <= 16384:
        raise OperationError("INVALID_BACKUP_METADATA")
    data = stream.read(length)
    if len(data) != length:
        raise OperationError("TRUNCATED_BACKUP")
    try:
        metadata = json.loads(data)
        UUID(metadata["backupId"])
        instant(metadata["createdAt"])
        if (
            metadata["formatVersion"] != 1
            or metadata["postgresMajor"] != 18
            or not isinstance(metadata["owners"], list)
        ):
            raise ValueError("Unsupported format")
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise OperationError("INVALID_BACKUP_METADATA") from exc
    return metadata, MAGIC + raw + data


def decrypt_stream(source, target, key):
    metadata, header = read_header(source)
    nonce = source.read(12)
    offset = source.tell()
    source.seek(0, 2)
    end = source.tell() - 16
    if len(nonce) != 12 or end < offset:
        raise OperationError("TRUNCATED_BACKUP")
    source.seek(end)
    tag = source.read(16)
    source.seek(offset)
    decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
    decryptor.authenticate_additional_data(header)
    remaining = end - offset
    while remaining:
        block = source.read(min(CHUNK, remaining))
        if not block:
            raise OperationError("TRUNCATED_BACKUP")
        remaining -= len(block)
        target.write(decryptor.update(block))
    # No pg_restore process starts until authentication succeeds completely.
    target.write(decryptor.finalize())
    return metadata


def native_url(value):
    if value.startswith("postgresql+psycopg://"):
        value = value.replace("postgresql+psycopg://", "postgresql://", 1)
    params = conninfo_to_dict(value)
    if not params.get("host") or not params.get("dbname") or not params.get("user"):
        raise OperationError("EXPLICIT_DATABASE_CONNECTION_REQUIRED")
    return value, params


def _client(name):
    executable = os.environ.get(name.upper() + "_BIN") or shutil.which(name)
    if not executable:
        raise OperationError("POSTGRESQL_18_CLIENT_REQUIRED")
    result = subprocess.run(
        [executable, "--version"], capture_output=True, text=True, timeout=5, check=False
    )
    if result.returncode or not re.search(r"\b18(?:\.|\b)", result.stdout):
        raise OperationError("POSTGRESQL_18_CLIENT_REQUIRED")
    return executable


def _client_env(params):
    env = {
        k: os.environ[k]
        for k in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "LANG")
        if k in os.environ
    }
    mapping = {
        "host": "PGHOST",
        "port": "PGPORT",
        "user": "PGUSER",
        "password": "PGPASSWORD",
        "dbname": "PGDATABASE",
        "sslmode": "PGSSLMODE",
        "sslrootcert": "PGSSLROOTCERT",
    }
    env.update({dest: str(params[source]) for source, dest in mapping.items() if source in params})
    env["PGCONNECT_TIMEOUT"] = "5"
    return env


def _server(conn):
    major = conn.execute("SELECT current_setting('server_version_num')::int/10000").fetchone()[0]
    version = conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()
    if major != 18 or not version:
        raise OperationError("INCOMPATIBLE_POSTGRESQL_OR_VECTOR")
    return major, version[0]


def backup(database, directory, key):
    url, params = native_url(database)
    executable = _client("pg_dump")
    with psycopg.connect(url, connect_timeout=5) as conn:
        major, vector = _server(conn)
        role = conn.execute(
            "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        if not any(role):
            raise OperationError("BACKUP_REQUIRES_SEPARATE_TECHNICAL_ROLE")
        revision = conn.execute("SELECT version_num FROM public.alembic_version").fetchone()[0]
        owners = conn.execute(
            "SELECT DISTINCT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname IN ('app','auth','public') UNION SELECT DISTINCT pg_get_userbyid(proowner) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('app','auth')"
        ).fetchall()
    metadata = dict(
        formatVersion=1,
        backupId=str(uuid4()),
        createdAt=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        postgresMajor=major,
        vectorVersion=vector,
        alembicRevision=revision,
        owners=sorted({r[0] for r in owners}),
    )
    directory = Path(directory).absolute()
    if directory.is_symlink():
        raise OperationError("UNSAFE_BACKUP_DIRECTORY")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / ("tutoria-" + metadata["backupId"] + ".tutoria-backup")
    try:
        # stderr may contain connection details: it is deliberately not printed.
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(
                [
                    executable,
                    "--format=custom",
                    "--no-password",
                    "--exclude-schema=restore_control",
                    "--exclude-schema=test_harness",
                ],
                stdout=subprocess.PIPE,
                stderr=errors,
                env=_client_env(params),
            )
            watchdog = Timer(3600, process.kill)
            watchdog.start()
            try:
                with path.open("xb") as output:
                    os.chmod(path, 0o600)
                    encrypt_stream(process.stdout, output, key, metadata)
                    output.flush()
                    os.fsync(output.fileno())
                if process.wait(timeout=3600) != 0:
                    raise OperationError("PG_DUMP_FAILED")
            except BaseException:
                process.kill()
                process.wait(timeout=10)
                raise
            finally:
                watchdog.cancel()
                if process.stdout:
                    process.stdout.close()
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while data := source.read(CHUNK):
                digest.update(data)
        return dict(
            backupId=metadata["backupId"],
            path=str(path),
            encryptedBytes=path.stat().st_size,
            sha256=digest.hexdigest(),
        )
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _gate(conn, state):
    conn.execute(
        "UPDATE restore_control.gate SET state=%s,updated_at=clock_timestamp() WHERE singleton",
        (state,),
    )


def _prepare_roles(conn, owners):
    roles = set(owners) | {"tutoria_api", "tutoria_worker", "tutoria_security", "tutoria_backup"}
    for role in roles:
        if not isinstance(role, str) or not 1 <= len(role) <= 63:
            raise OperationError("INVALID_BACKUP_ROLE_METADATA")
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
            bypass = (
                sql.SQL("BYPASSRLS")
                if role in ("tutoria_security", "tutoria_backup")
                else sql.SQL("NOBYPASSRLS")
            )
            conn.execute(
                sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER {}").format(
                    sql.Identifier(role), bypass
                )
            )


def verify_restored(conn):
    _server(conn)
    if conn.execute(
        "SELECT 1 FROM pg_constraint WHERE connamespace IN ('app'::regnamespace,'auth'::regnamespace) AND NOT convalidated LIMIT 1"
    ).fetchone():
        raise OperationError("UNVALIDATED_RESTORE_CONSTRAINT")
    if conn.execute(
        "SELECT 1 FROM app.courses c WHERE c.status IN ('active','archived') AND NOT EXISTS(SELECT 1 FROM app.memberships m WHERE m.course_id=c.id AND m.status='active' AND m.role='coordinator') LIMIT 1"
    ).fetchone():
        raise OperationError("RESTORED_COORDINATION_INVALID")
    if conn.execute(
        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname='tutoria_api'"
    ).fetchone()[0]:
        raise OperationError("RESTORED_API_ROLE_PRIVILEGED")
    if conn.execute(
        "SELECT has_table_privilege('tutoria_api','app.question_keys','SELECT')"
    ).fetchone()[0]:
        raise OperationError("RESTORED_KEYS_EXPOSED")
    if conn.execute(
        "SELECT 1 FROM app.documents d LEFT JOIN app.processing_runs r ON r.id=d.active_run_id WHERE d.active_run_id IS NOT NULL AND (r.id IS NULL OR r.state<>'ready' OR d.status<>'active') LIMIT 1"
    ).fetchone():
        raise OperationError("RESTORED_PUBLICATION_INVALID")
    if conn.execute(
        "SELECT 1 FROM app.child_chunks c JOIN app.documents d ON d.active_run_id=c.run_id JOIN app.processing_runs r ON r.id=c.run_id WHERE NOT EXISTS(SELECT 1 FROM app.chunk_embeddings e WHERE e.child_id=c.id AND e.embedding_space_id=r.embedding_space_id) LIMIT 1"
    ).fetchone():
        raise OperationError("RESTORED_EMBEDDING_MISSING")
    if conn.execute(
        "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='app' AND c.relkind='r' AND EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname IN ('course_id','user_id') AND NOT a.attisdropped) AND c.relname NOT IN ('audit_events') AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity) LIMIT 1"
    ).fetchone():
        raise OperationError("RESTORED_RLS_MISSING")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def replay(database, ledger_path, ledger_key):
    # Writers and the final replay share this small external-ledger lock. A
    # completed purge cannot append between the final snapshot and gate commit.
    with ledger_lock(ledger_path):
        return _replay_locked(database, ledger_path, ledger_key)


def _replay_locked(database, ledger_path, ledger_key):
    url, params = native_url(database)
    if not params["dbname"].startswith("tutoria_restore_"):
        raise OperationError("RESTORE_STAGING_DATABASE_REQUIRED")
    records = read_records(ledger_path, ledger_key)
    with psycopg.connect(url, connect_timeout=5) as conn:
        if not conn.execute("SELECT to_regclass('restore_control.gate')").fetchone()[0]:
            raise OperationError("RESTORE_GATE_REQUIRED")
        _gate(conn, "replaying")
    try:
        with psycopg.connect(url, connect_timeout=5) as conn:
            for record in records:
                conn.execute(
                    "SELECT auth.replay_deletion(%s,%s,%s,%s)",
                    (
                        UUID(record["id"]),
                        record["scope"],
                        UUID(record["subjectId"]),
                        instant(record["requestedAt"]),
                    ),
                )
            conn.execute("UPDATE app.auth_sessions SET revoked_at=clock_timestamp()")
            conn.execute(
                "UPDATE app.account_tokens SET consumed_at=coalesce(consumed_at,clock_timestamp())"
            )
            conn.execute(
                "UPDATE app.jobs SET state=CASE WHEN state='running' THEN 'queued' ELSE state END,lease_token=NULL,lease_until=NULL,locked_by=NULL"
            )
            verify_restored(conn)
            _gate(conn, "verified")
        return dict(state="verified", ledgerRecords=len(records), trafficEnabled=False)
    except BaseException:
        with psycopg.connect(url, connect_timeout=5) as conn:
            _gate(conn, "failed")
        raise


def restore(
    archive, database, key, ledger_path, ledger_key, alembic_config=None, harness_guard=None
):
    url, params = native_url(database)
    if not re.fullmatch(r"tutoria_restore_[a-zA-Z0-9_]{1,40}", params["dbname"]):
        raise OperationError("RESTORE_STAGING_DATABASE_REQUIRED")
    executable = _client("pg_restore")
    with tempfile.TemporaryDirectory(prefix="tutoria-restore-") as directory:
        plaintext = Path(directory) / "archive.dump"
        with Path(archive).open("rb") as source, plaintext.open("xb") as target:
            os.chmod(plaintext, 0o600)
            metadata = decrypt_stream(source, target, key)
        with psycopg.connect(url, connect_timeout=5) as conn:
            allowed = ["public", "information_schema"]
            if harness_guard is not None:
                if params["host"] != "db-test" or harness_guard["case_id"] != params["dbname"]:
                    raise OperationError("RESTORE_HARNESS_GUARD_MISMATCH")
                expected = (
                    UUID(str(harness_guard["run_id"])),
                    params["dbname"],
                    harness_guard["nonce"],
                )
                actual = conn.execute(
                    "SELECT run_id,case_id,nonce FROM test_harness.run_guard"
                ).fetchall()
                if actual != [expected]:
                    raise OperationError("RESTORE_HARNESS_GUARD_MISMATCH")
                allowed.append("test_harness")
            if (
                conn.execute(
                    "SELECT 1 FROM pg_namespace WHERE NOT nspname=ANY(%s) AND left(nspname,3)<>'pg_' LIMIT 1",
                    (allowed,),
                ).fetchone()
                or conn.execute(
                    "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m') LIMIT 1"
                ).fetchone()
            ):
                raise OperationError("RESTORE_DESTINATION_NOT_EMPTY")
            if (
                conn.execute("SELECT current_setting('server_version_num')::int/10000").fetchone()[
                    0
                ]
                != 18
            ):
                raise OperationError("POSTGRESQL_18_REQUIRED")
            _prepare_roles(conn, metadata["owners"])
            conn.execute(
                "CREATE SCHEMA restore_control; CREATE TABLE restore_control.gate(singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),state text NOT NULL,updated_at timestamptz NOT NULL DEFAULT now()); INSERT INTO restore_control.gate(state) VALUES('restoring')"
            )
            conn.execute(
                "GRANT USAGE ON SCHEMA restore_control TO tutoria_security; GRANT SELECT ON restore_control.gate TO tutoria_security"
            )
        result = subprocess.run(
            [
                executable,
                "--exit-on-error",
                "--single-transaction",
                "--no-password",
                "--dbname",
                params["dbname"],
                str(plaintext),
            ],
            env=_client_env(params),
            capture_output=True,
            timeout=14400,
            check=False,
        )
        if result.returncode:
            with psycopg.connect(url, connect_timeout=5) as conn:
                _gate(conn, "failed")
            raise OperationError("PG_RESTORE_FAILED")
        if alembic_config:
            from alembic import command
            from alembic.config import Config

            previous = os.environ.get("MIGRATION_DATABASE_URL")
            try:
                from sqlalchemy.engine import URL

                os.environ["MIGRATION_DATABASE_URL"] = URL.create(
                    "postgresql+psycopg",
                    username=params["user"],
                    password=params.get("password"),
                    host=params["host"],
                    port=int(params.get("port", 5432)),
                    database=params["dbname"],
                    query={k: params[k] for k in ("sslmode", "sslrootcert") if k in params},
                ).render_as_string(hide_password=False)
                command.upgrade(Config(str(alembic_config)), "head")
            finally:
                if previous is None:
                    os.environ.pop("MIGRATION_DATABASE_URL", None)
                else:
                    os.environ["MIGRATION_DATABASE_URL"] = previous
        with psycopg.connect(url, connect_timeout=5) as conn:
            _, vector = _server(conn)
            if vector != metadata["vectorVersion"]:
                _gate(conn, "failed")
                raise OperationError("RESTORE_VECTOR_VERSION_MISMATCH")
        return replay(database, ledger_path, ledger_key)


def expire_backups(directory, key, at=None):
    """Only authenticated, application-owned backup files older than seven days."""
    at = at or datetime.now(timezone.utc)
    directory = Path(directory).absolute()
    if directory.is_symlink() or not directory.is_dir():
        raise OperationError("UNSAFE_BACKUP_DIRECTORY")
    removed = 0
    for path in directory.glob("tutoria-*.tutoria-backup"):
        if path.is_symlink():
            raise OperationError("UNSAFE_BACKUP_FILE")
        with path.open("rb") as stream:
            metadata, _ = read_header(stream)
        if path.name != "tutoria-" + metadata["backupId"] + ".tutoria-backup":
            raise OperationError("BACKUP_ID_CONFLICT")
        if instant(metadata["createdAt"]) <= at - timedelta(days=7):
            # Authenticate the entire archive before deleting an alleged backup.
            with path.open("rb") as source, open(os.devnull, "wb") as sink:
                decrypt_stream(source, sink, key)
            path.unlink()
            removed += 1
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    back = commands.add_parser("backup")
    back.add_argument("--directory", required=True)
    rest = commands.add_parser("restore")
    rest.add_argument("--archive", required=True)
    rest.add_argument(
        "--alembic-config", default=str(Path(__file__).resolve().parents[2] / "alembic.ini")
    )
    commands.add_parser("replay-ledger")
    commands.add_parser("expire-backups").add_argument("--directory", required=True)
    args = parser.parse_args()
    try:
        key = decode_key(os.environ["BACKUP_KEY"])
        if args.command == "backup":
            result = backup(os.environ["BACKUP_DATABASE_URL"], args.directory, key)
        elif args.command == "expire-backups":
            result = {"removed": expire_backups(args.directory, key)}
        else:
            ledger_key = decode_key(os.environ["LEDGER_KEY"])
            if args.command == "restore":
                result = restore(
                    args.archive,
                    os.environ["RESTORE_DATABASE_URL"],
                    key,
                    os.environ["LEDGER_PATH"],
                    ledger_key,
                    args.alembic_config,
                )
            else:
                result = replay(
                    os.environ["RESTORE_DATABASE_URL"], os.environ["LEDGER_PATH"], ledger_key
                )
        print(json.dumps(result))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        # No DSNs, passwords, dump stderr or document text in diagnostic output.
        code = (
            str(exc) if isinstance(exc, (OperationError, LedgerError)) else "BACKUP_RESTORE_FAILED"
        )
        print(json.dumps({"error": {"code": code}}), file=sys.stderr)
        return 130 if isinstance(exc, KeyboardInterrupt) else 2


if __name__ == "__main__":
    raise SystemExit(main())
