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
def conversations(cid: UUID, conn=Depends(connection, scope="function")):
    course(conn, cid)
    return {
        "data": rows(
            conn,
            "SELECT id,title,created_at FROM app.conversations WHERE course_id=:cid AND status='active' ORDER BY created_at,id LIMIT 100",
            cid=cid,
        )
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
    return {
        "data": [
            dict(
                id=r["id"],
                role=r["role"],
                parts=r["parts"],
                status=r["status"],
                createdAt=r["created_at"],
                sequenceNo=r["sequence_no"],
                awaitingResponse=r["status"] in ("pending", "streaming"),
                citations=citations_for_message(conn, cid, r["id"]),
            )
            for r in result
        ]
    }


def citations_for_message(conn, cid, mid):
    result = rows(
        conn,
        """SELECT c.id,c.label,c.status,p.document_id,r.version_id
      FROM app.citations c LEFT JOIN app.parent_chunks p ON p.id=c.parent_id AND p.course_id=c.course_id
      LEFT JOIN app.processing_runs r ON r.id=p.run_id AND r.course_id=p.course_id
      WHERE c.message_id=:id AND c.course_id=:cid ORDER BY c.ordinal""",
        id=mid,
        cid=cid,
    )
    return [
        dict(
            id=c["id"],
            label=c["label"],
            status=c["status"],
            contentUrl=f"/api/v1/courses/{cid}/materials/{c['document_id']}/versions/{c['version_id']}/content"
            if c["document_id"]
            else None,
        )
        for c in result
    ]


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

    def create():
        c = one(conn, "SELECT * FROM app.courses WHERE id=:id FOR UPDATE", id=cid)
        conn.execute(text("SELECT id FROM app.conversations WHERE id=:id FOR UPDATE"), dict(id=tid))
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
           VALUES(:id,:cid,auth.uid(),'chat','ollama',:model,:revision,'chat_v1','{}',:cr,:gr,:hash)"""),
            dict(
                id=gid,
                cid=cid,
                model=os.environ["MODEL_LLM"],
                revision=os.environ["MODEL_LLM_REVISION"],
                cr=c["corpus_revision"],
                gr=c["graph_revision"],
                hash=sha256(body.content.encode()).digest(),
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
                parts=json_value([{"type": "markdown", "content": body.content}]),
            ),
        )
        conn.execute(
            text("""INSERT INTO app.messages(id,course_id,user_id,conversation_id,sequence_no,role,status,in_reply_to,generation_id)
          VALUES(:id,:cid,auth.uid(),:tid,:n,'assistant','pending',:mid,:gid)"""),
            dict(id=reply, cid=cid, tid=tid, n=n + 1, mid=mid, gid=gid),
        )
        enqueue(conn, "generate_chat", gid, cid, uid, 55)
        return dict(userMessageId=str(mid), assistantMessageId=str(reply), generationId=str(gid))

    return {"data": idempotent(conn, request, body.model_dump(), create)}


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
