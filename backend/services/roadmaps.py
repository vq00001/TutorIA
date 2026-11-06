"""Coherent, versioned study plans shared by HTTP and the refresh worker."""

from uuid import UUID, uuid4

from sqlalchemy import text

from backend.api.common import ApiError, json_value
from backend.db.connection import one, rows
from backend.services.learning import ROADMAP_PARAMETERS, ROADMAP_VERSION, roadmap


def ensure_default_goal(conn, cid, uid):
    current = one(conn, "SELECT id,status FROM app.courses WHERE id=:id FOR UPDATE", id=cid)
    if not current:
        raise ApiError("NOT_FOUND", 404)
    if current["status"] != "active":
        raise ApiError("COURSE_ARCHIVED")
    goal = one(
        conn,
        """SELECT id FROM app.learning_goals WHERE course_id=:cid AND user_id=:uid
        AND is_default AND status='active' FOR UPDATE""",
        cid=cid,
        uid=uid,
    )
    if not goal:
        if not one(
            conn,
            "SELECT id FROM app.concepts WHERE course_id=:cid AND status='approved' LIMIT 1",
            cid=cid,
        ):
            raise ApiError("NO_APPROVED_CONCEPTS")
        gid = uuid4()
        conn.execute(
            text("""INSERT INTO app.learning_goals(id,course_id,user_id,title,is_default)
            VALUES(:gid,:cid,:uid,'Dominar el curso',true)"""),
            dict(gid=gid, cid=cid, uid=uid),
        )
    else:
        gid = goal["id"]
    conn.execute(
        text("""INSERT INTO app.goal_concepts(course_id,user_id,goal_id,concept_id)
        SELECT :cid,:uid,:gid,id FROM app.concepts WHERE course_id=:cid AND status='approved'
        ON CONFLICT DO NOTHING"""),
        dict(cid=cid, uid=uid, gid=gid),
    )
    return gid


def build_roadmap(conn, gid, uid, validate=None):
    goal = one(
        conn, "SELECT * FROM app.learning_goals WHERE id=:id AND user_id=:uid", id=gid, uid=uid
    )
    if not goal:
        raise ApiError("NOT_FOUND", 404)
    cid = goal["course_id"]
    # The worker must not require API-only membership table privileges.
    c = one(conn, "SELECT * FROM app.courses WHERE id=:id FOR UPDATE", id=cid)
    if not c:
        raise ApiError("NOT_FOUND", 404)
    if c["status"] != "active":
        raise ApiError("COURSE_ARCHIVED")
    conn.execute(
        text(
            "INSERT INTO app.learning_profiles(course_id,user_id) VALUES(:cid,:uid) ON CONFLICT DO NOTHING"
        ),
        dict(cid=cid, uid=uid),
    )
    profile = one(
        conn,
        "SELECT * FROM app.learning_profiles WHERE course_id=:cid AND user_id=:uid FOR UPDATE",
        cid=cid,
        uid=uid,
    )
    goal = one(conn, "SELECT * FROM app.learning_goals WHERE id=:id FOR UPDATE", id=gid)
    if goal["status"] != "active":
        raise ApiError("GOAL_INACTIVE")
    catalog = one(
        conn,
        "SELECT parameters FROM app.roadmap_algorithms WHERE version=:version",
        version=ROADMAP_VERSION,
    )
    if not catalog or catalog["parameters"] != ROADMAP_PARAMETERS:
        raise ApiError("UNSUPPORTED_ALGORITHM", 503)
    latest = one(
        conn,
        "SELECT * FROM app.roadmaps WHERE goal_id=:id ORDER BY version_no DESC LIMIT 1",
        id=gid,
    )
    snapshot = (
        c["corpus_revision"],
        c["graph_revision"],
        profile["mastery_revision"],
        goal["row_version"],
    )
    if validate:
        validate(conn)
    if (
        latest
        and latest["status"] in ("active", "blocked")
        and (
            latest["corpus_revision"],
            latest["graph_revision"],
            latest["mastery_revision"],
            latest["goal_revision"],
        )
        == snapshot
        and latest["algorithm_version"] == ROADMAP_VERSION
        and latest["parameters"] == ROADMAP_PARAMETERS
    ):
        return {"id": str(latest["id"]), "goalId": str(gid)}
    targets = rows(conn, "SELECT concept_id FROM app.goal_concepts WHERE goal_id=:id", id=gid)
    if not targets:
        raise ApiError("NO_APPROVED_CONCEPTS")
    concepts = rows(
        conn,
        """SELECT c.*,coalesce(m.level,'unknown') level,
        EXISTS(SELECT 1 FROM app.concept_sources cs JOIN app.parent_chunks pc ON pc.id=cs.parent_id
          JOIN app.documents d ON d.id=pc.document_id WHERE cs.concept_id=c.id
          AND d.active_run_id=cs.run_id AND d.status='active') has_source
        FROM app.concepts c LEFT JOIN app.concept_mastery m
          ON m.concept_id=c.id AND m.course_id=c.course_id AND m.user_id=:uid
        WHERE c.course_id=:cid AND c.status IN ('approved','retired')""",
        cid=cid,
        uid=uid,
    )
    edges = rows(
        conn,
        """SELECT e.source_id,e.target_id FROM app.concept_edges e
        JOIN app.concepts a ON a.id=e.source_id JOIN app.concepts b ON b.id=e.target_id
        WHERE e.course_id=:cid AND e.relation='prerequisite' AND (e.status='approved'
        OR (e.status='retired' AND (a.status='retired' OR b.status='retired')))""",
        cid=cid,
    )
    try:
        steps = roadmap(
            concepts,
            [(e["source_id"], e["target_id"]) for e in edges],
            [t["concept_id"] for t in targets],
        )
    except ValueError as exc:
        raise ApiError("INVALID_GRAPH") from exc
    if validate:
        validate(conn)
    conn.execute(
        text(
            "UPDATE app.roadmaps SET status='superseded' WHERE goal_id=:id AND status IN ('active','stale','blocked')"
        ),
        dict(id=gid),
    )
    rid = uuid4()
    conn.execute(
        text("""INSERT INTO app.roadmaps(id,course_id,user_id,goal_id,version_no,status,
        corpus_revision,graph_revision,mastery_revision,goal_revision,algorithm_version,parameters)
        VALUES(:id,:cid,:uid,:gid,:version,:status,:cr,:gr,:mr,:goal_revision,:algorithm,CAST(:parameters AS jsonb))"""),
        dict(
            id=rid,
            cid=cid,
            uid=uid,
            gid=gid,
            version=latest["version_no"] + 1 if latest else 1,
            status="blocked" if any(s["state"] == "blocked" for s in steps) else "active",
            cr=snapshot[0],
            gr=snapshot[1],
            mr=snapshot[2],
            goal_revision=snapshot[3],
            algorithm=ROADMAP_VERSION,
            parameters=json_value(ROADMAP_PARAMETERS),
        ),
    )
    for step in steps:
        conn.execute(
            text("""INSERT INTO app.roadmap_steps(id,course_id,user_id,roadmap_id,concept_id,position,reason,estimated_minutes,state)
            VALUES(:id,:cid,:uid,:rid,:concept,:pos,:reason,:minutes,:state)"""),
            dict(
                id=uuid4(),
                cid=cid,
                uid=uid,
                rid=rid,
                concept=UUID(step["conceptId"]),
                pos=step["position"],
                reason=step["reason"],
                minutes=step["estimatedMinutes"],
                state=step["state"],
            ),
        )
    return {"id": str(rid), "goalId": str(gid)}


def read_roadmap(conn, gid):
    goal = one(conn, "SELECT * FROM app.learning_goals WHERE id=:id", id=gid)
    if not goal:
        raise ApiError("NOT_FOUND", 404)
    c = one(conn, "SELECT * FROM app.courses WHERE id=:id", id=goal["course_id"])
    if not c:
        raise ApiError("NOT_FOUND", 404)
    route = one(
        conn,
        "SELECT * FROM app.roadmaps WHERE goal_id=:id ORDER BY version_no DESC LIMIT 1",
        id=gid,
    )
    if not route:
        return None
    profile = one(
        conn,
        "SELECT mastery_revision FROM app.learning_profiles WHERE course_id=:cid AND user_id=auth.uid()",
        cid=goal["course_id"],
    )
    stale = (
        route["status"] == "stale"
        or goal["status"] != "active"
        or (
            route["corpus_revision"],
            route["graph_revision"],
            route["mastery_revision"],
            route["goal_revision"],
        )
        != (
            c["corpus_revision"],
            c["graph_revision"],
            profile["mastery_revision"] if profile else 0,
            goal["row_version"],
        )
    )
    return {
        **route,
        "stale": stale,
        "steps": rows(
            conn,
            """SELECT s.*,c.label concept_label
        FROM app.roadmap_steps s JOIN app.concepts c ON c.id=s.concept_id AND c.course_id=s.course_id
        WHERE s.roadmap_id=:id ORDER BY s.position""",
            id=route["id"],
        ),
    }
