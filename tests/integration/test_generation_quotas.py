from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.api.exams import ExamInput, create_exam
from backend.db.connection import one, transaction
from tests.integration.test_database import seed
from tests.integration.test_grading_integrity import request
from tests.integration.test_materials import configure, prepare
from tests.integration.test_upload_quotas import another_course


def configure_generation(monkeypatch):
    monkeypatch.setenv("MODEL_LLM", "synthetic")
    monkeypatch.setenv("MODEL_LLM_REVISION", "fixture-v1")


def create(uid, cid, req=None):
    with transaction(uid) as conn:
        return create_exam(
            cid,
            ExamInput(questionCount=1),
            req or request("POST", f"/courses/{cid}/exams"),
            uid,
            conn,
        )


def maximum(admin, value):
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO auth.generation_limits VALUES('synthetic_limits',clock_timestamp(),:n)"
            ),
            dict(n=value),
        )


def test_cross_course_generation_race_reserves_only_the_remaining_slot(database, monkeypatch):
    admin, url = database
    uid, cid, space = prepare(admin, url, monkeypatch)
    configure_generation(monkeypatch)
    other, third = another_course(admin, uid, space), another_course(admin, uid, space)
    create(uid, cid)  # One of the two default slots is occupied.
    barrier = Barrier(2)

    def submit(course):
        barrier.wait(timeout=10)
        try:
            create(uid, course)
            return "accepted"
        except IntegrityError as error:
            return error.orig.diag.message_primary

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.submit(submit, other), executor.submit(submit, third)
        assert sorted([first.result(timeout=20), second.result(timeout=20)]) == [
            "GENERATION_QUOTA_EXCEEDED",
            "accepted",
        ]
    with admin.connect() as conn:
        assert (
            one(conn, "SELECT count(*) n FROM app.generation_runs WHERE status='queued'")["n"] == 2
        )
        assert one(conn, "SELECT count(*) n FROM app.exams")["n"] == 2
        assert one(conn, "SELECT count(*) n FROM app.jobs WHERE kind='generate_exam'")["n"] == 2


def test_versioned_limit_releases_terminal_slots_and_idempotent_replay_needs_no_slot(
    database, monkeypatch
):
    admin, url = database
    uid, cid, _ = prepare(admin, url, monkeypatch)
    configure_generation(monkeypatch)
    maximum(admin, 1)
    first = create(uid, cid)
    retry = request("POST", f"/courses/{cid}/exams")
    with pytest.raises(IntegrityError, match="GENERATION_QUOTA_EXCEEDED"):
        create(uid, cid, retry)
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE app.generation_runs SET status='failed' WHERE id=(SELECT generation_id FROM app.exams WHERE id=:id)"
            ),
            dict(id=UUID(first["data"]["id"])),
        )
        conn.execute(
            text("UPDATE app.exams SET status='failed' WHERE id=:id"),
            dict(id=UUID(first["data"]["id"])),
        )
        conn.execute(
            text("UPDATE app.jobs SET state='failed' WHERE target_id=:id"),
            dict(id=UUID(first["data"]["id"])),
        )
    accepted = create(uid, cid, retry)
    assert create(uid, cid, retry) == accepted
    with admin.connect() as conn:
        assert (
            one(conn, "SELECT count(*) n FROM app.generation_runs WHERE status='queued'")["n"] == 1
        )
        assert one(conn, "SELECT count(*) n FROM app.generation_runs")["n"] == 2


def test_active_generation_hidden_by_departure_still_uses_the_user_slot(database, monkeypatch):
    admin, url = database
    uid, cid, space = prepare(admin, url, monkeypatch)
    configure_generation(monkeypatch)
    maximum(admin, 1)
    other = another_course(admin, uid, space)
    create(uid, cid)
    coordinator, _ = seed(admin)
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app.memberships(course_id,user_id,role) VALUES(:cid,:uid,'coordinator')"
            ),
            dict(cid=cid, uid=coordinator),
        )
        conn.execute(
            text(
                "UPDATE app.memberships SET status='left',left_at=clock_timestamp() WHERE course_id=:cid AND user_id=:uid"
            ),
            dict(cid=cid, uid=uid),
        )
    with transaction(uid) as conn:
        assert one(conn, "SELECT count(*) n FROM app.generation_runs")["n"] == 0
    with pytest.raises(IntegrityError, match="GENERATION_QUOTA_EXCEEDED"):
        create(uid, other)


def test_expired_leases_retry_with_jitter_then_release_the_generation_slot(database, monkeypatch):
    admin, url = database
    uid, cid, _ = prepare(admin, url, monkeypatch)
    configure_generation(monkeypatch)
    maximum(admin, 1)
    first = create(uid, cid)
    eid = UUID(first["data"]["id"])
    configure(monkeypatch, url.replace("tutoria_api:", "tutoria_worker:"))
    previous_token = None
    deadline = None
    for attempt, lower, upper in ((1, 8, 12), (2, 24, 36), (3, 0, 0)):
        with transaction() as conn:
            job = one(conn, "SELECT * FROM auth.claim_job('quota-recovery-fixture')")
        assert job["attempts"] == attempt and job["lease_token"] != previous_token
        if deadline is None:
            deadline = job["deadline_at"]
        assert job["deadline_at"] == deadline
        previous_token = job["lease_token"]
        with admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE app.jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=:id"
                ),
                dict(id=job["id"]),
            )
        with transaction() as conn:
            conn.execute(text("SELECT auth.recover_jobs()"))
        with admin.begin() as conn:
            recovered = one(
                conn,
                "SELECT state,lease_token,extract(epoch FROM run_after-clock_timestamp()) delay FROM app.jobs WHERE id=:id",
                id=job["id"],
            )
            assert recovered["lease_token"] is None
            if attempt < 3:
                assert recovered["state"] == "queued"
                assert lower - 1 <= float(recovered["delay"]) <= upper
                conn.execute(
                    text(
                        "UPDATE app.jobs SET run_after=clock_timestamp()-interval '1 second' WHERE id=:id"
                    ),
                    dict(id=job["id"]),
                )
            else:
                assert recovered["state"] == "failed"
                assert (
                    one(conn, "SELECT status FROM app.exams WHERE id=:id", id=eid)["status"]
                    == "failed"
                )
                assert (
                    one(
                        conn,
                        "SELECT status FROM app.generation_runs WHERE id=(SELECT generation_id FROM app.exams WHERE id=:id)",
                        id=eid,
                    )["status"]
                    == "failed"
                )
    configure(monkeypatch, url)
    assert create(uid, cid)["data"]["status"] == "generating"


@pytest.mark.parametrize("reason", ["deadline", "cancelled"])
def test_unclaimable_job_is_reconciled_without_an_execution(database, monkeypatch, reason):
    admin, url = database
    uid, cid, _ = prepare(admin, url, monkeypatch)
    configure_generation(monkeypatch)
    first = create(uid, cid)
    eid = UUID(first["data"]["id"])
    with admin.begin() as conn:
        if reason == "deadline":
            conn.execute(
                text(
                    "UPDATE app.jobs SET deadline_at=clock_timestamp()-interval '1 second' WHERE target_id=:id"
                ),
                dict(id=eid),
            )
        else:
            conn.execute(
                text("UPDATE app.jobs SET state='cancelled' WHERE target_id=:id"), dict(id=eid)
            )
    configure(monkeypatch, url.replace("tutoria_api:", "tutoria_worker:"))
    with transaction() as conn:
        assert one(conn, "SELECT * FROM auth.claim_job('expired-fixture')") is None
        conn.execute(text("SELECT auth.recover_jobs()"))
        assert one(conn, "SELECT * FROM auth.claim_job('expired-fixture')") is None
    with admin.connect() as conn:
        job = one(
            conn, "SELECT state,attempts,error_code FROM app.jobs WHERE target_id=:id", id=eid
        )
        assert job["state"] == ("failed" if reason == "deadline" else "cancelled")
        assert job["attempts"] == 0
        if reason == "deadline":
            assert job["error_code"] == "DEADLINE_EXCEEDED"
        assert one(conn, "SELECT status FROM app.exams WHERE id=:id", id=eid)["status"] == (
            "failed" if reason == "deadline" else "invalidated"
        )
        assert (
            one(
                conn,
                "SELECT count(*) n FROM app.generation_runs WHERE status IN ('queued','running')",
            )["n"]
            == 0
        )
