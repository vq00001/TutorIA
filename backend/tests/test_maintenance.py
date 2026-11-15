from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import ProgrammingError

from db.audit import audit_events, record_audit_event
from db.connection import require_restore_ready
from db.courses import courses, create_course, memberships
from db.documents import delete_document, documents, download_document, file_contents
from db.generations import generation_runs
from db.jobs import jobs
from db.maintenance import scheduler_tick
from db.processing import fail_processing, processing_runs
from db.purge import (
    acknowledge_receipt,
    deletion_requests,
    pending_receipts,
    purge_pending,
    request_purge,
)
from db.restore import DeletionReceipt, begin_restore, receipt_manifest_digest, replay_restore
from db.retention import run_retention
from db.sessions import issue_session, sessions
from db.users import users
from db.vector_search import search_course_chunks
from .test_courses import _user
from . import test_document_processing
from .test_document_processing import _publish, _start
from .test_exams import _generation, _role
from .test_jobs import _ingest

processing_context = test_document_processing.processing_context


def _request(conn, scope, subject):
    return request_purge(conn, request_id=uuid4(), scope=scope, subject_id=subject)


def test_document_withdrawal_and_scheduler_wait_for_external_receipt(postgres):
    owner = _user(postgres)
    _role(postgres, owner)
    course = create_course(postgres, name="Retirada")
    document, job, _ = _ingest(postgres, course)
    with pytest.raises(ProgrammingError), postgres.begin_nested():
        _request(postgres, "document", document)

    postgres.exec_driver_sql("RESET ROLE")
    rid = _request(postgres, "document", document)
    assert _request(postgres, "document", document) == rid
    assert [row.id for row in pending_receipts(postgres) if row.subject_id == document] == [rid]
    assert postgres.scalar(select(jobs.c.id).where(jobs.c.id == job)) is None

    _role(postgres, owner)
    assert download_document(postgres, course, document) is None
    assert not delete_document(postgres, course, document)
    postgres.exec_driver_sql("RESET ROLE")
    assert postgres.scalar(select(file_contents.c.document_id).where(
        file_contents.c.document_id == document
    )) == document
    assert purge_pending(postgres) == 0

    assert acknowledge_receipt(postgres, rid)
    with pytest.raises(RuntimeError, match="retry"), postgres.begin_nested():
        assert purge_pending(postgres) == 1
        raise RuntimeError("retry")
    assert postgres.scalar(select(documents.c.id).where(documents.c.id == document)) == document
    assert scheduler_tick(postgres).purged_requests == 1
    assert postgres.scalar(select(documents.c.id).where(documents.c.id == document)) is None
    assert postgres.scalar(select(deletion_requests.c.completed_at).where(
        deletion_requests.c.id == rid
    )) is not None


def test_account_purge_preserves_coordinator_and_course_purge_removes_membership(postgres):
    owner, peer = _user(postgres), _user(postgres)
    _role(postgres, owner)
    course = create_course(postgres, name="Clase compartida")
    issue_session(postgres, owner)
    document, _, _ = _ingest(postgres, course)
    postgres.exec_driver_sql("RESET ROLE")
    with pytest.raises(ValueError, match="LAST_COORDINATOR"):
        _request(postgres, "account", owner)
    assert postgres.scalar(select(users.c.status).where(users.c.id == owner)) == "active"

    postgres.execute(insert(memberships).values(course_id=course, user_id=peer, role="coordinator"))
    rid = _request(postgres, "account", owner)
    assert postgres.scalar(select(sessions.c.user_id).where(sessions.c.user_id == owner)) is None
    assert acknowledge_receipt(postgres, rid)
    assert purge_pending(postgres) == 1
    assert postgres.scalar(select(users.c.id).where(users.c.id == owner)) is None
    assert postgres.scalar(select(documents.c.id).where(documents.c.id == document)) is None
    assert postgres.scalar(select(memberships.c.user_id).where(
        memberships.c.course_id == course, memberships.c.role == "coordinator"
    )) == peer

    assert acknowledge_receipt(postgres, _request(postgres, "course", course))
    assert purge_pending(postgres) == 1
    assert postgres.scalar(select(courses.c.id).where(courses.c.id == course)) is None
    assert postgres.scalar(select(memberships.c.user_id).where(
        memberships.c.course_id == course
    )) is None
    postgres.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")


def test_retention_runs_once_and_protects_the_published_run(postgres, processing_context):
    course, document, space, owner, _ = processing_context
    failed = _start(postgres, processing_context)
    assert fail_processing(postgres, failed, "TEST_FAILURE")
    historical = _start(postgres, processing_context, chunker="v2")
    _publish(postgres, historical)
    _role(postgres, owner)
    sources = search_course_chunks(postgres, course, space, [3, 4])
    assert sources
    generation = _generation(postgres, course, kind="chat", sources=sources)

    _role(postgres, owner, worker=True)
    active = _start(postgres, processing_context, chunker="v3")
    _publish(postgres, active)
    _role(postgres, owner)
    issue_session(postgres, owner)
    event = record_audit_event(
        postgres, action="retention.test", entity_type="session", request_id=uuid4(),
        result="success",
    )
    postgres.exec_driver_sql("RESET ROLE")

    now = postgres.scalar(select(func.clock_timestamp()))
    at = now.replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=100)
    assert run_retention(postgres, at=at)
    assert not run_retention(postgres, at=at + timedelta(seconds=1))
    assert postgres.scalar(select(processing_runs.c.id).where(
        processing_runs.c.id == failed
    )) is None
    assert postgres.scalar(select(processing_runs.c.id).where(
        processing_runs.c.id == active
    )) == active
    assert postgres.scalar(select(sessions.c.user_id).where(sessions.c.user_id == owner)) is None
    assert postgres.scalar(select(audit_events.c.id).where(audit_events.c.id == event)) is None
    assert postgres.scalar(select(processing_runs.c.id).where(
        processing_runs.c.id == historical
    )) == historical

    _request(postgres, "document", document)
    assert postgres.scalar(select(generation_runs.c.status).where(
        generation_runs.c.id == generation
    )) == "invalidated"


def test_job_retention_uses_terminal_timestamp_not_creation(postgres):
    owner = _user(postgres)
    _role(postgres, owner)
    course = create_course(postgres, name="Cola")
    _, job, _ = _ingest(postgres, course)
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(update(jobs).where(jobs.c.id == job).values(
        state="failed", created_at=func.clock_timestamp() - timedelta(days=40)
    ))
    finished = postgres.scalar(select(jobs.c.finished_at).where(jobs.c.id == job))
    assert finished is not None
    assert run_retention(postgres, at=finished + timedelta(days=6))
    assert postgres.scalar(select(jobs.c.id).where(jobs.c.id == job)) == job
    assert run_retention(postgres, at=finished + timedelta(days=7))
    assert postgres.scalar(select(jobs.c.id).where(jobs.c.id == job)) is None


def test_restore_holds_gate_on_conflict_then_replays_withdrawal(postgres):
    owner = _user(postgres)
    _role(postgres, owner)
    course = create_course(postgres, name="Copia restaurada")
    document, _, _ = _ingest(postgres, course)
    postgres.exec_driver_sql("RESET ROLE")

    begin_restore(postgres)
    _role(postgres, owner)
    with pytest.raises(RuntimeError, match="RESTORE_NOT_READY"):
        require_restore_ready(postgres)
    postgres.exec_driver_sql("RESET ROLE")
    with pytest.raises(RuntimeError, match="RESTORE_NOT_READY"):
        scheduler_tick(postgres)

    now = postgres.scalar(select(func.clock_timestamp()))
    receipt = DeletionReceipt(uuid4(), "document", document, now)
    conflict = DeletionReceipt(receipt.id, "document", uuid4(), now)
    with pytest.raises(ValueError, match="DELETION_ID_CONFLICT"):
        replay_restore(postgres, [receipt, conflict], expected_digest=receipt_manifest_digest([receipt]))
    assert postgres.scalar(select(documents.c.id).where(documents.c.id == document)) == document
    assert postgres.scalar(select(func.app.restore_is_ready())) is False

    digest = receipt_manifest_digest([receipt])
    with pytest.raises(ValueError, match="RESTORE_MANIFEST_MISMATCH"):
        replay_restore(postgres, [], expected_digest=digest)
    assert postgres.scalar(select(func.app.restore_is_ready())) is False

    replay_restore(postgres, [receipt, receipt], expected_digest=digest)
    assert postgres.scalar(select(documents.c.id).where(documents.c.id == document)) is None
    _role(postgres, owner, worker=True)
    require_restore_ready(postgres)

def test_restore_replays_receipt_from_a_direct_delete(postgres):
    owner = _user(postgres)
    _role(postgres, owner)
    course = create_course(postgres, name="Respaldo previo al borrado")
    document, _, _ = _ingest(postgres, course)
    before_deletion = postgres.begin_nested()
    assert delete_document(postgres, course, document)
    postgres.exec_driver_sql("RESET ROLE")
    (exported,) = [row for row in pending_receipts(postgres) if row.subject_id == document]
    receipt = DeletionReceipt(exported.id, exported.scope, exported.subject_id, exported.requested_at)
    digest = receipt_manifest_digest([receipt])
    before_deletion.rollback()

    postgres.exec_driver_sql("RESET ROLE")
    assert postgres.scalar(select(documents.c.id).where(documents.c.id == document)) == document
    begin_restore(postgres)
    replay_restore(postgres, [receipt], expected_digest=digest)
    assert postgres.scalar(select(documents.c.id).where(documents.c.id == document)) is None
    _role(postgres, owner)
    require_restore_ready(postgres)
