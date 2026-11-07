from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.api.auth import Input
from backend.api.common import ApiError, connection, course, idempotent, match
from backend.api.cursors import decode_cursor, encode_cursor
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


class CourseDto(BaseModel):
    id: UUID
    name: str
    description: str
    objectives: str
    subject: str
    color: str
    icon: str
    rowVersion: int
    myRole: str
    status: str
    corpusRevision: int
    graphRevision: int
    progress: float
    nextTopic: str
    lastActivity: datetime | None


def dto(c, conn):
    metrics = one(
        conn,
        """SELECT
      (SELECT count(*) FROM app.concepts WHERE course_id=:cid AND status='approved') total,
      (SELECT count(*) FROM app.concept_mastery m JOIN app.concepts p ON p.id=m.concept_id AND p.course_id=m.course_id
         WHERE m.course_id=:cid AND m.user_id=auth.uid() AND m.level='mastered' AND p.status='approved') mastered,
      (SELECT max(at) FROM (
         SELECT created_at at FROM app.messages WHERE course_id=:cid AND user_id=auth.uid()
         UNION ALL SELECT coalesce(submitted_at,started_at) FROM app.exam_attempts WHERE course_id=:cid AND user_id=auth.uid()
         UNION ALL SELECT last_heartbeat_at FROM app.study_sessions WHERE course_id=:cid AND user_id=auth.uid()) a) last_activity""",
        cid=c["id"],
    )
    next_step = one(
        conn,
        """SELECT p.label FROM app.roadmaps r JOIN app.roadmap_steps s ON s.roadmap_id=r.id
      JOIN app.concepts p ON p.course_id=s.course_id AND p.id=s.concept_id
      JOIN app.learning_profiles lp ON lp.course_id=r.course_id AND lp.user_id=r.user_id
      JOIN app.learning_goals g ON g.id=r.goal_id
      WHERE r.course_id=:cid AND r.user_id=auth.uid() AND r.status='active' AND g.status='active'
      AND r.corpus_revision=:cr AND r.graph_revision=:gr AND r.mastery_revision=lp.mastery_revision
      AND s.state IN ('pending','in_progress') AND p.status='approved'
      AND NOT EXISTS(SELECT FROM app.concept_edges e JOIN app.roadmap_steps pre
        ON pre.roadmap_id=r.id AND pre.concept_id=e.source_id
        WHERE e.course_id=r.course_id AND e.target_id=s.concept_id AND e.relation='prerequisite' AND e.status='approved' AND pre.state<>'completed')
      ORDER BY r.created_at DESC,r.id,s.position LIMIT 1""",
        cid=c["id"],
        cr=c["corpus_revision"],
        gr=c["graph_revision"],
    )
    return CourseDto(
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
        progress=100 * metrics["mastered"] / metrics["total"] if metrics["total"] else 0,
        nextTopic=next_step["label"] if next_step else "",
        lastActivity=metrics["last_activity"],
    ).model_dump()


@router.get("/courses")
def list_courses(
    limit: int = 50, cursor: str | None = None, conn=Depends(connection, scope="function")
):
    if not 1 <= limit <= 100:
        raise ApiError("INVALID_LIMIT", 422)
    after_at, after_id = decode_cursor(cursor)
    result = rows(
        conn,
        """SELECT c.*,m.role my_role FROM app.courses c JOIN app.memberships m
       ON m.course_id=c.id AND m.user_id=auth.uid() AND m.status='active'
       WHERE (CAST(:after_at AS timestamptz) IS NULL OR (c.created_at,c.id)>(CAST(:after_at AS timestamptz),CAST(:after_id AS uuid))) ORDER BY c.created_at,c.id LIMIT :limit""",
        after_at=after_at,
        after_id=after_id,
        limit=limit + 1,
    )
    return {
        "data": [dto(c, conn) for c in result[:limit]],
        "meta": {
            "nextCursor": encode_cursor(result[limit - 1]["created_at"], result[limit - 1]["id"])
            if len(result) > limit
            else None
        },
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
    return {"data": dto(course(conn, UUID(resource["id"])), conn)}


@router.get("/courses/{cid}")
def get_course(cid: UUID, conn=Depends(connection, scope="function")):
    return {"data": dto(course(conn, cid), conn)}


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
    course(conn, cid)
    return {
        "data": rows(
            conn,
            "SELECT * FROM auth.course_members(:id)",
            id=cid,
        )
    }


@router.post("/courses/{cid}/leave")
def leave(cid: UUID, conn=Depends(connection, scope="function")):
    course(conn, cid)
    conn.execute(text("SELECT auth.leave_course(:id)"), dict(id=cid))
    conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    return {"data": {"status": "left"}}


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
    return {"data": dto(course(conn, UUID(resource["id"])), conn)}


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
