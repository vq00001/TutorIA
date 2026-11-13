from uuid import UUID, uuid4

import pytest
from sqlalchemy import LargeBinary, cast, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError, ProgrammingError

from db.conversations import append_user_message, create_conversation, messages
from db.courses import create_course
from db.documents import delete_document
from db.generations import stop_generation
from db.jobs import claim_job, get_job
from db.purge import pending_receipts
from test_courses import _user
from test_exams import _role
from test_job_integration import _generation, _upload


def test_chat_publication_uses_the_current_reservation(postgres):
    owner = _user(postgres)
    _role(postgres, owner)
    course = create_course(postgres, name="Chat")
    conversation = create_conversation(postgres, course, title="Conversación")
    reply = append_user_message(postgres, course, conversation, [{"type": "text", "text": "Hola"}])
    submitted = _generation(postgres, course, "chat", "chat")

    _role(postgres, None, worker=True)
    job = claim_job(postgres, "chat", kinds=["generate_chat"])
    assert str(job.id) == submitted["job_id"]
    source = postgres.scalar(select(func.app.record_job_request(
        job.id, job.lease_token, cast(b'{"messages":[]}', LargeBinary), cast([], JSONB)
    )))
    content = cast([{"type": "text", "text": "Respuesta"}], JSONB)
    with pytest.raises(ProgrammingError, match="JOB_LEASE_LOST"), postgres.begin_nested():
        postgres.scalar(select(func.app.publish_chat_job(
            job.id, uuid4(), conversation, content, reply, source, cast([], JSONB)
        )))
    message = postgres.scalar(select(func.app.publish_chat_job(
        job.id, job.lease_token, conversation, content, reply, source, cast([], JSONB)
    )))
    _role(postgres, owner)
    assert get_job(postgres, course, job.id).state == "succeeded"
    assert postgres.scalar(select(messages.c.generation_id).where(messages.c.id == message)) == (
        job.target_id
    )


def test_cancel_and_delete_remove_active_jobs_and_register_document_receipt(postgres):
    owner = _user(postgres)
    _role(postgres, owner)
    course = create_course(postgres, name="Retirada")
    submitted = _generation(postgres, course, "exam", "cancel")
    assert stop_generation(postgres, UUID(submitted["generation_id"]), status="cancelled")
    assert get_job(postgres, course, UUID(submitted["job_id"])) is None

    upload = _upload(postgres, course, "delete")
    document = UUID(upload["document_id"])
    assert delete_document(postgres, course, document)
    assert get_job(postgres, course, UUID(upload["job_id"])) is None

    postgres.exec_driver_sql("RESET ROLE")
    (receipt,) = [row for row in pending_receipts(postgres) if row.subject_id == document]
    assert receipt.scope == "document"
    assert receipt.completed_at is not None

def test_expired_ingestion_reuses_run_only_with_current_token(postgres):
    from datetime import timedelta

    from sqlalchemy import update

    from db.jobs import jobs, recover_jobs
    from db.processing import processing_runs, register_embedding_space

    owner = _user(postgres)
    space = register_embedding_space(
        postgres, provider="ollama", model_name="test", model_revision=uuid4().hex, dimension=2
    )
    _role(postgres, owner)
    course = create_course(postgres, name="Ingestión recuperable")
    submitted = _upload(postgres, course, "resume")
    _role(postgres, None, worker=True)
    first = claim_job(postgres, "first", kinds=["ingest_document"])
    assert str(first.id) == submitted["job_id"]

    def start(job):
        return postgres.scalar(select(func.app.start_ingestion_job(
            job.id, job.lease_token, space, cast(bytes(32), LargeBinary),
            "parser", "chunker", cast({}, JSONB),
        )))

    run = start(first)
    assert run is not None
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(update(jobs).where(jobs.c.id == first.id).values(
        lease_until=func.clock_timestamp() - timedelta(seconds=1)
    ))
    _role(postgres, None, worker=True)
    assert recover_jobs(postgres) == 1
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(update(jobs).where(jobs.c.id == first.id).values(
        run_after=func.clock_timestamp()
    ))
    _role(postgres, None, worker=True)
    second = claim_job(postgres, "second", kinds=["ingest_document"])
    assert second.lease_token != first.lease_token
    assert start(second) == run
    with pytest.raises(ProgrammingError, match="JOB_LEASE_LOST"), postgres.begin_nested():
        start(first)
    postgres.exec_driver_sql("RESET ROLE")
    assert postgres.scalar(select(processing_runs.c.ingestion_job_id).where(
        processing_runs.c.id == run
    )) == first.id


def test_exam_publication_checks_request_and_completes_job(postgres):
    from db.exams import exams

    owner = _user(postgres)
    _role(postgres, owner)
    course = create_course(postgres, name="Examen")
    submitted = _generation(postgres, course, "exam", "exam")
    _role(postgres, None, worker=True)
    job = claim_job(postgres, "exam", kinds=["generate_exam"])
    assert str(job.id) == submitted["job_id"]
    request = postgres.scalar(select(func.app.record_job_request(
        job.id, job.lease_token, cast(b'{"messages":[]}', LargeBinary), cast([], JSONB)
    )))

    def publish(request_id):
        return postgres.scalar(select(func.app.publish_exam_job(
            job.id, job.lease_token, "Práctica", "practice", 600, cast({
                "request_id": str(request_id),
                "questions": [{
                    "kind": "true_false", "prompt": "¿Es correcto?",
                    "explanation": "Sí.", "options": ["Verdadero", "Falso"],
                    "correct_indices": [0], "max_points": "1",
                    "source_ranks": [],
                }],
            }, JSONB)
        )))

    with pytest.raises(IntegrityError, match="INVALID_JOB_REQUEST"), postgres.begin_nested():
        publish(uuid4())
    exam = publish(request)
    _role(postgres, owner)
    assert get_job(postgres, course, job.id).state == "succeeded"
    assert postgres.scalar(select(exams.c.generation_id).where(exams.c.id == exam)) == job.target_id
