"""Operaciones transaccionales que crean un recurso y encolan su procesamiento."""

from datetime import datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from .courses import courses
from .documents import register_upload
from .generations import start_generation
from .idempotency import run_idempotent
from .jobs import enqueue_job


class SubmissionConflict(ValueError):
    """La clave ya se usó con otros datos en el mismo ámbito."""


class CourseUnavailable(PermissionError):
    """La clase no está activa o el usuario no puede operar en ella."""


def _submit(conn, course_id, operation, key, fingerprint, create):
    visible = conn.scalar(select(courses.c.id).where(
        courses.c.id == course_id, courses.c.status == "active"
    ))
    if visible is None:
        raise CourseUnavailable("COURSE_UNAVAILABLE")
    try:
        return run_idempotent(
            conn, scope=f"{operation}:{course_id}", key=key, payload=fingerprint, operation=create
        )
    except DBAPIError as exc:
        if (
            getattr(exc.orig, "sqlstate", None) == "23505"
            and getattr(getattr(exc.orig, "diag", None), "message_primary", None)
            == "IDEMPOTENCY_CONFLICT"
        ):
            raise SubmissionConflict("IDEMPOTENCY_CONFLICT") from exc
        raise


def enqueue_upload(
    conn: Connection, course_id: UUID, *, filename: str, mime_type: str,
    data: bytes, deadline_at: datetime, **metadata,
) -> dict[str, str | None]:
    """Guarda y encola una carga dentro de la transacción del llamador."""
    document = register_upload(
        conn, course_id=course_id, data=data, filename=filename, mime_type=mime_type, **metadata
    )
    if document is None:
        return {"document_id": None, "job_id": None}
    job = enqueue_job(
        conn, course_id, kind="ingest_document", target_id=document,
        idempotency_key=str(document), deadline_at=deadline_at,
    )
    return {"document_id": str(document), "job_id": str(job)}


def submit_upload(
    conn: Connection,
    *,
    course_id: UUID,
    key: str,
    deadline_at: datetime,
    filename: str,
    data: bytes,
    mime_type: str,
    subject: str = "",
    title: str | None = None,
    description: str = "",
    authors: list[str] | None = None,
) -> dict[str, str | None]:
    """Guarda el original y encola ingestión. Un duplicado por bytes no genera trabajo."""
    fields = {
        "filename": filename, "mime_type": mime_type, "subject": subject,
        "title": title or filename, "description": description, "authors": list(authors or []),
    }

    def create(active: Connection) -> dict[str, str | None]:
        return enqueue_upload(active, course_id, data=data, deadline_at=deadline_at, **fields)

    return _submit(
        conn, course_id, "ingest_document", key,
        {**fields, "sha256": sha256(data).hexdigest()}, create,
    )


def submit_generation(
    conn: Connection,
    course_id: UUID,
    *,
    key: str,
    deadline_at: datetime,
    kind: Literal["chat", "exam"],
    provider: str,
    model_name: str,
    model_revision: str,
    prompt_version: str,
    payload: dict,
) -> dict[str, str]:
    """Reserva el cupo de generación y encola los datos que necesita el consumidor."""
    if kind not in ("chat", "exam"):
        raise ValueError("INVALID_GENERATION_KIND")
    if not isinstance(payload, dict):
        raise TypeError("INVALID_JOB_PAYLOAD")
    job_kind = "generate_chat" if kind == "chat" else "generate_exam"
    model = {
        "provider": provider, "model_name": model_name,
        "model_revision": model_revision, "prompt_version": prompt_version,
    }

    def create(active: Connection) -> dict[str, str]:
        generation = start_generation(active, course_id, kind=kind, **model)
        job = enqueue_job(
            active, course_id, kind=job_kind, target_id=generation, payload=payload,
            idempotency_key=str(generation), deadline_at=deadline_at,
        )
        return {"generation_id": str(generation), "job_id": str(job)}

    return _submit(conn, course_id, job_kind, key, {"model": model, "payload": payload}, create)
