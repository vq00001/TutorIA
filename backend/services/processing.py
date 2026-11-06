"""Create reproducible processing runs for accepted originals."""

from hashlib import sha256
from uuid import uuid4

from sqlalchemy import text

from backend.api.common import ApiError, enqueue, json_value
from backend.db.connection import one

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
