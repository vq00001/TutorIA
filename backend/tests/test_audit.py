from uuid import uuid4

import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError, ProgrammingError

from db.audit import audit_events, record_audit_event, record_worker_audit_event
from db.courses import create_course, memberships
from db.users import users
from test_courses import _user
from test_exams import _role


def _event(request_id, course_id=None):
    return {
        "action": "document.delete",
        "entity_type": "document",
        "entity_id": uuid4(),
        "request_id": request_id,
        "course_id": course_id,
        "result": "success",
    }


def _visible_events(conn, request_id):
    return conn.execute(
        select(audit_events).where(audit_events.c.request_id == request_id)
    ).mappings().all()


def test_audit_correlates_user_and_worker_events_and_isolates_readers(postgres):
    coordinator, student, outsider = (_user(postgres) for _ in range(3))
    _role(postgres, coordinator)
    course = create_course(postgres, name="Auditoría")
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(insert(memberships).values(course_id=course, user_id=student, role="student"))

    request = uuid4()
    event = _event(request, course)
    _role(postgres, student)
    user_event = record_audit_event(postgres, **event)
    assert [row.id for row in _visible_events(postgres, request)] == [user_event]

    _role(postgres, coordinator, worker=True)
    worker_event = record_worker_audit_event(
        postgres,
        action="job.failed",
        entity_type="job",
        entity_id=uuid4(),
        course_id=course,
        request_id=request,
        result="error",
        reason_code="dependency_failure",
    )
    with pytest.raises(ProgrammingError), postgres.begin_nested():
        _visible_events(postgres, request)

    _role(postgres, coordinator)
    events = {row.id: row for row in _visible_events(postgres, request)}
    assert events[user_event].actor_id == student
    assert events[user_event].source == "user"
    assert events[user_event].entity_id == event["entity_id"]
    assert events[user_event].metadata == {}
    assert events[worker_event].actor_id is None
    assert events[worker_event].source == "worker"
    assert events[worker_event].result == "error"
    assert events[worker_event].metadata == {"reason_code": "dependency_failure"}
    assert events[worker_event].created_at.tzinfo is not None

    _role(postgres, student)
    assert [row.id for row in _visible_events(postgres, request)] == [user_event]
    _role(postgres, outsider)
    assert _visible_events(postgres, request) == []
    with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
        record_audit_event(postgres, **event)


def test_audit_rejects_direct_mutations_and_cross_role_functions(postgres):
    actor, pending = _user(postgres), _user(postgres, status="pending")
    event = _event(uuid4())

    for identity in (None, pending):
        _role(postgres, identity)
        with pytest.raises(ProgrammingError, match="NOT_AUTHORIZED"), postgres.begin_nested():
            record_audit_event(postgres, **event)

    for worker in (False, True):
        _role(postgres, actor, worker=worker)
        for statement in (
            insert(audit_events).values(**event, actor_id=actor, source="user"),
            update(audit_events).where(audit_events.c.id == uuid4()).values(result="denied"),
            delete(audit_events).where(audit_events.c.id == uuid4()),
        ):
            with pytest.raises(ProgrammingError), postgres.begin_nested():
                postgres.execute(statement)

        wrong_recorder = record_audit_event if worker else record_worker_audit_event
        with pytest.raises(ProgrammingError), postgres.begin_nested():
            wrong_recorder(postgres, **event)

    _role(postgres, actor)
    for invalid in (
        {"result": "pending"},
        {"reason_code": "uncontrolled text"},
        {"action": "not an action code"},
    ):
        with pytest.raises(IntegrityError), postgres.begin_nested():
            record_audit_event(postgres, **{**event, **invalid})


def test_audit_success_rolls_back_and_failure_can_be_written_afterward(postgres):
    actor = _user(postgres)
    _role(postgres, actor)
    request = uuid4()
    event = _event(request)
    with pytest.raises(RuntimeError, match="operation failed"), postgres.begin_nested():
        record_audit_event(postgres, **event)
        assert len(_visible_events(postgres, request)) == 1
        raise RuntimeError("operation failed")

    assert _visible_events(postgres, request) == []
    failure = record_audit_event(
        postgres, **{**event, "result": "denied"}, reason_code="not_authorized"
    )
    (row,) = _visible_events(postgres, request)
    assert (row.id, row.result) == (failure, "denied")

    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(delete(users).where(users.c.id == actor))
    (retained,) = _visible_events(postgres, request)
    assert (retained.actor_id, retained.source) == (None, "user")
