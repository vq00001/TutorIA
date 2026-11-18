"""Inicio, entrega y resultados de intentos de examen."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import DBAPIError

from ...db.exam_attempts import (
    ExamAnswer,
    get_exam_attempt,
    list_attempt_answers,
    review_development_answer,
    start_exam_attempt,
    submit_exam_attempt,
)
from ...db.exams import get_exam
from .dependencies import AuthenticatedConnection, ErrorResponse, error_response

router = APIRouter(prefix="/courses/{courseId}/exams/{examId}/attempts", tags=["Exams"])
_ERRORS = {code: {"model": ErrorResponse} for code in (401, 404, 422)}
_WRITE_ERRORS = {**_ERRORS, **{code: {"model": ErrorResponse} for code in (403, 409)}}


class AnswerInput(BaseModel):
    questionId: UUID
    selectedOptionIds: list[UUID] = Field(default_factory=list, max_length=6)
    textAnswer: str | None = Field(default=None, max_length=20000)

    @field_validator("textAnswer")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("Respuesta inválida")
        return value


class SubmitAttemptRequest(BaseModel):
    answers: list[AnswerInput] = Field(max_length=20)


class DevelopmentReviewRequest(BaseModel):
    points: Decimal = Field(ge=0, le=Decimal("999.99999"), decimal_places=5)
    feedback: str = Field(min_length=1, max_length=10000)

    @field_validator("feedback")
    @classmethod
    def validate_feedback(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("Feedback inválido")
        return value


class AnswerResult(BaseModel):
    answerId: UUID
    questionId: UUID
    selectedOptionIds: list[UUID]
    textAnswer: str | None
    points: Decimal | None
    feedback: str | None
    gradeStatus: str
    gradeMethod: str | None


class Attempt(BaseModel):
    id: UUID
    examId: UUID
    attemptNo: int
    status: Literal["in_progress", "expired", "graded", "needs_review", "invalidated"]
    startedAt: datetime
    deadlineAt: datetime
    submittedAt: datetime | None
    gradedAt: datetime | None
    scorePercent: Decimal | None
    answers: list[AnswerResult]


class AttemptResponse(BaseModel):
    data: Attempt


def _not_found() -> JSONResponse:
    return error_response(404, "ATTEMPT_NOT_FOUND", "Intento no encontrado.")


def _read_attempt(connection, course_id: UUID, exam_id: UUID, attempt_id: UUID):
    row = get_exam_attempt(connection, course_id, attempt_id)
    return row if row is not None and row.exam_id == exam_id else None


def _result(connection, course_id: UUID, row) -> AttemptResponse:
    status = row.status
    if status == "in_progress" and row.deadline_at <= datetime.now(timezone.utc):
        status = "expired"
    answers = []
    if status in ("graded", "needs_review"):
        answers = [
            AnswerResult(
                answerId=item.id,
                questionId=item.question_id,
                selectedOptionIds=item.selected_option_ids,
                textAnswer=item.text_answer,
                points=item.points,
                feedback=item.feedback,
                gradeStatus=item.grade_status,
                gradeMethod=item.grade_method,
            )
            for item in list_attempt_answers(connection, course_id, row.id)
        ]
    return AttemptResponse(
        data=Attempt(
            id=row.id,
            examId=row.exam_id,
            attemptNo=row.attempt_no,
            status=status,
            startedAt=row.started_at,
            deadlineAt=row.deadline_at,
            submittedAt=row.submitted_at,
            gradedAt=row.graded_at,
            scorePercent=row.score_percent,
            answers=answers,
        )
    )


def _write_error(exc: DBAPIError) -> JSONResponse | None:
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "42501":
        return error_response(403, "ATTEMPT_FORBIDDEN", "El intento no admite esta operación.")
    primary = getattr(getattr(exc.orig, "diag", None), "message_primary", None)
    if sqlstate == "23514" and primary in ("INVALID_EXAM_ANSWERS", "INVALID_ANSWER_OPTIONS"):
        return error_response(422, primary, "Revisa las respuestas del examen.")
    return None


@router.post("", response_model=AttemptResponse, responses=_WRITE_ERRORS)
def attempts_start(
    courseId: UUID,
    examId: UUID,
    connection: AuthenticatedConnection,
) -> AttemptResponse | JSONResponse:
    exam = get_exam(connection, courseId, examId)
    if exam is None:
        return error_response(404, "EXAM_NOT_FOUND", "Examen no encontrado.")
    if exam.status != "ready":
        return error_response(409, "EXAM_UNAVAILABLE", "El examen ya no está disponible.")
    try:
        with connection.begin_nested():
            attempt = start_exam_attempt(connection, courseId, examId)
    except DBAPIError as exc:
        response = _write_error(exc)
        if response is not None:
            return response
        raise
    return _result(connection, courseId, get_exam_attempt(connection, courseId, attempt))


@router.get("/{attemptId}", response_model=AttemptResponse, responses=_ERRORS)
def attempts_detail(
    courseId: UUID,
    examId: UUID,
    attemptId: UUID,
    connection: AuthenticatedConnection,
) -> AttemptResponse | JSONResponse:
    row = _read_attempt(connection, courseId, examId, attemptId)
    return _result(connection, courseId, row) if row is not None else _not_found()


@router.post("/{attemptId}/submissions", response_model=AttemptResponse, responses=_WRITE_ERRORS)
def attempts_submit(
    courseId: UUID,
    examId: UUID,
    attemptId: UUID,
    payload: SubmitAttemptRequest,
    connection: AuthenticatedConnection,
) -> AttemptResponse | JSONResponse:
    row = _read_attempt(connection, courseId, examId, attemptId)
    if row is None:
        return _not_found()
    if row.status == "invalidated":
        return error_response(409, "EXAM_UNAVAILABLE", "El examen ya no está disponible.")
    items = [
        ExamAnswer(item.questionId, item.selectedOptionIds, item.textAnswer)
        for item in payload.answers
    ]
    try:
        with connection.begin_nested():
            submitted = submit_exam_attempt(connection, courseId, attemptId, items)
    except DBAPIError as exc:
        response = _write_error(exc)
        if response is not None:
            return response
        raise
    if submitted is None:
        return error_response(409, "ATTEMPT_EXPIRED", "El plazo del intento venció.")
    return _result(connection, courseId, get_exam_attempt(connection, courseId, attemptId))


@router.put(
    "/{attemptId}/answers/{answerId}/review",
    response_model=AttemptResponse,
    responses=_WRITE_ERRORS,
)
def attempts_review_development(
    courseId: UUID,
    examId: UUID,
    attemptId: UUID,
    answerId: UUID,
    payload: DevelopmentReviewRequest,
    connection: AuthenticatedConnection,
) -> AttemptResponse | JSONResponse:
    row = _read_attempt(connection, courseId, examId, attemptId)
    if row is None:
        return _not_found()
    if row.status == "invalidated":
        return error_response(409, "EXAM_UNAVAILABLE", "El examen ya no está disponible.")
    try:
        with connection.begin_nested():
            review_development_answer(
                connection,
                courseId,
                attemptId,
                answerId,
                points=payload.points,
                feedback=payload.feedback,
            )
    except DBAPIError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        primary = getattr(getattr(exc.orig, "diag", None), "message_primary", None)
        if sqlstate == "23514":
            if primary == "INVALID_DEVELOPMENT_REVIEW":
                return error_response(422, primary, "Revisa la respuesta y la nota asignada.")
            if primary in ("ATTEMPT_NOT_SUBMITTED", "ANSWER_NOT_PENDING_REVIEW"):
                return error_response(409, primary, "La respuesta no está pendiente de revisión.")
        response = _write_error(exc)
        if response is not None:
            return response
        raise
    return _result(connection, courseId, get_exam_attempt(connection, courseId, attemptId))
