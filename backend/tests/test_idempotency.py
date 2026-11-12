import pytest
import test_exams
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, ProgrammingError
from test_exams import _role

from db.idempotency import idempotency_requests, run_idempotent
from db.learning_catalog import create_concept, list_concepts

idempotency_context = test_exams.exam_context


def test_request_replays_json_and_rejects_conflicting_payload(postgres, idempotency_context):
    owner, peer, _, _ = idempotency_context
    calls = []

    def operation(conn):
        calls.append(conn)
        return {"saved": len(calls)}

    args = {"scope": "operation", "key": "retry-1", "operation": operation}
    assert run_idempotent(postgres, payload={"a": 1, "b": 2}, **args) == {"saved": 1}
    assert run_idempotent(postgres, payload={"b": 2, "a": 1}, **args) == {"saved": 1}
    with pytest.raises(IntegrityError, match="IDEMPOTENCY_CONFLICT"):
        run_idempotent(postgres, payload={"a": 2}, **args)
    _role(postgres, peer)
    assert run_idempotent(postgres, payload={"a": 2}, **args) == {"saved": 2}
    _role(postgres, owner)
    assert run_idempotent(postgres, payload={"a": 1, "b": 2}, **args) == {"saved": 1}
    with pytest.raises(ProgrammingError, match="permission denied"), postgres.begin_nested():
        postgres.execute(select(idempotency_requests))


def test_request_rolls_back_failed_work_and_reuses_expired_key(postgres, idempotency_context):
    owner, _, course, _ = idempotency_context

    def failing(conn):
        create_concept(conn, course, label="Temporal")
        raise RuntimeError("failed")

    args = {"scope": "save", "key": "retry"}
    with pytest.raises(RuntimeError, match="failed"):
        run_idempotent(postgres, payload={"old": True}, operation=failing, **args)
    assert list_concepts(postgres, course) == []
    assert (
        run_idempotent(postgres, payload={"new": True}, operation=lambda conn: None, **args) is None
    )
    assert run_idempotent(postgres, payload={"new": True}, operation=failing, **args) is None

    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(
        update(idempotency_requests)
        .where(idempotency_requests.c.user_id == owner)
        .values(expires_at=func.now())
    )
    _role(postgres, owner)
    assert run_idempotent(postgres, payload={}, operation=lambda conn: 42, **args) == 42
