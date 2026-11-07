import asyncio
import os
from hashlib import sha256
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import text

from backend.api.auth import Input
from backend.api.common import (
    ApiError,
    connection,
    course,
    current_user,
    enqueue,
    idempotent,
    json_value,
)
from backend.api.cursors import decode_cursor, encode_cursor
from backend.db.connection import one, rows, transaction

router = APIRouter()


class ConversationInput(Input):
    title: str = Field(default="Nueva conversación", min_length=1, max_length=255)


class MessageInput(Input):
    content: str = Field(min_length=1, max_length=8000)


def conversation(conn, cid, tid):
    course(conn, cid)
    result = one(
        conn,
        "SELECT * FROM app.conversations WHERE id=:id AND course_id=:cid AND user_id=auth.uid() AND status='active'",
        id=tid,
        cid=cid,
    )
    if not result:
        raise ApiError("NOT_FOUND", 404)
    return result


@router.get("/courses/{cid}/conversations")
def conversations(
    cid: UUID,
    limit: int = 50,
    cursor: str | None = None,
    conn=Depends(connection, scope="function"),
):
    course(conn, cid)
    if not 1 <= limit <= 100:
        raise ApiError("INVALID_LIMIT", 422)
    at, identifier = decode_cursor(cursor)
    result = rows(
        conn,
        """SELECT id,title,created_at FROM app.conversations WHERE course_id=:cid AND status='active'
      AND (CAST(:at AS timestamptz) IS NULL OR (created_at,id)>(CAST(:at AS timestamptz),CAST(:id AS uuid)))
      ORDER BY created_at,id LIMIT :limit""",
        cid=cid,
        at=at,
        id=identifier,
        limit=limit + 1,
    )
    return {
        "data": result[:limit],
        "meta": {
            "nextCursor": encode_cursor(result[limit - 1]["created_at"], result[limit - 1]["id"])
            if len(result) > limit
            else None
        },
    }


@router.post("/courses/{cid}/conversations", status_code=201)
def create_conversation(
    cid: UUID, body: ConversationInput, request: Request, conn=Depends(connection, scope="function")
):
    course(conn, cid, write=True)

    def create():
        tid = uuid4()
        conn.execute(
            text(
                "INSERT INTO app.conversations(id,course_id,user_id,title) VALUES(:id,:cid,auth.uid(),:title)"
            ),
            dict(id=tid, cid=cid, title=body.title),
        )
        return {"id": str(tid)}

    result = idempotent(conn, request, body.model_dump(), create)
    conversation(conn, cid, UUID(result["id"]))
    return {"data": result}


@router.get("/courses/{cid}/conversations/{tid}/messages")
def messages(cid: UUID, tid: UUID, after: int = 0, conn=Depends(connection, scope="function")):
    conversation(conn, cid, tid)
    result = rows(
        conn,
        """SELECT id,role,parts,status,created_at,sequence_no FROM app.messages
      WHERE conversation_id=:tid AND sequence_no>:after ORDER BY sequence_no LIMIT 100""",
        tid=tid,
        after=after,
    )
    data = []
    for r in result:
        citations = citations_for_message(conn, cid, r["id"])
        documents = [c for c in citations if c["status"] == "available" and c["contentUrl"]]
        data.append(
            dict(
                id=r["id"],
                role=r["role"],
                parts=r["parts"],
                status=r["status"],
                createdAt=r["created_at"],
                sequenceNo=r["sequence_no"],
                awaitingResponse=r["status"] in ("pending", "streaming"),
                citations=citations,
                usesDocuments=bool(documents),
                usedDocuments=documents,
            )
        )
    return {"data": data}


def citations_for_message(conn, cid, mid):
    result = rows(
        conn,
        """SELECT c.id,c.label,c.status,p.document_id,r.version_id,v.original_name
      FROM app.citations c LEFT JOIN app.parent_chunks p ON p.id=c.parent_id AND p.course_id=c.course_id
      LEFT JOIN app.processing_runs r ON r.id=p.run_id AND r.course_id=p.course_id
      LEFT JOIN app.document_versions v ON v.id=r.version_id AND v.course_id=r.course_id
      WHERE c.message_id=:id AND c.course_id=:cid ORDER BY c.ordinal""",
        id=mid,
        cid=cid,
    )
    return [
        dict(
            id=c["id"],
            label=c["label"],
            status=c["status"],
            documentId=c["document_id"],
            documentName=c["original_name"],
            contentUrl=f"/api/v1/courses/{cid}/materials/{c['document_id']}/versions/{c['version_id']}/content"
            if c["document_id"]
            else None,
        )
        for c in result
    ]


@router.delete("/courses/{cid}/conversations/{tid}")
def delete_conversation(cid: UUID, tid: UUID, conn=Depends(connection, scope="function")):
    course(conn, cid)
    conn.execute(text("SELECT id FROM app.courses WHERE id=:id FOR UPDATE"), dict(id=cid))
    conversation(conn, cid, tid)
    conn.execute(text("SELECT id FROM app.conversations WHERE id=:id FOR UPDATE"), dict(id=tid))
    conn.execute(
        text("""UPDATE app.jobs SET state='cancelled',lease_token=NULL,lease_until=NULL
      WHERE kind='generate_chat' AND target_id IN (SELECT generation_id FROM app.messages WHERE conversation_id=:id)
      AND state IN ('queued','running')"""),
        dict(id=tid),
    )
    conn.execute(
        text("""UPDATE app.generation_runs SET status='cancelled',finished_at=now()
      WHERE id IN (SELECT generation_id FROM app.messages WHERE conversation_id=:id) AND status IN ('queued','running')"""),
        dict(id=tid),
    )
    conn.execute(
        text(
            "DELETE FROM app.citations WHERE message_id IN (SELECT id FROM app.messages WHERE conversation_id=:id)"
        ),
        dict(id=tid),
    )
    conn.execute(
        text(
            "UPDATE app.messages SET in_reply_to=NULL,generation_id=NULL WHERE conversation_id=:id"
        ),
        dict(id=tid),
    )
    conn.execute(text("DELETE FROM app.messages WHERE conversation_id=:id"), dict(id=tid))
    conn.execute(text("DELETE FROM app.conversations WHERE id=:id"), dict(id=tid))
    return {"data": {"deleted": True}}


@router.post("/courses/{cid}/conversations/{tid}/messages", status_code=202)
def send(
    cid: UUID,
    tid: UUID,
    body: MessageInput,
    request: Request,
    uid=Depends(current_user),
    conn=Depends(connection, scope="function"),
):
    course(conn, cid, write=True)
    conversation(conn, cid, tid)

    return {
        "data": idempotent(
            conn,
            request,
            body.model_dump(),
            lambda: queue_response(conn, cid, tid, body.content, uid),
        )
    }


def queue_response(conn, cid, tid, content, uid, parameters=None):
    c = one(conn, "SELECT * FROM app.courses WHERE id=:id FOR UPDATE", id=cid)
    if not c:
        raise ApiError("NOT_FOUND", 404)
    if c["status"] != "active":
        raise ApiError("COURSE_ARCHIVED")
    locked = one(
        conn,
        "SELECT id FROM app.conversations WHERE id=:id AND course_id=:cid AND user_id=auth.uid() AND status='active' FOR UPDATE",
        id=tid,
        cid=cid,
    )
    if not locked:
        raise ApiError("NOT_FOUND", 404)
    if one(
        conn,
        "SELECT id FROM app.messages WHERE conversation_id=:id AND role='assistant' AND status IN ('pending','streaming')",
        id=tid,
    ):
        raise ApiError("GENERATION_IN_PROGRESS")
    n = one(
        conn,
        "SELECT coalesce(max(sequence_no),0)+1 AS n FROM app.messages WHERE conversation_id=:id",
        id=tid,
    )["n"]
    gid, mid, reply = uuid4(), uuid4(), uuid4()
    conn.execute(
        text("""INSERT INTO app.generation_runs(id,course_id,user_id,kind,provider,model_name,model_revision,
       prompt_version,parameters,corpus_revision,graph_revision,request_hash)
       VALUES(:id,:cid,auth.uid(),'chat','ollama',:model,:revision,'chat_v1',CAST(:parameters AS jsonb),:cr,:gr,:hash)"""),
        dict(
            id=gid,
            cid=cid,
            model=os.environ["MODEL_LLM"],
            revision=os.environ["MODEL_LLM_REVISION"],
            cr=c["corpus_revision"],
            gr=c["graph_revision"],
            hash=sha256(content.encode()).digest(),
            parameters=json_value(parameters or {}),
        ),
    )
    conn.execute(
        text("""INSERT INTO app.messages(id,course_id,user_id,conversation_id,sequence_no,role,status,parts)
      VALUES(:id,:cid,auth.uid(),:tid,:n,'user','completed',CAST(:parts AS jsonb))"""),
        dict(
            id=mid,
            cid=cid,
            tid=tid,
            n=n,
            parts=json_value([{"type": "markdown", "content": content}]),
        ),
    )
    conn.execute(
        text("""INSERT INTO app.messages(id,course_id,user_id,conversation_id,sequence_no,role,status,in_reply_to,generation_id)
      VALUES(:id,:cid,auth.uid(),:tid,:n,'assistant','pending',:mid,:gid)"""),
        dict(id=reply, cid=cid, tid=tid, n=n + 1, mid=mid, gid=gid),
    )
    enqueue(conn, "generate_chat", gid, cid, uid, 55)
    return dict(userMessageId=str(mid), assistantMessageId=str(reply), generationId=str(gid))


@router.get("/generations/{gid}")
def generation(gid: UUID, conn=Depends(connection, scope="function")):
    g = one(
        conn, "SELECT id,course_id,status,error_code FROM app.generation_runs WHERE id=:id", id=gid
    )
    if not g:
        raise ApiError("NOT_FOUND", 404)
    course(conn, g["course_id"])
    msg = one(conn, "SELECT id,parts,status FROM app.messages WHERE generation_id=:id", id=gid)
    return {"data": {**g, "message": msg}}


@router.get("/generations/{gid}/events")
def events(gid: UUID, uid=Depends(current_user), conn=Depends(connection, scope="function")):
    generation(gid, conn)

    async def stream():
        previous = None
        for _ in range(120):
            with transaction(uid) as current:
                data = generation(gid, current)["data"]
            encoded = json_value(data)
            if encoded != previous:
                yield "id: " + str(gid) + "\nevent: status\ndata: " + encoded + "\n\n"
                previous = encoded
            if data["status"] in ("completed", "failed", "cancelled", "invalidated"):
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
    )
