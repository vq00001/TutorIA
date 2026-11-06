import re
from math import isfinite, sqrt
from uuid import uuid4

import requests
from sqlalchemy import text

from backend.api.common import enqueue, json_value
from backend.db.connection import one, transaction
from backend.services.documents import validate_file
from backend.services.embedding_migrations import prepare_run, try_cutover


def source_chunks(content, config):
    """Preserve source whitespace and Unicode offsets exactly."""
    parent_limit = int(config["parent_max_tokens"] / config["token_estimate_ratio"])
    child_limit = int(config["child_target_tokens"] / config["token_estimate_ratio"])
    words = list(re.finditer(r"\S+", content))
    parents = []
    for start in range(0, len(words), parent_limit):
        span = words[start : start + parent_limit]
        a, b = span[0].start(), span[-1].end()
        body = content[a:b]
        local = list(re.finditer(r"\S+", body))
        children = []
        step = max(1, int(child_limit * (1 - config["overlap_ratio"])))
        for pos in range(0, len(local), step):
            section = local[pos : pos + child_limit]
            x, y = section[0].start(), section[-1].end()
            children.append((x, y, body[x:y]))
            if pos + child_limit >= len(local):
                break
        parents.append(
            dict(
                id=uuid4(),
                text=body,
                children=children,
                locator=dict(
                    kind="text",
                    lineStart=content.count("\n", 0, a) + 1,
                    lineEnd=content.count("\n", 0, b) + 1,
                ),
            )
        )
    return parents


def vector_text(vector, dimension):
    if (
        not isinstance(vector, list)
        or len(vector) != dimension
        or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in vector)
    ):
        raise ValueError("INVALID_VECTOR")
    try:
        values = [float(x) for x in vector]
    except (OverflowError, ValueError) as exc:
        raise ValueError("INVALID_VECTOR") from exc
    if not all(isfinite(x) for x in values):
        raise ValueError("INVALID_VECTOR")
    scale = max(abs(x) for x in values)
    if scale == 0:
        raise ValueError("INVALID_VECTOR")
    norm = sqrt(sum((x / scale) ** 2 for x in values))
    return "[" + ",".join(str((x / scale) / norm) for x in values) + "]"


def embeddings(space, texts, timeout):
    import os

    if space["provider"] != "ollama":
        raise ValueError("UNSUPPORTED_PROVIDER")
    revision = requests.get(
        os.environ["OLLAMA_URL"].rstrip("/") + "/api/tags", timeout=(5, min(5, max(1, timeout)))
    )
    revision.raise_for_status()
    installed = next(
        (
            m
            for m in revision.json()["models"]
            if m["name"] in (space["model_name"], space["model_name"] + ":latest")
        ),
        None,
    )
    if not installed or installed["digest"] != space["model_revision"]:
        raise ValueError("MODEL_REVISION_CHANGED")
    response = requests.post(
        os.environ["OLLAMA_URL"].rstrip("/") + "/api/embed",
        json=dict(model=space["model_name"], input=texts),
        timeout=(5, max(1, timeout)),
    )
    response.raise_for_status()
    result = response.json()["embeddings"]
    if len(result) != len(texts):
        raise ValueError("INVALID_EMBEDDING_BATCH")
    return [vector_text(v, space["dimension"]) for v in result]


def ingest(job, lease):
    with transaction(job["requested_by"]) as conn:
        run = one(
            conn,
            """SELECT r.*,v.original_name,b.bytes FROM app.processing_runs r
          JOIN app.document_versions v ON v.id=r.version_id JOIN app.file_contents b ON b.version_id=v.id
          WHERE r.id=:id AND r.course_id=:cid""",
            id=job["target_id"],
            cid=job["course_id"],
        )
        if not run:
            raise ValueError("NOT_AUTHORIZED")
        if run["state"] == "ready":
            lease.complete(conn)
            return
        if run["state"] not in ("queued", "running"):
            raise ValueError("RUN_NOT_PROCESSABLE")
        config = one(
            conn,
            "SELECT active_space_id,pending_space_id FROM app.course_embedding_config WHERE course_id=:cid",
            cid=job["course_id"],
        )
        if not config or run["embedding_space_id"] not in (
            config["active_space_id"],
            config["pending_space_id"],
        ):
            conn.execute(
                text(
                    "UPDATE app.processing_runs SET state='cancelled',finished_at=now(),error_code='SPACE_RETIRED' WHERE id=:id"
                ),
                dict(id=run["id"]),
            )
            lease.complete(conn)
            return
        space = one(
            conn, "SELECT * FROM app.embedding_spaces WHERE id=:id", id=run["embedding_space_id"]
        )
        conn.execute(
            text(
                "UPDATE app.processing_runs SET state='running',started_at=now() WHERE id=:id AND state='queued'"
            ),
            dict(id=run["id"]),
        )
        conn.execute(
            text(
                "UPDATE app.document_versions SET status='processing' WHERE id=:id AND status IN ('uploaded','failed')"
            ),
            dict(id=run["version_id"]),
        )
    _, _, content, _ = validate_file(run["original_name"], bytes(run["bytes"]))
    parents = source_chunks(content, run["parameters"])
    vectors = []
    inputs = [space["document_prefix"] + c[2] for p in parents for c in p["children"]]
    for start in range(0, len(inputs), 32):
        lease.check()
        vectors.extend(embeddings(space, inputs[start : start + 32], lease.remaining()))
    with transaction(job["requested_by"]) as conn:
        conn.execute(
            text("SELECT id FROM app.courses WHERE id=:id FOR UPDATE"), dict(id=job["course_id"])
        )
        config = one(
            conn,
            "SELECT active_space_id,pending_space_id FROM app.course_embedding_config WHERE course_id=:cid",
            cid=job["course_id"],
        )
        if config and config["pending_space_id"]:
            conn.execute(
                text(
                    "SELECT id FROM app.documents WHERE course_id=:cid AND status='active' ORDER BY id FOR UPDATE"
                ),
                dict(cid=job["course_id"]),
            )
        doc = one(
            conn, "SELECT * FROM app.documents WHERE id=:id FOR UPDATE", id=run["document_id"]
        )
        if not doc or doc["status"] != "active":
            raise ValueError("SOURCE_REMOVED")
        if run["embedding_space_id"] not in (config["active_space_id"], config["pending_space_id"]):
            conn.execute(
                text(
                    "UPDATE app.processing_runs SET state='cancelled',finished_at=now(),error_code='SPACE_RETIRED' WHERE id=:id"
                ),
                dict(id=run["id"]),
            )
            lease.complete(conn)
            return
        active = one(
            conn,
            """SELECT v.version_no FROM app.processing_runs r
         JOIN app.document_versions v ON v.id=r.version_id WHERE r.id=:id""",
            id=doc["active_run_id"],
        )
        current = one(
            conn, "SELECT version_no FROM app.document_versions WHERE id=:id", id=run["version_id"]
        )
        if active and active["version_no"] > current["version_no"]:
            raise ValueError("SUPERSEDED_RUN")
        lease.validate(conn)
        conn.execute(text("DELETE FROM app.parent_chunks WHERE run_id=:id"), dict(id=run["id"]))
        n = 0
        for ordinal, p in enumerate(parents):
            conn.execute(
                text("""INSERT INTO app.parent_chunks(id,course_id,document_id,run_id,ordinal,text,locator,token_estimate)
              VALUES(:id,:cid,:did,:rid,:ordinal,:body,CAST(:locator AS jsonb),:tokens)"""),
                dict(
                    id=p["id"],
                    cid=job["course_id"],
                    did=run["document_id"],
                    rid=run["id"],
                    ordinal=ordinal,
                    body=p["text"],
                    locator=json_value(p["locator"]),
                    tokens=max(1, int(len(p["text"].split()) * 1.3)),
                ),
            )
            for a, b, body in p["children"]:
                child = uuid4()
                conn.execute(
                    text("""INSERT INTO app.child_chunks(id,course_id,document_id,run_id,parent_id,ordinal,text,embedding_text,start_offset,end_offset,token_estimate)
                 VALUES(:id,:cid,:did,:rid,:pid,:ordinal,:body,:embedding,:a,:b,:tokens)"""),
                    dict(
                        id=child,
                        cid=job["course_id"],
                        did=run["document_id"],
                        rid=run["id"],
                        pid=p["id"],
                        ordinal=n,
                        body=body,
                        embedding=space["document_prefix"] + body,
                        a=a,
                        b=b,
                        tokens=max(1, int(len(body.split()) * 1.3)),
                    ),
                )
                conn.execute(
                    text("""INSERT INTO app.chunk_embeddings(course_id,run_id,child_id,embedding_space_id,embedding)
                 VALUES(:cid,:rid,:child,:space,CAST(:vector AS vector))"""),
                    dict(
                        cid=job["course_id"],
                        rid=run["id"],
                        child=child,
                        space=space["id"],
                        vector=vectors[n],
                    ),
                )
                n += 1
        if not n:
            raise ValueError("EMPTY_DOCUMENT")
        conn.execute(
            text(
                "UPDATE app.processing_runs SET state='ready',finished_at=now(),error_code=NULL WHERE id=:id"
            ),
            dict(id=run["id"]),
        )
        if run["embedding_space_id"] != config["active_space_id"]:
            try_cutover(conn, job["course_id"])
            lease.complete(conn)
            return
        conn.execute(
            text("UPDATE app.document_versions SET status='ready',error_code=NULL WHERE id=:id"),
            dict(id=run["version_id"]),
        )
        conn.execute(
            text("""UPDATE app.document_versions v SET superseded_at=now() FROM app.processing_runs r
          WHERE r.id=:old AND v.id=r.version_id AND v.id<>:new AND v.superseded_at IS NULL"""),
            dict(old=doc["active_run_id"], new=run["version_id"]),
        )
        conn.execute(
            text("UPDATE app.documents SET active_run_id=:rid WHERE id=:id"),
            dict(rid=run["id"], id=run["document_id"]),
        )
        conn.execute(
            text("UPDATE app.courses SET corpus_revision=corpus_revision+1 WHERE id=:id"),
            dict(id=job["course_id"]),
        )
        enqueue(conn, "extract_concepts", run["id"], job["course_id"], job["requested_by"])
        if config["pending_space_id"]:
            prepare_run(
                conn,
                job["course_id"],
                run["document_id"],
                run["version_id"],
                config["pending_space_id"],
                job["requested_by"],
                run["parameters"],
            )
            try_cutover(conn, job["course_id"])
        lease.complete(conn)
