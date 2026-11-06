from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from backend.api.learning import (
    GoalInput,
    RoadmapSelection,
    course_roadmap,
    create_goal,
    create_roadmap,
    current_roadmap,
)
from backend.db.connection import one, rows, transaction
from backend.services.learning import ROADMAP_PARAMETERS
from backend.workers.exams import grade_attempt
from backend.workers.main import Lease
from backend.workers.roadmaps import refresh_roadmap
from tests.integration.test_grading_integrity import (
    answer_and_submit,
    claim,
    published_exam,
    request,
)
from tests.integration.test_materials import configure, prepare


def concept(admin, cid, name):
    identifier = uuid4()
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app.concepts(id,course_id,canonical_key,label,status) VALUES(:id,:cid,:key,:name,'approved')"
            ),
            dict(id=identifier, cid=cid, key=str(identifier), name=name),
        )
    return identifier


def default_route(cid, uid):
    with transaction(uid) as conn:
        return course_roadmap(
            cid, RoadmapSelection(), request("POST", f"/courses/{cid}/roadmaps"), uid, conn
        )["data"]


def test_default_goal_is_reused_and_only_it_gains_new_approved_concepts(database, monkeypatch):
    admin, url = database
    uid, cid, _ = prepare(admin, url, monkeypatch)
    first = concept(admin, cid, "First synthetic concept")
    initial = default_route(cid, uid)
    assert default_route(cid, uid) == initial
    with transaction(uid) as conn:
        custom = create_goal(
            cid,
            GoalInput(title="Personal selection", conceptIds=[first]),
            request("POST", f"/courses/{cid}/goals"),
            conn,
        )["data"]
    second = concept(admin, cid, "Second synthetic concept")
    changed = default_route(cid, uid)
    assert changed["goalId"] == initial["goalId"] and changed["id"] != initial["id"]
    with transaction(uid) as conn:
        route = current_roadmap(UUID(changed["goalId"]), conn)["data"]
        assert route["version_no"] == 2 and route["status"] == "blocked" and not route["stale"]
        assert {s["concept_id"] for s in route["steps"]} == {first, second}
        assert route["parameters"] == ROADMAP_PARAMETERS
        selected = rows(
            conn,
            "SELECT concept_id FROM app.goal_concepts WHERE goal_id=:id",
            id=UUID(custom["id"]),
        )
        assert [r["concept_id"] for r in selected] == [first]
    with admin.connect() as conn:
        assert one(conn, "SELECT count(*) n FROM app.learning_goals WHERE is_default")["n"] == 1
        assert (
            one(
                conn,
                "SELECT count(*) n FROM app.jobs WHERE kind='refresh_roadmap' AND target_id=:id AND state='queued'",
                id=UUID(initial["goalId"]),
            )["n"]
            == 1
        )
        assert (
            one(conn, "SELECT status FROM app.roadmaps WHERE id=:id", id=UUID(initial["id"]))[
                "status"
            ]
            == "superseded"
        )


def test_grading_enqueues_fenced_refresh_without_changing_published_snapshot(database, monkeypatch):
    admin, url, uid, cid, eid, _ = published_exam(database, monkeypatch, count=1)
    initial = default_route(cid, uid)
    job = claim(monkeypatch, url, "refresh_roadmap")
    refresh_roadmap(job, Lease(job))  # Same snapshot is a no-op, not a second version.
    aid = answer_and_submit(url, monkeypatch, uid, cid, eid)
    grading = claim(monkeypatch, url, "grade_attempt")
    grade_attempt(grading, Lease(grading))
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        previous = current_roadmap(UUID(initial["goalId"]), conn)["data"]
        assert previous["stale"] and previous["version_no"] == 1
        assert previous["mastery_revision"] == 0
    job = claim(monkeypatch, url, "refresh_roadmap")
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE app.jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=:id"
            ),
            dict(id=job["id"]),
        )
    with pytest.raises(ValueError, match="LEASE_LOST"):
        refresh_roadmap(job, Lease(job))
    with admin.begin() as conn:
        assert one(conn, "SELECT count(*) n FROM app.roadmaps")["n"] == 1
        conn.execute(text("SELECT auth.recover_jobs()"))
        conn.execute(
            text(
                "UPDATE app.jobs SET run_after=clock_timestamp()-interval '1 second' WHERE id=:id"
            ),
            dict(id=job["id"]),
        )
    replacement = claim(monkeypatch, url, "refresh_roadmap")
    assert replacement["lease_token"] != job["lease_token"]
    refresh_roadmap(replacement, Lease(replacement))
    configure(monkeypatch, url)
    with transaction(uid) as conn:
        current = current_roadmap(UUID(initial["goalId"]), conn)["data"]
        assert current["version_no"] == 2 and not current["stale"]
        assert current["mastery_revision"] == 1
        assert (
            one(conn, "SELECT status FROM app.exam_attempts WHERE id=:id", id=aid)["status"]
            == "graded"
        )


def test_retired_prerequisite_is_retained_as_blocked_instead_of_omitted(database, monkeypatch):
    admin, url, uid, cid, _, first = published_exam(database, monkeypatch, count=1)
    second = concept(admin, cid, "Target synthetic concept")
    edge = uuid4()
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app.concept_edges(id,course_id,source_id,target_id,relation,status) VALUES(:id,:cid,:a,:b,'prerequisite','approved')"
            ),
            dict(id=edge, cid=cid, a=first, b=second),
        )
    with transaction(uid) as conn:
        goal = create_goal(
            cid, GoalInput(conceptIds=[second]), request("POST", f"/courses/{cid}/goals"), conn
        )["data"]
        create_roadmap(
            UUID(goal["id"]), request("POST", f"/goals/{goal['id']}/roadmaps"), uid, conn
        )
    with admin.begin() as conn:
        conn.execute(text("UPDATE app.concepts SET status='retired' WHERE id=:id"), dict(id=first))
        conn.execute(
            text("UPDATE app.concept_edges SET status='retired' WHERE id=:id"), dict(id=edge)
        )
    with transaction(uid) as conn:
        create_roadmap(
            UUID(goal["id"]), request("POST", f"/goals/{goal['id']}/roadmaps"), uid, conn
        )
        route = current_roadmap(UUID(goal["id"]), conn)["data"]
        assert route["status"] == "blocked"
        assert [s["concept_id"] for s in route["steps"]] == [first, second]
        assert route["steps"][0]["reason"] == "RETIRED_CONCEPT"


def test_catalogs_and_published_roadmap_contents_are_immutable(database, monkeypatch):
    admin, url = database
    uid, cid, _ = prepare(admin, url, monkeypatch)
    concept(admin, cid, "Synthetic concept")
    result = default_route(cid, uid)
    with transaction() as conn:
        assert rows(conn, "SELECT * FROM app.mastery_algorithms") == []
    with transaction(uid) as conn:
        assert one(conn, "SELECT version FROM app.mastery_algorithms")["version"] == "beta_v1"
    with pytest.raises(ProgrammingError):
        with transaction(uid) as conn:
            conn.execute(text("UPDATE app.roadmap_algorithms SET parameters='{}'"))
    with pytest.raises(IntegrityError, match="IMMUTABLE_CONFIGURATION"):
        with admin.begin() as conn:
            conn.execute(text("UPDATE app.roadmap_algorithms SET parameters='{}'"))
    with pytest.raises(IntegrityError, match="IMMUTABLE_ROADMAP"):
        with transaction(uid) as conn:
            conn.execute(
                text("UPDATE app.roadmaps SET parameters='{}' WHERE id=:id"),
                dict(id=UUID(result["id"])),
            )
    with pytest.raises(IntegrityError, match="IMMUTABLE_ROADMAP"):
        with transaction(uid) as conn:
            conn.execute(
                text("UPDATE app.roadmap_steps SET position=position+1 WHERE roadmap_id=:id"),
                dict(id=UUID(result["id"])),
            )
    with pytest.raises(IntegrityError):
        with transaction(uid) as conn:
            conn.execute(
                text("""INSERT INTO app.roadmaps(id,course_id,user_id,goal_id,version_no,status,
                corpus_revision,graph_revision,mastery_revision,parameters,algorithm_version)
                SELECT :new,course_id,user_id,goal_id,version_no+1,'superseded',corpus_revision,
                graph_revision,mastery_revision,parameters,'unknown_version' FROM app.roadmaps WHERE id=:id"""),
                dict(new=uuid4(), id=UUID(result["id"])),
            )
