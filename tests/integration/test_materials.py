from uuid import UUID, uuid4

import pytest
from backend.api.courses import list_courses
from backend.api.materials import accept_file, list_materials
from backend.db.connection import engine, one, rows, transaction
from backend.repositories.vector import PgVectorStore
from backend.workers.ingest import ingest
from backend.workers.main import Lease
from sqlalchemy import text

from tests.integration.test_database import seed


def prepare(admin, url, monkeypatch):
    uid, cid = seed(admin)
    sid = uuid4()
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app.embedding_spaces(id,provider,model_name,model_revision,dimension) VALUES(:id,'ollama','synthetic','fixture-v1',3)"
            ),
            dict(id=sid),
        )
        conn.execute(
            text(
                "INSERT INTO app.course_embedding_config(course_id,active_space_id) VALUES(:cid,:sid)"
            ),
            dict(cid=cid, sid=sid),
        )
    configure(monkeypatch, url)
    return uid, cid, sid


def configure(monkeypatch, url):
    if engine.cache_info().currsize:
        engine().dispose()
    engine.cache_clear()
    monkeypatch.setenv("DATABASE_URL", url)


def publish(
    admin, url, monkeypatch, uid, cid, body=b"# Synthetic\nA real source with known vectors."
):
    with transaction(uid) as conn:
        accepted = accept_file(conn, cid, uid, "synthetic.md", body)
    configure(monkeypatch, url.replace("tutoria_api:", "tutoria_worker:"))
    with transaction() as conn:
        job = one(conn, "SELECT * FROM auth.claim_job('fixture-worker')")
    monkeypatch.setattr(
        "backend.workers.ingest.embeddings",
        lambda space, texts, timeout: ["[1,0,0]" for _ in texts],
    )
    ingest(job, Lease(job))
    return accepted, job


def test_DAT_04_10_11_vectors_and_course_scope(database, monkeypatch):
    admin, url = database
    uid, cid, sid = prepare(admin, url, monkeypatch)
    accepted, _ = publish(admin, url, monkeypatch, uid, cid)
    configure(monkeypatch,url)
    with transaction(uid) as conn:
        assert list_courses(conn=conn)["data"][0]["id"] == cid
        assert list_materials(cid, conn=conn)["data"][0]["versionId"] == UUID(accepted["versionId"])
        queued = one(
            conn,
            "SELECT deadline_at FROM app.jobs WHERE kind='extract_concepts' AND target_id=:id",
            id=UUID(accepted["runId"]),
        )
        assert queued["deadline_at"] is None
        hits = PgVectorStore(conn, cid, sid).search([1, 0, 0])
        assert len(hits) == 1 and hits[0]["score"] == pytest.approx(1)
        assert "# Synthetic" not in hits[0]["text"] or "real source" in hits[0]["text"]
        assert not PgVectorStore(conn, cid, sid).search([-1, 0, 0])
        with pytest.raises(ValueError, match="EMBEDDING_SPACE_MISMATCH"):
            PgVectorStore(conn, cid, uuid4()).search([1, 0, 0])
    other, other_course = seed(admin)
    with transaction(other) as conn:
        assert not rows(conn, "SELECT id FROM app.parent_chunks WHERE course_id=:cid", cid=cid)


def test_DAT_08_failed_replacement_preserves_published_original(database, monkeypatch):
    admin, url = database
    uid, cid, sid = prepare(admin, url, monkeypatch)
    accepted, _ = publish(admin, url, monkeypatch, uid, cid)
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        accept_file(
            conn,
            cid,
            uid,
            "replacement.txt",
            b"New version not yet published.",
            UUID(accepted["materialId"]),
        )
        d = one(
            conn,
            "SELECT active_run_id FROM app.documents WHERE id=:id",
            id=UUID(accepted["materialId"]),
        )
        assert str(d["active_run_id"]) == accepted["runId"]
    with admin.begin() as conn:
        conn.execute(text("UPDATE app.jobs SET state='cancelled' WHERE kind='extract_concepts'"))
    configure(monkeypatch, url.replace("tutoria_api:", "tutoria_worker:"))
    with transaction() as conn:
        job = one(conn, "SELECT * FROM auth.claim_job('fixture-worker')")

    def unavailable(*args):
        raise ValueError("PROVIDER_UNAVAILABLE")

    monkeypatch.setattr("backend.workers.ingest.embeddings", unavailable)
    with pytest.raises(ValueError, match="PROVIDER_UNAVAILABLE"):
        ingest(job, Lease(job))
    with transaction(uid) as conn:
        d = one(
            conn,
            "SELECT active_run_id FROM app.documents WHERE id=:id",
            id=UUID(accepted["materialId"]),
        )
        assert str(d["active_run_id"]) == accepted["runId"]
        assert PgVectorStore(conn, cid, sid).search([1, 0, 0])


def test_DAT_09_expired_lease_cannot_publish(database, monkeypatch):
    admin, url = database
    uid, cid, sid = prepare(admin, url, monkeypatch)
    with transaction(uid) as conn:
        accepted = accept_file(
            conn, cid, uid, "source.txt", b"Source that must remain unpublished."
        )
    configure(monkeypatch, url.replace("tutoria_api:", "tutoria_worker:"))
    with transaction() as conn:
        job = one(conn, "SELECT * FROM auth.claim_job('fixture-worker')")
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE app.jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=:id"
            ),
            dict(id=job["id"]),
        )
    monkeypatch.setattr(
        "backend.workers.ingest.embeddings",
        lambda space, texts, timeout: ["[1,0,0]" for _ in texts],
    )
    with pytest.raises(ValueError, match="LEASE_LOST"):
        ingest(job, Lease(job))
    with transaction(uid) as conn:
        assert not one(
            conn,
            "SELECT active_run_id FROM app.documents WHERE id=:id",
            id=UUID(accepted["materialId"]),
        )["active_run_id"]
        assert not PgVectorStore(conn, cid, sid).search([1, 0, 0])
