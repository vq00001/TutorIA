from datetime import date, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import text

from backend.api.auth import Input
from backend.api.common import (
    ApiError,
    connection,
    course,
    current_user,
    idempotent,
    now,
)
from backend.db.connection import one, rows
from backend.services.roadmaps import build_roadmap, ensure_default_goal, read_roadmap

router = APIRouter()


class GoalInput(Input):
    title: str = Field(default="Dominar el curso", min_length=1, max_length=255)
    conceptIds: list[UUID] = Field(min_length=1, max_length=5000)
    targetDate: date | None = None


@router.get("/courses/{cid}/mastery/me")
def mastery_me(cid: UUID, conn=Depends(connection, scope="function")):
    course(conn, cid)
    return {
        "data": rows(
            conn,
            """SELECT c.id AS concept_id,coalesce(m.level,'unknown') AS level,
      CASE WHEN m.evidence_count>0 THEN m.alpha/(m.alpha+m.beta) ELSE NULL END AS mastery_score,
      coalesce(m.evidence_count,0) AS evidence_count,coalesce(m.algorithm_version,'beta_v1') AS algorithm_version
      FROM app.concepts c LEFT JOIN app.concept_mastery m ON m.course_id=c.course_id AND m.concept_id=c.id AND m.user_id=auth.uid()
      WHERE c.course_id=:cid AND c.status='approved' ORDER BY c.id""",
            cid=cid,
        )
    }


@router.get("/courses/{cid}/goals")
def goals(cid: UUID, conn=Depends(connection, scope="function")):
    course(conn, cid)
    return {
        "data": rows(
            conn,
            "SELECT * FROM app.learning_goals WHERE course_id=:cid ORDER BY created_at,id",
            cid=cid,
        )
    }


@router.post("/courses/{cid}/goals", status_code=201)
def create_goal(
    cid: UUID, body: GoalInput, request: Request, conn=Depends(connection, scope="function")
):
    course(conn, cid, write=True)

    def create():
        conn.execute(text("SELECT auth.lock_course(:id)"), dict(id=cid))
        selected = list(set(body.conceptIds))
        approved = rows(
            conn,
            "SELECT id FROM app.concepts WHERE course_id=:cid AND status='approved' AND id=ANY(CAST(:ids AS uuid[]))",
            cid=cid,
            ids=selected,
        )
        if len(approved) != len(selected):
            raise ApiError("INVALID_CONCEPT", 422)
        gid = uuid4()
        conn.execute(
            text(
                "INSERT INTO app.learning_goals(id,course_id,user_id,title,target_date) VALUES(:id,:cid,auth.uid(),:title,:date)"
            ),
            dict(id=gid, cid=cid, title=body.title, date=body.targetDate),
        )
        conn.execute(
            text(
                "INSERT INTO app.goal_concepts(course_id,user_id,goal_id,concept_id) SELECT :cid,auth.uid(),:gid,unnest(CAST(:ids AS uuid[]))"
            ),
            dict(cid=cid, gid=gid, ids=selected),
        )
        return {"id": str(gid)}

    return {"data": idempotent(conn, request, body.model_dump(), create)}


@router.post("/goals/{gid}/roadmaps", status_code=201)
def create_roadmap(
    gid: UUID,
    request: Request,
    uid=Depends(current_user),
    conn=Depends(connection, scope="function"),
):
    return {"data": idempotent(conn, request, {}, lambda: build_roadmap(conn, gid, uid))}


@router.get("/goals/{gid}/roadmaps/current")
def current_roadmap(gid: UUID, conn=Depends(connection, scope="function")):
    return {"data": read_roadmap(conn, gid)}


class RoadmapSelection(Input):
    goalId: UUID | None = None


@router.post("/courses/{cid}/roadmaps", status_code=201)
def course_roadmap(
    cid: UUID,
    body: RoadmapSelection,
    request: Request,
    uid=Depends(current_user),
    conn=Depends(connection, scope="function"),
):
    course(conn, cid, write=True)

    def create():
        gid = body.goalId or ensure_default_goal(conn, cid, uid)
        goal = one(conn, "SELECT course_id FROM app.learning_goals WHERE id=:id", id=gid)
        if not goal or goal["course_id"] != cid:
            raise ApiError("NOT_FOUND", 404)
        return build_roadmap(conn, gid, uid)

    return {"data": idempotent(conn, request, body.model_dump(), create)}


@router.get("/courses/{cid}/roadmaps/current")
def current_course_roadmap(cid: UUID, conn=Depends(connection, scope="function")):
    course(conn, cid)
    goal = one(
        conn,
        "SELECT id FROM app.learning_goals WHERE course_id=:cid AND is_default AND status='active'",
        cid=cid,
    )
    return {"data": read_roadmap(conn, goal["id"]) if goal else None}


class StudyInput(Input):
    courseId: UUID


@router.post("/study-sessions", status_code=201)
def start_session(body: StudyInput, request: Request, conn=Depends(connection, scope="function")):
    course(conn, body.courseId)

    def create():
        conn.execute(text("SELECT id FROM app.users WHERE id=auth.uid() FOR UPDATE"))
        session = one(
            conn,
            "SELECT * FROM app.study_sessions WHERE user_id=auth.uid() AND ended_at IS NULL FOR UPDATE",
        )
        if session and (now() - session["last_heartbeat_at"]).total_seconds() <= 120:
            return {"id": str(session["id"])}
        if session:
            conn.execute(
                text("UPDATE app.study_sessions SET ended_at=last_heartbeat_at WHERE id=:id"),
                dict(id=session["id"]),
            )
        sid = uuid4()
        conn.execute(
            text(
                "INSERT INTO app.study_sessions(id,course_id,user_id,started_at,last_heartbeat_at) VALUES(:id,:cid,auth.uid(),clock_timestamp(),clock_timestamp())"
            ),
            dict(id=sid, cid=body.courseId),
        )
        return {"id": str(sid)}

    return {"data": idempotent(conn, request, body.model_dump(), create)}


@router.post("/study-sessions/{sid}/heartbeat")
def heartbeat(sid: UUID, request: Request, conn=Depends(connection, scope="function")):
    s = one(conn, "SELECT * FROM app.study_sessions WHERE id=:id", id=sid)
    if not s:
        raise ApiError("NOT_FOUND", 404)
    course(conn, s["course_id"])

    def beat():
        s = one(conn, "SELECT * FROM app.study_sessions WHERE id=:id FOR UPDATE", id=sid)
        current = now()
        delta = (current - s["last_heartbeat_at"]).total_seconds()
        if s["ended_at"] or delta > 120:
            conn.execute(
                text("UPDATE app.study_sessions SET ended_at=last_heartbeat_at WHERE id=:id"),
                dict(id=sid),
            )
            return {"status": "closed", "activeSeconds": s["active_seconds"]}
        seconds = min(int(delta), 30) if delta >= 25 else 0
        if seconds:
            n = one(
                conn,
                "SELECT coalesce(max(sequence_no),0)+1 n FROM app.study_intervals WHERE session_id=:id",
                id=sid,
            )["n"]
            conn.execute(
                text("""INSERT INTO app.study_intervals(id,course_id,user_id,session_id,sequence_no,start_at,end_at)
              VALUES(:id,:cid,auth.uid(),:sid,:n,:start,:end)"""),
                dict(
                    id=uuid4(),
                    cid=s["course_id"],
                    sid=sid,
                    n=n,
                    start=current - timedelta(seconds=seconds),
                    end=current,
                ),
            )
            conn.execute(
                text(
                    "UPDATE app.study_sessions SET active_seconds=active_seconds+:seconds,last_heartbeat_at=:at WHERE id=:id"
                ),
                dict(id=sid, seconds=seconds, at=current),
            )
        return {"status": "active", "activeSeconds": s["active_seconds"] + seconds}

    return {"data": idempotent(conn, request, {}, beat)}
