from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import EmailStr
from sqlalchemy import text

from backend.api.auth import Input
from backend.api.common import ApiError, connection, course, idempotent
from backend.api.cursors import decode_cursor, encode_cursor
from backend.db.connection import one, rows

router = APIRouter()


class InviteMember(Input):
    email: EmailStr


class InvitationDecision(Input):
    accept: bool


@router.post("/courses/{cid}/invitations", status_code=202)
def invite_member(
    cid: UUID, body: InviteMember, request: Request, conn=Depends(connection, scope="function")
):
    course(conn, cid, coordinator=True, write=True)

    def perform():
        conn.execute(
            text("SELECT auth.invite_member(:cid,:email)"), dict(cid=cid, email=str(body.email))
        )
        return {"status": "accepted"}

    return {"data": idempotent(conn, request, body.model_dump(mode="json"), perform)}


@router.get("/invitations")
def invitations(conn=Depends(connection, scope="function")):
    return {"data": rows(conn, "SELECT * FROM auth.my_invitations()")}


@router.post("/invitations/{iid}/respond")
def respond(
    iid: UUID,
    body: InvitationDecision,
    request: Request,
    conn=Depends(connection, scope="function"),
):
    def perform():
        result = one(
            conn, "SELECT auth.respond_invitation(:id,:accept) AS id", id=iid, accept=body.accept
        )
        if not result["id"]:
            raise ApiError("INVALID_INVITATION", 422)
        return {"courseId": str(result["id"]), "status": "accepted" if body.accept else "declined"}

    return {"data": idempotent(conn, request, body.model_dump(), perform)}


def event_feed(conn, limit, cursor, notifications=False, cid=None):
    if not 1 <= limit <= 100:
        raise ApiError("INVALID_LIMIT", 422)
    at, identifier = decode_cursor(cursor)
    events = rows(
        conn,
        """SELECT id,course_id AS "courseId",action,entity_id AS "entityId",
      created_at AS "createdAt",read_at AS "readAt" FROM app.user_events
      WHERE (NOT :notices OR notification) AND (CAST(:cid AS uuid) IS NULL OR course_id=:cid)
      AND (CAST(:at AS timestamptz) IS NULL OR (created_at,id)>(CAST(:at AS timestamptz),CAST(:id AS uuid)))
      ORDER BY created_at,id LIMIT :limit""",
        notices=notifications,
        cid=cid,
        at=at,
        id=identifier,
        limit=limit + 1,
    )
    return {
        "data": events[:limit],
        "meta": {
            "nextCursor": encode_cursor(events[limit - 1]["createdAt"], events[limit - 1]["id"])
            if len(events) > limit
            else None
        },
    }


@router.get("/activity")
def activity(
    limit: int = 50, cursor: str | None = None, conn=Depends(connection, scope="function")
):
    return event_feed(conn, limit, cursor)


@router.get("/notifications")
def notifications(
    limit: int = 50, cursor: str | None = None, conn=Depends(connection, scope="function")
):
    return event_feed(conn, limit, cursor, True)


@router.get("/courses/{cid}/notifications")
def course_notifications(
    cid: UUID,
    limit: int = 50,
    cursor: str | None = None,
    conn=Depends(connection, scope="function"),
):
    course(conn, cid)
    return event_feed(conn, limit, cursor, True, cid)


@router.post("/notifications/{nid}/read")
def mark_read(nid: UUID, conn=Depends(connection, scope="function")):
    result = one(
        conn,
        '''UPDATE app.user_events SET read_at=coalesce(read_at,clock_timestamp())
      WHERE id=:id AND notification RETURNING id,read_at AS "readAt"''',
        id=nid,
    )
    if not result:
        raise ApiError("NOT_FOUND", 404)
    return {"data": result}
