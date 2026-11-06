import base64
from uuid import UUID, uuid4

import pytest
from backend.db.connection import one, transaction
from backend.services.ledger import read_records
from backend.workers.main import Lease
from backend.workers.purge import purge
from sqlalchemy import text

from tests.integration.test_database import seed
from tests.integration.test_materials import configure, prepare, publish


def run_purge(url, monkeypatch, tmp_path):
    configure(monkeypatch, url.replace("tutoria_api:", "tutoria_worker:"))
    monkeypatch.setenv("LEDGER_KEY", base64.b64encode(b"l" * 32).decode())
    monkeypatch.setenv("LEDGER_PATH", str(tmp_path))
    with transaction() as conn:
        job = one(conn, "SELECT * FROM auth.claim_job('purge-test')")
    assert job["kind"] == "purge"
    purge(job, Lease(job))
    return job


def test_DAT_25_document_purge_redacts_source_derivatives(database, monkeypatch, tmp_path):
    admin, url = database
    uid, cid, space = prepare(admin, url, monkeypatch)
    accepted, _ = publish(admin, url, monkeypatch, uid, cid, b"SOURCE_SENTINEL_document_purge")
    generation, conversation, message, artifact, concept = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    running = uuid4()
    with admin.begin() as conn:
        parent = conn.execute(
            text("SELECT id FROM app.parent_chunks WHERE run_id=:id"),
            dict(id=UUID(accepted["runId"])),
        ).scalar_one()
        base = dict(
            cid=cid,
            uid=uid,
            gid=generation,
            tid=conversation,
            mid=message,
            pid=parent,
            rid=UUID(accepted["runId"]),
            concept=concept,
        )
        conn.execute(
            text(
                "INSERT INTO app.generation_runs(id,course_id,user_id,kind,status,provider,model_name,model_revision,prompt_version,parameters,corpus_revision,graph_revision,request_hash) VALUES(:gid,:cid,:uid,'chat','completed','ollama','synthetic','v1','v1','{}',1,0,decode(repeat('00',32),'hex'))"
            ),
            base,
        )
        conn.execute(
            text(
                "INSERT INTO app.generation_runs(id,course_id,user_id,kind,status,provider,model_name,model_revision,prompt_version,parameters,corpus_revision,graph_revision,request_hash) VALUES(:gid,:cid,:uid,'chat','running','ollama','synthetic','v1','v1','{}',1,0,decode(repeat('00',32),'hex'))"
            ),
            dict(base, gid=running),
        )
        conn.execute(
            text(
                "INSERT INTO app.generation_sources(course_id,generation_id,parent_id,run_id,rank,score) VALUES(:cid,:gid,:pid,:rid,1,1)"
            ),
            dict(base, gid=running),
        )
        conn.execute(
            text(
                "INSERT INTO app.conversations(id,course_id,user_id,title) VALUES(:tid,:cid,:uid,'Synthetic')"
            ),
            base,
        )
        conn.execute(
            text(
                'INSERT INTO app.messages(id,course_id,user_id,conversation_id,sequence_no,role,status,parts,generation_id) VALUES(:mid,:cid,:uid,:tid,1,\'assistant\',\'completed\',\'[{"type":"markdown","content":"SOURCE_SENTINEL_document_purge"}]\',:gid)'
            ),
            base,
        )
        conn.execute(
            text(
                "INSERT INTO app.generation_sources(course_id,generation_id,parent_id,run_id,rank,score) VALUES(:cid,:gid,:pid,:rid,1,1)"
            ),
            base,
        )
        conn.execute(
            text(
                "INSERT INTO app.citations(id,course_id,user_id,message_id,generation_id,parent_id,ordinal,label,locator) VALUES(:id,:cid,:uid,:mid,:gid,:pid,1,'Sensitive title','{}')"
            ),
            dict(base, id=uuid4()),
        )
        conn.execute(
            text(
                "INSERT INTO app.generated_artifacts(id,course_id,user_id,generation_id,kind,title,content) VALUES(:id,:cid,:uid,:gid,'summary','Sensitive title','{\"text\":\"SOURCE_SENTINEL_document_purge\"}')"
            ),
            dict(base, id=artifact),
        )
        conn.execute(
            text(
                "INSERT INTO app.concepts(id,course_id,canonical_key,label,description,origin,label_origin,description_origin,status) VALUES(:concept,:cid,:key,'SOURCE_SENTINEL_document_purge','SOURCE_SENTINEL_document_purge','llm','llm','llm','approved')"
            ),
            dict(base, key=str(concept)),
        )
        conn.execute(
            text(
                "INSERT INTO app.concept_sources(course_id,concept_id,parent_id,run_id,origin) VALUES(:cid,:concept,:pid,:rid,'llm')"
            ),
            base,
        )
    configure(monkeypatch, url)
    request = uuid4()
    with transaction(uid) as conn:
        conn.execute(
            text("SELECT auth.request_document_delete(:cid,:did,:rid)"),
            dict(cid=cid, did=UUID(accepted["materialId"]), rid=request),
        )
        assert one(conn, "SELECT parts FROM app.messages WHERE id=:id", id=message)["parts"] == []
        assert (
            one(conn, "SELECT status FROM app.generation_runs WHERE id=:id", id=running)["status"]
            == "cancelled"
        )
    run_purge(url, monkeypatch, tmp_path)
    with admin.connect() as conn:
        for table in (
            "documents",
            "document_versions",
            "file_contents",
            "parent_chunks",
            "child_chunks",
            "chunk_embeddings",
            "processing_runs",
        ):
            assert (
                conn.execute(
                    text(f"SELECT count(*) FROM app.{table} WHERE course_id=:cid"), dict(cid=cid)
                ).scalar_one()
                == 0
            )
        assert (
            conn.execute(
                text("SELECT status FROM app.citations WHERE message_id=:id"), dict(id=message)
            ).scalar_one()
            == "source_removed"
        )
        assert (
            conn.execute(
                text("SELECT content FROM app.generated_artifacts WHERE id=:id"), dict(id=artifact)
            ).scalar_one()
            == {}
        )
        assert (
            conn.execute(
                text("SELECT label FROM app.concepts WHERE id=:id"), dict(id=concept)
            ).scalar_one()
            == "Concepto retirado"
        )
        assert (
            conn.execute(
                text("SELECT state FROM app.deletion_requests WHERE id=:id"), dict(id=request)
            ).scalar_one()
            == "completed"
        )
    assert read_records(tmp_path, b"l" * 32)[0]["subjectId"] == accepted["materialId"]


def test_account_purge_preserves_shared_materials_and_other_accounts(
    database, monkeypatch, tmp_path
):
    admin, url = database
    uid, cid, space = prepare(admin, url, monkeypatch)
    accepted, _ = publish(admin, url, monkeypatch, uid, cid)
    other, other_course = seed(admin)
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app.memberships(course_id,user_id,role) VALUES(:cid,:uid,'coordinator')"
            ),
            dict(cid=cid, uid=other),
        )
        failed_generation, failed_exam = uuid4(), uuid4()
        conn.execute(
            text(
                "INSERT INTO app.generation_runs(id,course_id,user_id,kind,status,provider,model_name,model_revision,prompt_version,parameters,corpus_revision,graph_revision,request_hash) VALUES(:gid,:cid,:uid,'exam','failed','ollama','synthetic','v1','v1','{}',1,0,decode(repeat('00',32),'hex'))"
            ),
            dict(gid=failed_generation, cid=cid, uid=uid),
        )
        conn.execute(
            text(
                "INSERT INTO app.exams(id,course_id,user_id,generation_id,title,kind,status,duration_seconds) VALUES(:id,:cid,:uid,:gid,'Failed synthetic','practice','failed',600)"
            ),
            dict(id=failed_exam, cid=cid, uid=uid, gid=failed_generation),
        )
    configure(monkeypatch, url)
    request = uuid4()
    with transaction(uid) as conn:
        conn.execute(
            text("SELECT auth.request_scope_delete('account',:uid,:id,:confirmation)"),
            dict(uid=uid, id=request, confirmation="u_" + uid.hex),
        )
    run_purge(url, monkeypatch, tmp_path)
    with admin.connect() as conn:
        assert not conn.execute(text("SELECT id FROM app.users WHERE id=:id"), dict(id=uid)).first()
        assert conn.execute(text("SELECT id FROM app.users WHERE id=:id"), dict(id=other)).first()
        assert (
            conn.execute(
                text("SELECT uploaded_by FROM app.documents WHERE id=:id"),
                dict(id=UUID(accepted["materialId"])),
            ).scalar_one()
            is None
        )
        assert conn.execute(
            text("SELECT bytes FROM app.file_contents WHERE version_id=:id"),
            dict(id=UUID(accepted["versionId"])),
        ).scalar_one()
        assert (
            conn.execute(
                text("SELECT state FROM app.deletion_requests WHERE id=:id"), dict(id=request)
            ).scalar_one()
            == "completed"
        )


def test_course_purge_requires_confirmation_and_keeps_global_users(database, monkeypatch, tmp_path):
    admin, url = database
    uid, cid, space = prepare(admin, url, monkeypatch)
    accepted, _ = publish(admin, url, monkeypatch, uid, cid)
    configure(monkeypatch, url)
    with pytest.raises(Exception, match="CONFIRMATION_REQUIRED"):
        with transaction(uid) as conn:
            conn.execute(
                text("SELECT auth.request_scope_delete('course',:cid,:id,'wrong')"),
                dict(cid=cid, id=uuid4()),
            )
    request = uuid4()
    with transaction(uid) as conn:
        conn.execute(
            text("SELECT auth.request_scope_delete('course',:cid,:id,'Synthetic course')"),
            dict(cid=cid, id=request),
        )
    run_purge(url, monkeypatch, tmp_path)
    with admin.connect() as conn:
        assert not conn.execute(
            text("SELECT id FROM app.courses WHERE id=:id"), dict(id=cid)
        ).first()
        assert conn.execute(text("SELECT id FROM app.users WHERE id=:id"), dict(id=uid)).first()
        assert (
            conn.execute(
                text("SELECT state FROM app.deletion_requests WHERE id=:id"), dict(id=request)
            ).scalar_one()
            == "completed"
        )


def test_account_delete_rejects_last_coordinator(database, monkeypatch):
    admin, url = database
    uid, cid = seed(admin)
    configure(monkeypatch, url)
    with pytest.raises(Exception, match="LAST_COORDINATOR"):
        with transaction(uid) as conn:
            conn.execute(
                text("SELECT auth.request_scope_delete('account',:uid,:id,:confirmation)"),
                dict(uid=uid, id=uuid4(), confirmation="u_" + uid.hex),
            )
    with admin.connect() as conn:
        assert (
            conn.execute(
                text("SELECT status FROM app.users WHERE id=:id"), dict(id=uid)
            ).scalar_one()
            == "active"
        )


def test_retention_does_not_remove_an_old_active_original(database, monkeypatch):
    admin, url = database
    uid, cid, space = prepare(admin, url, monkeypatch)
    accepted, _ = publish(admin, url, monkeypatch, uid, cid)
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE app.document_versions SET superseded_at=clock_timestamp()-interval '60 days' WHERE id=:id"
            ),
            dict(id=UUID(accepted["versionId"])),
        )
        conn.execute(
            text(
                "UPDATE app.processing_runs SET finished_at=clock_timestamp()-interval '60 days' WHERE id=:id"
            ),
            dict(id=UUID(accepted["runId"])),
        )
        conn.execute(text("SELECT auth.retention_cleanup()"))
        assert conn.execute(
            text("SELECT id FROM app.document_versions WHERE id=:id"),
            dict(id=UUID(accepted["versionId"])),
        ).first()
        assert conn.execute(
            text("SELECT id FROM app.processing_runs WHERE id=:id"),
            dict(id=UUID(accepted["runId"])),
        ).first()
