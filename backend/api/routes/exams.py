"""Consulta de exámenes propios y solicitudes de generación."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import DBAPIError

from ...core.config import Config
from ...db.courses import get_member_course
from ...db.exams import get_exam, list_exam_questions, list_exams
from ...db.submissions import CourseUnavailable, SubmissionConflict, submit_generation
from .dependencies import AuthenticatedConnection, ErrorResponse, error_response

router = APIRouter(prefix="/courses/{courseId}/exams", tags=["Exams"])
_ERRORS = {code: {"model": ErrorResponse} for code in (401, 404, 422)}
ExamKind = Literal["diagnostic", "practice"]
QuestionKind = Literal["alternatives", "multiple", "development", "true_false"]


class GenerateExamRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=255)
    questionCount: int = Field(ge=1, le=20)
    durationMinutes: int = Field(default=30, ge=1, le=120)
    questionTypes: list[Literal["alternatives", "multiple", "development", "true-false"]] | None = (
        Field(default=None, min_length=1, max_length=4)
    )

    @field_validator("topic", mode="before")
    @classmethod
    def normalize_topic(cls, value: object) -> object:
        if isinstance(value, str):
            if "\x00" in value:
                raise ValueError("Tema inválido")
            return value.strip()
        return value


class ExamSummary(BaseModel):
    id: UUID
    generationId: UUID
    title: str
    kind: ExamKind
    status: Literal["ready", "invalidated"]
    durationSeconds: int
    publishedAt: datetime
    questionCount: int


class ExamOption(BaseModel):
    id: UUID
    position: int
    text: str


class ExamQuestion(BaseModel):
    id: UUID
    position: int
    kind: QuestionKind
    prompt: str
    maxPoints: Decimal
    options: list[ExamOption]


class ExamDetail(ExamSummary):
    questions: list[ExamQuestion]


class ExamsResponse(BaseModel):
    data: list[ExamSummary]


class ExamResponse(BaseModel):
    data: ExamDetail


class AcceptedExam(BaseModel):
    generationId: UUID
    jobId: UUID


class AcceptedResponse(BaseModel):
    data: AcceptedExam


def _summary(row) -> ExamSummary:
    return ExamSummary(
        id=row.id, generationId=row.generation_id, title=row.title, kind=row.kind,
        status=row.status, durationSeconds=row.duration_seconds, publishedAt=row.published_at,
        questionCount=row.question_count,
    )


@router.get("", response_model=ExamsResponse, responses=_ERRORS)
def exams_index(
    courseId: UUID, connection: AuthenticatedConnection,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ExamsResponse | JSONResponse:
    if get_member_course(connection, courseId) is None:
        return error_response(404, "COURSE_NOT_FOUND", "Clase no encontrada.")
    return ExamsResponse(data=[_summary(row) for row in list_exams(connection, courseId, limit=limit)])


@router.get("/{examId}", response_model=ExamResponse, responses=_ERRORS)
def exams_detail(
    courseId: UUID, examId: UUID, connection: AuthenticatedConnection,
) -> ExamResponse | JSONResponse:
    row = get_exam(connection, courseId, examId)
    if row is None:
        return error_response(404, "EXAM_NOT_FOUND", "Examen no encontrado.")
    questions = [
        ExamQuestion(
            id=item.id, position=item.position, kind=item.kind, prompt=item.prompt,
            maxPoints=item.max_points, options=item.options,
        )
        for item in list_exam_questions(connection, courseId, examId)
    ]
    return ExamResponse(data=ExamDetail(**_summary(row).model_dump(), questions=questions))


@router.post(
    "", status_code=202, response_model=AcceptedResponse,
    responses={**_ERRORS, **{code: {"model": ErrorResponse} for code in (403, 409, 429)}},
)
def exams_generate(
    courseId: UUID, payload: GenerateExamRequest, connection: AuthenticatedConnection,
    key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> AcceptedResponse | JSONResponse:
    if not key.strip():
        return error_response(422, "INVALID_IDEMPOTENCY_KEY", "La clave no puede estar vacía.")
    if get_member_course(connection, courseId) is None:
        return error_response(404, "COURSE_NOT_FOUND", "Clase no encontrada.")
    try:
        config = Config.from_env()
        result = submit_generation(
            connection, courseId, key=key, kind="exam", provider="ai_gateway",
            model_name=config.model_llm,
            model_revision=config.model_llm_revision,
            prompt_version="v1",
            deadline_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            payload={
                "topic": payload.topic, "question_count": payload.questionCount,
                "duration_seconds": payload.durationMinutes * 60,
                "question_types": [kind.replace("-", "_") for kind in payload.questionTypes]
                if payload.questionTypes is not None else None,
            },
        )
    except CourseUnavailable:
        return error_response(403, "EXAM_FORBIDDEN", "La clase no admite generación de exámenes.")
    except SubmissionConflict:
        return error_response(409, "IDEMPOTENCY_CONFLICT", "La clave ya se usó con otra solicitud.")
    except DBAPIError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        primary = getattr(getattr(exc.orig, "diag", None), "message_primary", None)
        if (sqlstate, primary) == ("23514", "GENERATION_QUOTA_EXCEEDED"):
            return error_response(429, primary, "Se alcanzó la cuota de generación.")
        if sqlstate == "42501":
            return error_response(403, "EXAM_FORBIDDEN", "La cuenta no puede generar exámenes.")
        raise
    return AcceptedResponse(data=AcceptedExam(generationId=result["generation_id"], jobId=result["job_id"]))
