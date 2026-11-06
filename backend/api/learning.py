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
from backend.services.learning import roadmap

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
      coalesce(m.evidence_count,0) AS evidence_count,'beta_v1' AS algorithm_version
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
        gid = uuid4()
        conn.execute(
            text(
                "INSERT INTO app.learning_goals(id,course_id,user_id,title,target_date) VALUES(:id,:cid,auth.uid(),:title,:date)"
            ),
            dict(id=gid, cid=cid, title=body.title, date=body.targetDate),
        )
        for concept in set(body.conceptIds):
            found = one(
                conn,
                "SELECT id FROM app.concepts WHERE id=:id AND course_id=:cid AND status='approved'",
                id=concept,
                cid=cid,
            )
            if not found:
                raise ApiError("INVALID_CONCEPT", 422)
            conn.execute(
                text(
                    "INSERT INTO app.goal_concepts(course_id,user_id,goal_id,concept_id) VALUES(:cid,auth.uid(),:gid,:concept)"
                ),
                dict(cid=cid, gid=gid, concept=concept),
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
    goal = one(conn, "SELECT * FROM app.learning_goals WHERE id=:id", id=gid)
    if not goal:
        raise ApiError("NOT_FOUND", 404)
    cid = goal["course_id"]
    course(conn, cid)

    def create():
        # Small MVP graph: read and compute under the same ordered locks, without AI.
        c = one(conn, "SELECT * FROM app.courses WHERE id=:id FOR UPDATE", id=cid)
        conn.execute(
            text(
                "INSERT INTO app.learning_profiles(course_id,user_id) VALUES(:cid,:uid) ON CONFLICT DO NOTHING"
            ),
            dict(cid=cid, uid=uid),
        )
        p = one(
            conn,
            "SELECT * FROM app.learning_profiles WHERE course_id=:cid AND user_id=:uid FOR UPDATE",
            cid=cid,
            uid=uid,
        )
        conn.execute(
            text("SELECT id FROM app.learning_goals WHERE id=:id FOR UPDATE"), dict(id=gid)
        )
        concepts = rows(
            conn,
            """SELECT c.*,coalesce(m.level,'unknown') level,
          EXISTS(SELECT FROM app.concept_sources cs JOIN app.parent_chunks pc ON pc.id=cs.parent_id
            JOIN app.documents d ON d.id=pc.document_id WHERE cs.concept_id=c.id AND d.active_run_id=cs.run_id AND d.status='active') has_source
          FROM app.concepts c LEFT JOIN app.concept_mastery m ON m.concept_id=c.id AND m.course_id=c.course_id AND m.user_id=auth.uid()
          WHERE c.course_id=:cid AND c.status='approved'""",
            cid=cid,
        )
        edges = rows(
            conn,
            "SELECT source_id,target_id FROM app.concept_edges WHERE course_id=:cid AND status='approved' AND relation='prerequisite'",
            cid=cid,
        )
        targets = rows(conn, "SELECT concept_id FROM app.goal_concepts WHERE goal_id=:id", id=gid)
        try:
            steps = roadmap(
                concepts,
                [(e["source_id"], e["target_id"]) for e in edges],
                [t["concept_id"] for t in targets],
            )
        except ValueError as exc:
            raise ApiError("INVALID_GRAPH") from exc
        conn.execute(
            text(
                "UPDATE app.roadmaps SET status='superseded' WHERE goal_id=:id AND status='active'"
            ),
            dict(id=gid),
        )
        version = one(
            conn,
            "SELECT coalesce(max(version_no),0)+1 n FROM app.roadmaps WHERE goal_id=:id",
            id=gid,
        )["n"]
        rid = uuid4()
        conn.execute(
            text("""INSERT INTO app.roadmaps(id,course_id,user_id,goal_id,version_no,status,corpus_revision,graph_revision,mastery_revision,parameters)
          VALUES(:id,:cid,:uid,:gid,:n,:status,:cr,:gr,:mr,'{}')"""),
            dict(
                id=rid,
                cid=cid,
                uid=uid,
                gid=gid,
                n=version,
                status="blocked" if any(s["state"] == "blocked" for s in steps) else "active",
                cr=c["corpus_revision"],
                gr=c["graph_revision"],
                mr=p["mastery_revision"],
            ),
        )
        for s in steps:
            conn.execute(
                text("""INSERT INTO app.roadmap_steps(id,course_id,user_id,roadmap_id,concept_id,position,reason,estimated_minutes,state)
              VALUES(:id,:cid,:uid,:rid,:concept,:pos,:reason,:minutes,:state)"""),
                dict(
                    id=uuid4(),
                    cid=cid,
                    uid=uid,
                    rid=rid,
                    concept=UUID(s["conceptId"]),
                    pos=s["position"],
                    reason=s["reason"],
                    minutes=s["estimatedMinutes"],
                    state=s["state"],
                ),
            )
        return {"id": str(rid)}

    return {"data": idempotent(conn, request, {}, create)}


@router.get("/goals/{gid}/roadmaps/current")
def current_roadmap(gid: UUID, conn=Depends(connection, scope="function")):
    goal = one(conn, "SELECT * FROM app.learning_goals WHERE id=:id", id=gid)
    if not goal:
        raise ApiError("NOT_FOUND", 404)
    c = course(conn, goal["course_id"])
    result = one(
        conn,
        "SELECT * FROM app.roadmaps WHERE goal_id=:id ORDER BY version_no DESC LIMIT 1",
        id=gid,
    )
    if not result:
        return {"data": None}
    p = one(
        conn,
        "SELECT mastery_revision FROM app.learning_profiles WHERE course_id=:cid AND user_id=auth.uid()",
        cid=goal["course_id"],
    )
    stale = (
        result["status"] == "stale"
        or result["corpus_revision"] != c["corpus_revision"]
        or result["graph_revision"] != c["graph_revision"]
        or result["mastery_revision"] != p["mastery_revision"]
    )
    return {
        "data": {
            **result,
            "stale": stale,
            "steps": rows(
                conn,
                "SELECT * FROM app.roadmap_steps WHERE roadmap_id=:id ORDER BY position",
                id=result["id"],
            ),
        }
    }


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
