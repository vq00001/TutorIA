from uuid import UUID, uuid4

import pytest
from backend.api.materials import accept_file
from backend.db.connection import one, rows, transaction
from backend.repositories.vector import PgVectorStore
from backend.services.embedding_migrations import cancel_migration, start_migration
from backend.workers.ingest import ingest
from backend.workers.main import Lease
from sqlalchemy import text

from tests.integration.test_materials import configure, prepare, publish


def target_space(admin):
    target = uuid4()
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app.embedding_spaces(id,provider,model_name,model_revision,dimension) VALUES(:id,'ollama','second-synthetic','fixture-v2',3)"
            ),
            dict(id=target),
        )
        conn.execute(
            text(
                "UPDATE app.jobs SET state='cancelled' WHERE kind='extract_concepts' AND state='queued'"
            )
        )
    return target


def run_next(url, monkeypatch):
    configure(monkeypatch, url.replace("tutoria_api:", "tutoria_worker:"))
    with transaction() as conn:
        job = one(conn, "SELECT * FROM auth.claim_job('embedding-migration-fixture')")
    assert job and job["kind"] == "ingest_document"
    monkeypatch.setattr(
        "backend.workers.ingest.embeddings",
        lambda space, texts, timeout: ["[0,1,0]" for _ in texts],
    )
    ingest(job, Lease(job))


def test_DAT_12_all_document_pointers_switch_with_query_space(database, monkeypatch):
    admin, url = database
    uid, cid, old = prepare(admin, url, monkeypatch)
    original, _ = publish(admin, url, monkeypatch, uid, cid)
    target = target_space(admin)
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        start_migration(conn, cid, target, uid)
        assert PgVectorStore(conn, cid, old).search([1, 0, 0])
        with pytest.raises(ValueError, match="EMBEDDING_SPACE_MISMATCH"):
            PgVectorStore(conn, cid, target).search([0, 1, 0])
    run_next(url, monkeypatch)
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        cfg = one(conn, "SELECT * FROM app.course_embedding_config WHERE course_id=:cid", cid=cid)
        assert cfg["active_space_id"] == target and cfg["pending_space_id"] is None
        result = PgVectorStore(conn, cid, target).search([0, 1, 0])
        assert len(result) == 1 and str(result[0]["run_id"]) != original["runId"]
        with pytest.raises(ValueError, match="EMBEDDING_SPACE_MISMATCH"):
            PgVectorStore(conn, cid, old).search([1, 0, 0])


def test_upload_during_pending_migration_gets_both_spaces(database, monkeypatch):
    admin, url = database
    uid, cid, old = prepare(admin, url, monkeypatch)
    publish(admin, url, monkeypatch, uid, cid)
    target = target_space(admin)
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        start_migration(conn, cid, target, uid)
        second = accept_file(
            conn, cid, uid, "second.md", b"Synthetic material accepted during migration."
        )
        runs = rows(
            conn,
            "SELECT embedding_space_id FROM app.processing_runs WHERE version_id=:id",
            id=UUID(second["versionId"]),
        )
        assert {r["embedding_space_id"] for r in runs} == {old, target}
    for _ in range(3):
        with admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE app.jobs SET state='cancelled' WHERE kind='extract_concepts' AND state='queued'"
                )
            )
        run_next(url, monkeypatch)
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        cfg = one(conn, "SELECT * FROM app.course_embedding_config WHERE course_id=:cid", cid=cid)
        assert cfg["active_space_id"] == target and cfg["pending_space_id"] is None
        assert len(PgVectorStore(conn, cid, target).search([0, 1, 0])) == 2


def test_cancel_pending_migration_keeps_old_corpus(database, monkeypatch):
    admin, url = database
    uid, cid, old = prepare(admin, url, monkeypatch)
    publish(admin, url, monkeypatch, uid, cid)
    target = target_space(admin)
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        start_migration(conn, cid, target, uid)
        assert cancel_migration(conn, cid)["status"] == "cancelled"
        assert PgVectorStore(conn, cid, old).search([1, 0, 0])
        assert (
            one(
                conn,
                "SELECT pending_space_id FROM app.course_embedding_config WHERE course_id=:cid",
                cid=cid,
            )["pending_space_id"]
            is None
        )


def test_DAT_24_withdrawal_hides_originals_and_fragments_before_purge(database, monkeypatch):
    admin, url = database
    uid, cid, old = prepare(admin, url, monkeypatch)
    original, _ = publish(admin, url, monkeypatch, uid, cid)
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        conn.execute(
            text("SELECT auth.request_document_delete(:cid,:did,:rid)"),
            dict(cid=cid, did=UUID(original["materialId"]), rid=uuid4()),
        )
    with transaction(uid) as conn:
        for table in (
            "documents",
            "file_contents",
            "document_versions",
            "processing_runs",
            "parent_chunks",
            "child_chunks",
            "chunk_embeddings",
        ):
            assert not rows(
                conn, f"SELECT course_id FROM app.{table} WHERE course_id=:cid", cid=cid
            )
    with admin.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM app.file_contents WHERE course_id=:cid"), dict(cid=cid)
            ).scalar_one()
            == 1
        )
