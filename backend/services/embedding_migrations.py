"""Versioned reindexing and an atomic, course-wide embedding-space cutover."""

from hashlib import sha256
from uuid import uuid4

from sqlalchemy import text

from backend.api.common import ApiError, enqueue, json_value
from backend.db.connection import one, rows

DEFAULT_CHUNKING = dict(
    child_target_tokens=160,
    parent_max_tokens=1200,
    min_section_tokens=60,
    overlap_ratio=0.12,
    token_estimate_ratio=1.3,
)


def prepare_run(conn, cid, did, vid, space_id, uid, parameters=None):
    parameters = parameters or DEFAULT_CHUNKING
    hashed = sha256(
        json_value(
            dict(
                parser="utf8_nfc_v1", chunker="source_offsets_v1", space=str(space_id), **parameters
            )
        ).encode()
    ).digest()
    previous = one(
        conn,
        "SELECT id,state FROM app.processing_runs WHERE version_id=:vid AND config_hash=:hash",
        vid=vid,
        hash=hashed,
    )
    if previous:
        if previous["state"] in ("failed", "cancelled"):
            raise ApiError("REINDEX_CONFIGURATION_ALREADY_TERMINATED", 409)
        return previous["id"]
    rid = uuid4()
    conn.execute(
        text("""INSERT INTO app.processing_runs(id,course_id,document_id,version_id,config_hash,
      parser_version,chunker_version,embedding_space_id,parameters)
      VALUES(:id,:cid,:did,:vid,:hash,'utf8_nfc_v1','source_offsets_v1',:space,CAST(:parameters AS jsonb))"""),
        dict(
            id=rid,
            cid=cid,
            did=did,
            vid=vid,
            hash=hashed,
            space=space_id,
            parameters=json_value(parameters),
        ),
    )
    enqueue(conn, "ingest_document", rid, cid, uid, 290)
    return rid


def try_cutover(conn, cid):
    """Caller holds the course lock; all document pointers switch in this transaction."""
    config = one(
        conn, "SELECT * FROM app.course_embedding_config WHERE course_id=:cid FOR UPDATE", cid=cid
    )
    if not config or not config["pending_space_id"]:
        return False
    docs = rows(
        conn,
        "SELECT id,active_run_id FROM app.documents WHERE course_id=:cid AND status='active' ORDER BY id FOR UPDATE",
        cid=cid,
    )
    replacements = []
    for doc in docs:
        if not doc["active_run_id"]:
            return False
        latest = one(
            conn,
            """SELECT v.id,v.status,a.version_id active_version_id FROM app.document_versions v
          JOIN app.processing_runs a ON a.id=:active WHERE v.document_id=:did
          ORDER BY v.version_no DESC LIMIT 1""",
            active=doc["active_run_id"],
            did=doc["id"],
        )
        if latest["id"] != latest["active_version_id"] and latest["status"] not in (
            "failed",
            "purged",
        ):
            return False
        target = one(
            conn,
            """SELECT target.id FROM app.processing_runs active JOIN app.processing_runs target
          ON target.course_id=active.course_id AND target.document_id=active.document_id AND target.version_id=active.version_id
          WHERE active.id=:active AND target.embedding_space_id=:space AND target.state='ready'
          ORDER BY target.created_at DESC,target.id DESC LIMIT 1""",
            active=doc["active_run_id"],
            space=config["pending_space_id"],
        )
        if not target:
            return False
        replacements.append((doc["id"], target["id"]))
    # The publication trigger sees the new space; no other transaction sees partial pointers.
    conn.execute(
        text(
            "UPDATE app.course_embedding_config SET active_space_id=pending_space_id,pending_space_id=NULL WHERE course_id=:cid"
        ),
        dict(cid=cid),
    )
    for did, rid in replacements:
        conn.execute(
            text("UPDATE app.documents SET active_run_id=:rid WHERE course_id=:cid AND id=:id"),
            dict(cid=cid, id=did, rid=rid),
        )
    conn.execute(
        text("UPDATE app.courses SET corpus_revision=corpus_revision+1 WHERE id=:cid"),
        dict(cid=cid),
    )
    return True


def start_migration(conn, cid, target, uid):
    conn.execute(text("SELECT auth.lock_course(:id)"), dict(id=cid))
    config = one(
        conn, "SELECT * FROM app.course_embedding_config WHERE course_id=:cid FOR UPDATE", cid=cid
    )
    space = one(conn, "SELECT id FROM app.embedding_spaces WHERE id=:id AND enabled", id=target)
    if not config or not space:
        raise ApiError("EMBEDDING_SPACE_NOT_CONFIGURED", 422)
    if target == config["active_space_id"]:
        return {"activeSpaceId": str(target), "pendingSpaceId": None, "status": "completed"}
    if config["pending_space_id"] and config["pending_space_id"] != target:
        raise ApiError("EMBEDDING_MIGRATION_IN_PROGRESS")
    conn.execute(
        text(
            "UPDATE app.course_embedding_config SET pending_space_id=:target WHERE course_id=:cid"
        ),
        dict(cid=cid, target=target),
    )
    sources = rows(
        conn,
        """SELECT d.id,r.version_id,r.parameters FROM app.documents d
       JOIN app.processing_runs r ON r.id=d.active_run_id AND r.course_id=d.course_id
       WHERE d.course_id=:cid AND d.status='active' ORDER BY d.id""",
        cid=cid,
    )
    for source in sources:
        prepare_run(
            conn, cid, source["id"], source["version_id"], target, uid, source["parameters"]
        )
    completed = try_cutover(conn, cid)
    return {
        "activeSpaceId": str(target if completed else config["active_space_id"]),
        "pendingSpaceId": None if completed else str(target),
        "status": "completed" if completed else "processing",
    }


def cancel_migration(conn, cid):
    conn.execute(text("SELECT auth.lock_course(:id)"), dict(id=cid))
    config = one(
        conn, "SELECT * FROM app.course_embedding_config WHERE course_id=:cid FOR UPDATE", cid=cid
    )
    if not config or not config["pending_space_id"]:
        return {"status": "idle"}
    # A fenced technical function cancels jobs from every uploader, not just the coordinator.
    conn.execute(text("SELECT auth.cancel_embedding_migration(:cid)"), dict(cid=cid))
    return {"status": "cancelled", "activeSpaceId": str(config["active_space_id"])}
