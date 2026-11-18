from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from db.audit import audit_events, record_worker_audit_event
from db.courses import create_course
from db.documents import DuplicateDocumentWarning, documents
from db.generations import generation_runs
from db.jobs import claim_job, complete_job, get_job
from db.submissions import SubmissionConflict, submit_generation, submit_upload

from .test_courses import _user
from .test_exams import _role


def _deadline():
    return datetime.now(timezone.utc) + timedelta(minutes=15)


def _upload(conn, course, key, **changes):
    args = {
        "course_id": course,
        "key": key,
        "deadline_at": _deadline(),
        "filename": "guide.md",
        "data": b"contenido",
        "mime_type": "text/markdown",
    }
    return submit_upload(conn, **(args | changes))


def _generation(conn, course, kind, key, **changes):
    args = {
        "key": key,
        "deadline_at": _deadline(),
        "kind": kind,
        "provider": "ollama",
        "model_name": "local",
        "model_revision": "test",
        "prompt_version": "v1",
        "payload": {"topic": "álgebra"},
    }
    return submit_generation(conn, course, **(args | changes))


def test_upload_and_ingestion_replay_and_roll_back_together(postgres):
    owner = _user(postgres)
    _role(postgres, owner)
    course = create_course(postgres, name="Ingestión")
    with pytest.raises(IntegrityError, match="INVALID_JOB_DEADLINE"):
        _upload(postgres, course, "upload", deadline_at=_deadline() - timedelta(hours=1))
    assert postgres.scalar(select(documents.c.id).where(documents.c.course_id == course)) is None

    result = _upload(postgres, course, "upload")
    assert _upload(postgres, course, "upload") == result
    with pytest.raises(SubmissionConflict, match="IDEMPOTENCY_CONFLICT"):
        _upload(postgres, course, "upload", data=b"distinto")
    with pytest.warns(DuplicateDocumentWarning):
        assert _upload(postgres, course, "different-key") == {"document_id": None, "job_id": None}

    job = get_job(postgres, course, UUID(result["job_id"]))
    assert (job.kind, job.target_id) == ("ingest_document", UUID(result["document_id"]))


def test_chat_and_exam_enqueue_the_generation_once_per_scope(postgres):
    owner = _user(postgres)
    _role(postgres, owner)
    course = create_course(postgres, name="Generaciones")
    with pytest.raises(IntegrityError, match="INVALID_JOB_DEADLINE"):
        _generation(
            postgres, course, "chat", "generate", deadline_at=_deadline() - timedelta(hours=1)
        )
    assert (
        postgres.scalar(select(generation_runs.c.id).where(generation_runs.c.course_id == course))
        is None
    )

    chat = _generation(postgres, course, "chat", "generate")
    exam = _generation(postgres, course, "exam", "generate")
    assert _generation(postgres, course, "chat", "generate") == chat
    assert _generation(postgres, course, "exam", "generate") == exam
    with pytest.raises(SubmissionConflict):
        _generation(postgres, course, "exam", "generate", payload={"topic": "otro"})

    _role(postgres, owner, worker=True)
    assert claim_job(postgres, "unsupported", kinds=["ingest_document"]) is None
    claimed = claim_job(postgres, "exam-worker", kinds=["generate_exam"])
    assert (claimed.id, claimed.target_id, claimed.payload) == (
        UUID(exam["job_id"]),
        UUID(exam["generation_id"]),
        {"topic": "álgebra"},
    )
    assert claim_job(postgres, "any-worker").id == UUID(chat["job_id"])


class ResultRejected(Exception):
    """La reserva no permite confirmar el resultado."""


@pytest.mark.parametrize("valid_token", [False, True])
def test_queue_result_and_completion_share_a_transaction(postgres, valid_token):
    owner = _user(postgres)
    _role(postgres, owner)
    course = create_course(postgres, name="Resultado")
    job_id = UUID(_upload(postgres, course, "upload")["job_id"])
    request_id = uuid4()

    _role(postgres, None, worker=True)
    job = claim_job(postgres, "ingestion", kinds=["ingest_document"])
    assert job.id == job_id
    token = job.lease_token if valid_token else uuid4()

    def publish():
        with postgres.begin_nested():
            record_worker_audit_event(
                postgres,
                course_id=course,
                entity_id=job_id,
                entity_type="job",
                action="job.published",
                request_id=request_id,
                result="success",
            )
            if not complete_job(postgres, job_id, token):
                raise ResultRejected

    if valid_token:
        publish()
    else:
        with pytest.raises(ResultRejected):
            publish()

    _role(postgres, owner)
    assert get_job(postgres, course, job_id).state == ("succeeded" if valid_token else "running")
    event = postgres.scalar(
        select(audit_events.c.id).where(audit_events.c.request_id == request_id)
    )
    assert (event is not None) == valid_token
