import os
from uuid import UUID, uuid4

import psycopg
import pytest
from backend.db.connection import transaction
from backend.services.operations_backup import (
    OperationError,
    backup,
    native_url,
    restore,
    verify_restored,
)
from psycopg import sql
from sqlalchemy import text

from tests.integration.test_materials import configure, prepare, publish
from tests.integration.test_purge import run_purge


def test_restore_verifier_rejects_disabled_row_security(database):
    admin, url = database
    native, _ = native_url(url.replace("tutoria_api:", "postgres:"))
    with psycopg.connect(native) as conn:
        conn.execute("ALTER TABLE app.conversations DISABLE ROW LEVEL SECURITY")
        with pytest.raises(OperationError, match="RESTORED_RLS_MISSING"):
            verify_restored(conn)
        conn.rollback()


def test_DAT_26_restore_replays_external_ledger_before_opening_gate(
    database, monkeypatch, tmp_path
):
    admin, url = database
    uid, cid, space = prepare(admin, url, monkeypatch)
    accepted, _ = publish(admin, url, monkeypatch, uid, cid, b"SENTINEL_MUST_NOT_RESURRECT")
    source, params = native_url(url.replace("tutoria_api:", "postgres:"))
    archive = backup(source, tmp_path / "backups", b"b" * 32)
    request = uuid4()
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        conn.execute(
            text("SELECT auth.request_document_delete(:cid,:did,:rid)"),
            dict(cid=cid, did=UUID(accepted["materialId"]), rid=request),
        )
    ledger = tmp_path / "ledger"
    run_purge(url, monkeypatch, ledger)

    run = UUID(os.environ["TEST_RUN_ID"])
    target = "tutoria_restore_" + run.hex[:24] + "_" + uuid4().hex[:8]
    guard = dict(run_id=run, case_id=target, nonce=os.environ["TEST_NONCE"])
    target_params = {**params, "dbname": target}
    with psycopg.connect(**{**params, "dbname": "postgres"}, autocommit=True) as cluster:
        cluster.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))
        try:
            with psycopg.connect(**target_params) as conn:
                conn.execute(
                    "CREATE SCHEMA test_harness; CREATE TABLE test_harness.run_guard(run_id uuid,case_id text,nonce text)"
                )
                conn.execute(
                    "INSERT INTO test_harness.run_guard VALUES(%s,%s,%s)",
                    (run, target, guard["nonce"]),
                )
            from psycopg.conninfo import make_conninfo

            destination = make_conninfo(**target_params)
            result = restore(
                archive["path"], destination, b"b" * 32, ledger, b"l" * 32, harness_guard=guard
            )
            assert result == dict(state="verified", ledgerRecords=1, trafficEnabled=False)
            with psycopg.connect(**target_params) as conn:
                assert conn.execute("SELECT auth.restore_is_ready()").fetchone()[0]
                assert not conn.execute(
                    "SELECT 1 FROM app.documents WHERE id=%s", (UUID(accepted["materialId"]),)
                ).fetchone()
                assert conn.execute("SELECT count(*) FROM app.file_contents").fetchone()[0] == 0
                assert conn.execute("SELECT 1 FROM app.users WHERE id=%s", (uid,)).fetchone()
                assert (
                    conn.execute(
                        "SELECT state FROM app.deletion_requests WHERE id=%s", (request,)
                    ).fetchone()[0]
                    == "completed"
                )
                assert conn.execute(
                    "SELECT run_id,case_id,nonce FROM test_harness.run_guard"
                ).fetchall() == [(run, target, guard["nonce"])]
        finally:
            # Exact case marker, name and cluster connection are checked before DROP.
            with psycopg.connect(**target_params) as conn:
                assert conn.execute(
                    "SELECT run_id,case_id,nonce FROM test_harness.run_guard"
                ).fetchall() == [(run, target, guard["nonce"])]
            cluster.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(target)))
