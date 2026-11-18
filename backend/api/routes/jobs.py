"""Consulta de trabajos propios."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...db.jobs import get_job
from .dependencies import AuthenticatedConnection, ErrorResponse, error_response

router = APIRouter(tags=["Jobs"])


class JobData(BaseModel):
    id: UUID
    kind: Literal["ingest_document", "generate_chat", "generate_exam"]
    targetId: UUID
    state: Literal["queued", "running", "succeeded", "failed"]
    attempts: int
    maxAttempts: int
    runAfter: datetime
    deadlineAt: datetime
    errorCode: str | None
    createdAt: datetime


class JobResponse(BaseModel):
    data: JobData


@router.get("/courses/{courseId}/jobs/{jobId}", response_model=JobResponse,
         responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
def jobs_detail(courseId: UUID, jobId: UUID, connection: AuthenticatedConnection) -> JobResponse | JSONResponse:
    row = get_job(connection, courseId, jobId)
    if row is None:
        return error_response(404, "JOB_NOT_FOUND", "Trabajo no encontrado.")
    return JobResponse(data=JobData(
        id=row.id, kind=row.kind, targetId=row.target_id, state=row.state,
        attempts=row.attempts, maxAttempts=row.max_attempts, runAfter=row.run_after,
        deadlineAt=row.deadline_at, errorCode=row.error_code, createdAt=row.created_at,
    ))
