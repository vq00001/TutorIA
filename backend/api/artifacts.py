from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import text

from backend.api.auth import Input
from backend.api.chat import queue_response
from backend.api.common import ApiError, connection, course, current_user, idempotent
from backend.api.cursors import decode_cursor, encode_cursor
from backend.db.connection import rows

router = APIRouter()


class ArtifactInput(Input):
    kind: str = Field(pattern="^(summary|explanation|flashcards)$")
    topic: str = Field(min_length=1, max_length=200)


def generation_documents(conn, cid, gid, parents):
    return rows(
        conn,
        '''SELECT DISTINCT d.id,d.title,v.original_name AS name,
      '/api/v1/courses/'||d.course_id||'/materials/'||d.id||'/versions/'||v.id||'/content' AS "contentUrl"
      FROM app.generation_sources gs JOIN app.parent_chunks p ON p.id=gs.parent_id AND p.course_id=gs.course_id
      JOIN app.processing_runs r ON r.id=p.run_id JOIN app.document_versions v ON v.id=r.version_id
      JOIN app.documents d ON d.id=v.document_id WHERE gs.generation_id=:id AND gs.course_id=:cid
      AND d.status='active' AND p.id::text=ANY(CAST(:parents AS text[]))
      ORDER BY d.id,v.original_name,d.title,"contentUrl"''',
        id=gid,
        cid=cid,
        parents=parents,
    )


@router.get("/courses/{cid}/artifacts")
def artifacts(
    cid: UUID,
    conn=Depends(connection, scope="function"),
    limit: int = 50,
    cursor: str | None = None,
):
    course(conn, cid)
    if not 1 <= limit <= 100:
        raise ApiError("INVALID_LIMIT", 422)
    at, identifier = decode_cursor(cursor)
    result = rows(
        conn,
        """SELECT id,kind,title,content,created_at AS "createdAt",generation_id FROM app.generated_artifacts
      WHERE course_id=:cid AND status='active'
      AND (CAST(:at AS timestamptz) IS NULL OR (created_at,id)>(CAST(:at AS timestamptz),CAST(:id AS uuid)))
      ORDER BY created_at,id LIMIT :limit""",
        cid=cid,
        at=at,
        id=identifier,
        limit=limit + 1,
    )
    return {
        "data": [
            {
                **r,
                "usedDocuments": generation_documents(
                    conn, cid, r["generation_id"], r["content"].get("citedParentIds", [])
                ),
            }
            for r in result[:limit]
        ],
        "meta": {
            "nextCursor": encode_cursor(result[limit - 1]["createdAt"], result[limit - 1]["id"])
            if len(result) > limit
            else None
        },
    }


@router.post("/courses/{cid}/artifacts", status_code=202)
def create_artifact(
    cid: UUID,
    body: ArtifactInput,
    request: Request,
    uid=Depends(current_user),
    conn=Depends(connection, scope="function"),
):
    course(conn, cid, write=True)

    def create():
        tid = uuid4()
        title = f"{body.kind}: {body.topic}"
        conn.execute(
            text("""INSERT INTO app.conversations(id,course_id,user_id,title)
          VALUES(:id,:cid,auth.uid(),:title)"""),
            dict(id=tid, cid=cid, title=title),
        )
        names = {
            "summary": "un resumen de estudio",
            "explanation": "una explicación didáctica",
            "flashcards": "tarjetas de estudio con pregunta y respuesta",
        }
        prompt = f"Prepara {names[body.kind]} sobre {body.topic}. Usa Markdown y exclusivamente los documentos del curso; si no hay evidencia suficiente, indícalo."
        queued = queue_response(
            conn, cid, tid, prompt, uid, {"artifactKind": body.kind, "artifactTitle": title}
        )
        return {"generationId": queued["generationId"], "conversationId": str(tid)}

    return {"data": idempotent(conn, request, body.model_dump(), create)}
