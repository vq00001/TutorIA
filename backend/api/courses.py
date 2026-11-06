from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import text

from backend.api.auth import Input
from backend.api.common import ApiError, connection, course, idempotent, match
from backend.db.connection import one, rows
from backend.security.tokens import digest, token

router = APIRouter()


class CourseInput(Input):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=10000)
    objectives: str = Field(default="", max_length=10000)


class InviteInput(Input):
    maxUses: int = Field(default=1, ge=1, le=100)


class AcceptInvite(Input):
    token: str = Field(min_length=20, max_length=128)


def dto(c):
    return dict(
        id=c["id"],
        name=c["name"],
        description=c["description"],
        objectives=c["objectives"],
        subject=c["subject"],
        color=c["color"],
        icon=c["icon"],
        rowVersion=c["row_version"],
        myRole=c["my_role"],
        status=c["status"],
        corpusRevision=c["corpus_revision"],
        graphRevision=c["graph_revision"],
        progress=0,
        nextTopic="",
        lastActivity=None,
    )


@router.get("/courses")
def list_courses(
    limit: int = 50, cursor: UUID | None = None, conn=Depends(connection, scope="function")
):
    if not 1 <= limit <= 100:
        raise ApiError("INVALID_LIMIT", 422)
    result = rows(
        conn,
        """SELECT c.*,m.role my_role FROM app.courses c JOIN app.memberships m
       ON m.course_id=c.id AND m.user_id=auth.uid() AND m.status='active'
       WHERE (CAST(:cursor AS uuid) IS NULL OR c.id>CAST(:cursor AS uuid)) ORDER BY c.id LIMIT :limit""",
        cursor=str(cursor) if cursor else None,
        limit=limit + 1,
    )
    return {
        "data": [dto(c) for c in result[:limit]],
        "meta": {"nextCursor": str(result[limit - 1]["id"]) if len(result) > limit else None},
    }


@router.post("/courses", status_code=201)
def create_course(body: CourseInput, request: Request, conn=Depends(connection, scope="function")):
    def create():
        cid = uuid4()
        conn.execute(
            text("SELECT auth.create_course(:id,:name,:description,:objectives)"),
            dict(id=cid, **body.model_dump()),
        )
        import os

        space = os.environ.get("EMBEDDING_SPACE_ID")
        if space:
            conn.execute(
                text(
                    "INSERT INTO app.course_embedding_config(course_id,active_space_id) VALUES(:cid,:space)"
                ),
                dict(cid=cid, space=UUID(space)),
            )
        return {"id": str(cid)}

    resource = idempotent(conn, request, body.model_dump(), create)
    return {"data": dto(course(conn, UUID(resource["id"])))}


@router.get("/courses/{cid}")
def get_course(cid: UUID, conn=Depends(connection, scope="function")):
    return {"data": dto(course(conn, cid))}


@router.patch("/courses/{cid}")
def edit_course(
    cid: UUID, body: CourseInput, request: Request, conn=Depends(connection, scope="function")
):
    course(conn, cid, coordinator=True, write=True)
    row = one(conn, "SELECT * FROM app.courses WHERE id=:id FOR UPDATE", id=cid)
    match(request, row)
    conn.execute(
        text(
            "UPDATE app.courses SET name=:name,description=:description,objectives=:objectives WHERE id=:id"
        ),
        dict(id=cid, **body.model_dump()),
    )
    return get_course(cid, conn)


@router.get("/courses/{cid}/members")
def members(cid: UUID, conn=Depends(connection, scope="function")):
    course(conn, cid, coordinator=True)
    return {
        "data": rows(
            conn,
            "SELECT user_id AS id,role,status FROM app.memberships WHERE course_id=:id ORDER BY user_id",
            id=cid,
        )
    }


@router.post("/courses/{cid}/invites", status_code=201)
def invite(cid: UUID, body: InviteInput, conn=Depends(connection, scope="function")):
    course(conn, cid, coordinator=True, write=True)
    raw, hashed = token()
    conn.execute(
        text("""INSERT INTO app.course_invites(id,course_id,token_hash,created_by,expires_at,max_uses)
       VALUES(:id,:cid,:hash,auth.uid(),now()+interval '7 days',:uses)"""),
        dict(id=uuid4(), cid=cid, hash=hashed, uses=body.maxUses),
    )
    return {"data": {"token": raw}}


@router.post("/invites/accept")
def accept(body: AcceptInvite, request: Request, conn=Depends(connection, scope="function")):
    def perform():
        result = one(conn, "SELECT auth.accept_invite(:hash) AS id", hash=digest(body.token))
        if not result["id"]:
            raise ApiError("INVALID_INVITATION", 422)
        return {"id": str(result["id"])}

    resource = idempotent(conn, request, body.model_dump(), perform)
    return {"data": dto(course(conn, UUID(resource["id"])))}


class MemberInput(Input):
    role: str = Field(pattern="^(student|coordinator)$")
    status: str = Field(pattern="^(active|left|removed)$")


@router.patch("/courses/{cid}/members/{uid}")
def edit_member(
    cid: UUID,
    uid: UUID,
    body: MemberInput,
    request: Request,
    conn=Depends(connection, scope="function"),
):
    course(conn, cid, coordinator=True)
    conn.execute(text("SELECT id FROM app.courses WHERE id=:id FOR UPDATE"), dict(id=cid))
    member = one(
        conn,
        "SELECT * FROM app.memberships WHERE course_id=:cid AND user_id=:uid FOR UPDATE",
        cid=cid,
        uid=uid,
    )
    if not member:
        raise ApiError("NOT_FOUND", 404)
    match(request, member)
    conn.execute(
        text("""UPDATE app.memberships SET role=:role,status=:status,
       left_at=CASE WHEN :status='active' THEN NULL ELSE now() END WHERE course_id=:cid AND user_id=:uid"""),
        dict(cid=cid, uid=uid, **body.model_dump()),
    )
    conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    return {"data": {"id": uid, **body.model_dump(), "rowVersion": member["row_version"] + 1}}
