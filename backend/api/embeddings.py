from uuid import UUID

from fastapi import APIRouter, Depends, Request

from backend.api.auth import Input
from backend.api.common import connection, course, current_user, idempotent
from backend.db.connection import one
from backend.services.embedding_migrations import cancel_migration, start_migration

router = APIRouter()


class SpaceInput(Input):
    embeddingSpaceId: UUID


@router.post("/courses/{cid}/embedding-migration", status_code=202)
def migrate(
    cid: UUID,
    body: SpaceInput,
    request: Request,
    uid=Depends(current_user),
    conn=Depends(connection, scope="function"),
):
    course(conn, cid, coordinator=True, write=True)
    result = idempotent(
        conn,
        request,
        body.model_dump(),
        lambda: start_migration(conn, cid, body.embeddingSpaceId, uid),
    )
    return {"data": result}


@router.get("/courses/{cid}/embedding-migration")
def status(cid: UUID, conn=Depends(connection, scope="function")):
    course(conn, cid)
    return {
        "data": one(
            conn,
            "SELECT active_space_id,pending_space_id FROM app.course_embedding_config WHERE course_id=:id",
            id=cid,
        )
    }


@router.delete("/courses/{cid}/embedding-migration")
def cancel(cid: UUID, request: Request, conn=Depends(connection, scope="function")):
    course(conn, cid, coordinator=True, write=True)
    return {"data": idempotent(conn, request, {}, lambda: cancel_migration(conn, cid))}
