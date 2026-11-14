from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import test_exams
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, ProgrammingError
from test_exams import _generation, _role

from db.documents import register_upload
from db.generations import generation_runs
from db.jobs import (
    claim_job,
    complete_job,
    enqueue_job,
    fail_job,
    get_job,
    jobs,
    recover_jobs,
    renew_job_lease,
)

job_context = test_exams.exam_context


def _ingest(conn, course, **options):
    document = register_upload(
        conn, course_id=course, filename="guia.md", data=uuid4().bytes, mime_type="text/markdown"
    )
    deadline = datetime.now(UTC) + timedelta(minutes=15)
    jid = enqueue_job(
        conn,
        course,
        kind="ingest_document",
        target_id=document,
        idempotency_key="ingest",
        deadline_at=deadline,
        **options,
    )
    return document, jid, deadline


def _change_job(conn, actor, jid, **values):
    conn.exec_driver_sql("RESET ROLE")
    conn.execute(update(jobs).where(jobs.c.id == jid).values(**values))
    _role(conn, actor, worker=True)


def test_enqueue_is_idempotent_and_restricts_visibility_and_worker_operations(
    postgres, job_context
):
    owner, peer, course, other = job_context
    scheduled = datetime.now(UTC) + timedelta(minutes=5)
    document, jid, deadline = _ingest(postgres, course, run_after=scheduled, payload={"parser": 1})
    args = {
        "kind": "ingest_document",
        "target_id": document,
        "idempotency_key": "ingest",
        "deadline_at": deadline,
        "payload": {"parser": 1},
        "run_after": scheduled,
    }
    assert enqueue_job(postgres, course, **args) == jid
    for changes in ({"payload": {}}, {"run_after": None}, {"max_attempts": 2}):
        with (
            pytest.raises(IntegrityError, match="JOB_IDEMPOTENCY_CONFLICT"),
            postgres.begin_nested(),
        ):
            enqueue_job(postgres, course, **(args | changes))
    with pytest.raises(IntegrityError, match="INVALID_JOB_TARGET"), postgres.begin_nested():
        enqueue_job(postgres, other, **args)
    with pytest.raises(ProgrammingError, match="permission denied"), postgres.begin_nested():
        claim_job(postgres, "api")
    with pytest.raises(ProgrammingError, match="permission denied"), postgres.begin_nested():
        postgres.execute(select(jobs.c.lease_token))
    assert get_job(postgres, course, jid)["state"] == "queued"

    _role(postgres, peer)
    assert get_job(postgres, course, jid) is None
    with pytest.raises(IntegrityError, match="INVALID_JOB_TARGET"), postgres.begin_nested():
        enqueue_job(postgres, course, **args)
    _role(postgres, owner, worker=True)
    assert claim_job(postgres, "worker") is None


def test_worker_renews_retries_and_completes_only_with_current_token(postgres, job_context):
    owner, _, course, _ = job_context
    _, jid, deadline = _ingest(postgres, course)
    _role(postgres, owner, worker=True)
    first = claim_job(postgres, "worker-1")
    assert (first["id"], first["attempts"], first["locked_by"]) == (jid, 1, "worker-1")
    token = first["lease_token"]
    assert first["lease_until"] < deadline
    assert not renew_job_lease(postgres, jid, uuid4())
    assert renew_job_lease(postgres, jid, token)
    assert fail_job(postgres, jid, token, error_code="TEMPORARY")
    assert not complete_job(postgres, jid, token)
    assert claim_job(postgres, "worker-1") is None

    _change_job(postgres, owner, jid, run_after=func.now() - timedelta(seconds=1))
    second = claim_job(postgres, "worker-2")
    assert second["attempts"] == 2 and second["lease_token"] != token
    assert not complete_job(postgres, jid, token)
    assert complete_job(postgres, jid, second["lease_token"])
    assert not complete_job(postgres, jid, second["lease_token"])
    _role(postgres, owner)
    result = get_job(postgres, course, jid)
    assert (result["state"], result["attempts"], result["error_code"]) == ("succeeded", 2, None)


def test_recovery_requeues_expired_lease_and_closes_exhausted_generation(postgres, job_context):
    owner, _, course, _ = job_context
    generation = _generation(postgres, course, kind="chat", record=False)
    jid = enqueue_job(
        postgres,
        course,
        kind="generate_chat",
        target_id=generation,
        idempotency_key="generation",
        deadline_at=datetime.now(UTC) + timedelta(minutes=15),
        max_attempts=2,
    )
    _role(postgres, owner, worker=True)
    first = claim_job(postgres, "worker-1")
    past = func.now() - timedelta(seconds=1)
    _change_job(postgres, owner, jid, lease_until=past)
    assert not renew_job_lease(postgres, jid, first["lease_token"])
    assert recover_jobs(postgres) == 1
    _role(postgres, owner)
    result = get_job(postgres, course, jid)
    assert (result["state"], result["error_code"]) == ("queued", "LEASE_EXPIRED")

    _change_job(postgres, owner, jid, run_after=past)
    second = claim_job(postgres, "worker-2")
    _change_job(postgres, owner, jid, lease_until=past)
    assert not complete_job(postgres, jid, second["lease_token"])
    assert recover_jobs(postgres) == 1
    assert recover_jobs(postgres) == 0
    _role(postgres, owner)
    result = get_job(postgres, course, jid)
    assert (result["state"], result["error_code"]) == ("failed", "RETRY_LIMIT_EXCEEDED")
    assert (
        postgres.scalar(select(generation_runs.c.status).where(generation_runs.c.id == generation))
        == "failed"
    )


def test_worker_skips_queued_deadlines_and_never_extends_past_deadline(postgres, job_context):
    owner, _, course, _ = job_context
    document, jid, _ = _ingest(postgres, course)
    short_deadline = datetime.now(UTC) + timedelta(seconds=30)
    _change_job(postgres, owner, jid, deadline_at=short_deadline)
    job = claim_job(postgres, "worker")
    assert job["lease_until"] == short_deadline
    assert renew_job_lease(postgres, jid, job["lease_token"])
    _change_job(postgres, owner, jid, deadline_at=func.now(), lease_until=func.now())
    assert not renew_job_lease(postgres, jid, job["lease_token"])
    assert recover_jobs(postgres) == 1
    _role(postgres, owner)
    assert get_job(postgres, course, jid)["error_code"] == "DEADLINE_EXCEEDED"

    queued = enqueue_job(
        postgres,
        course,
        kind="ingest_document",
        target_id=document,
        idempotency_key="expired-queued",
        deadline_at=datetime.now(UTC) + timedelta(minutes=2),
    )
    _change_job(postgres, owner, queued, deadline_at=func.now())
    assert claim_job(postgres, "worker") is None
    assert recover_jobs(postgres) == 1
    _role(postgres, owner)
    result = get_job(postgres, course, queued)
    assert (result["state"], result["attempts"], result["error_code"]) == (
        "failed",
        0,
        "DEADLINE_EXCEEDED",
    )
