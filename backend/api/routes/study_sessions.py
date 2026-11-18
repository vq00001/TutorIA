"""Sesiones privadas de estudio."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import DBAPIError

from ...db.courses import get_member_course
from ...db.study_sessions import (
    finish_study_session,
    get_study_session,
    heartbeat_study_session,
    list_study_sessions,
    start_study_session,
)
from .dependencies import AuthenticatedConnection, ErrorResponse, error_response

router = APIRouter(prefix="/courses/{courseId}/study-sessions", tags=["Learning"])
_ERRORS = {code: {"model": ErrorResponse} for code in (401, 404, 422)}
_WRITE_ERRORS = {**_ERRORS, 403: {"model": ErrorResponse}}


class StudySession(BaseModel):
    id: UUID
    courseId: UUID
    startedAt: datetime
    lastHeartbeatAt: datetime
    endedAt: datetime | None
    activeSeconds: int


class StudySessionResponse(BaseModel):
    data: StudySession


class StudySessionListResponse(BaseModel):
    data: list[StudySession]


def _session(row) -> StudySession:
    return StudySession(
        id=row.id,
        courseId=row.course_id,
        startedAt=row.started_at,
        lastHeartbeatAt=row.last_heartbeat_at,
        endedAt=row.ended_at,
        activeSeconds=row.active_seconds,
    )


def _not_found() -> JSONResponse:
    return error_response(404, "STUDY_SESSION_NOT_FOUND", "Clase o sesión no encontrada.")


def _detail(connection, course_id: UUID, session_id: UUID) -> StudySessionResponse | JSONResponse:
    row = get_study_session(connection, course_id, session_id)
    if row is None:
        return _not_found()
    return StudySessionResponse(data=_session(row))


@router.get("", response_model=StudySessionListResponse, responses=_ERRORS)
def sessions_index(
    courseId: UUID,
    connection: AuthenticatedConnection,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> StudySessionListResponse | JSONResponse:
    if get_member_course(connection, courseId) is None:
        return _not_found()
    return StudySessionListResponse(
        data=[_session(row) for row in list_study_sessions(connection, courseId, limit=limit)]
    )


@router.get("/{sessionId}", response_model=StudySessionResponse, responses=_ERRORS)
def sessions_detail(
    courseId: UUID,
    sessionId: UUID,
    connection: AuthenticatedConnection,
) -> StudySessionResponse | JSONResponse:
    return _detail(connection, courseId, sessionId)


@router.post("", response_model=StudySessionResponse, responses=_WRITE_ERRORS)
def sessions_start(
    courseId: UUID,
    connection: AuthenticatedConnection,
) -> StudySessionResponse | JSONResponse:
    if get_member_course(connection, courseId) is None:
        return _not_found()
    try:
        with connection.begin_nested():
            session_id = start_study_session(connection, courseId)
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "42501":
            return error_response(403, "STUDY_SESSION_FORBIDDEN", "La clase no admite sesiones.")
        raise
    return _detail(connection, courseId, session_id)


def _update(connection, course_id: UUID, session_id: UUID, *, finish: bool):
    if get_study_session(connection, course_id, session_id) is None:
        return _not_found()
    operation = finish_study_session if finish else heartbeat_study_session
    try:
        with connection.begin_nested():
            operation(connection, course_id, session_id)
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "42501":
            return error_response(403, "STUDY_SESSION_FORBIDDEN", "La sesión no admite cambios.")
        raise
    return _detail(connection, course_id, session_id)


@router.post("/{sessionId}/heartbeat", response_model=StudySessionResponse, responses=_WRITE_ERRORS)
def sessions_heartbeat(
    courseId: UUID,
    sessionId: UUID,
    connection: AuthenticatedConnection,
) -> StudySessionResponse | JSONResponse:
    return _update(connection, courseId, sessionId, finish=False)


@router.post("/{sessionId}/finish", response_model=StudySessionResponse, responses=_WRITE_ERRORS)
def sessions_finish(
    courseId: UUID,
    sessionId: UUID,
    connection: AuthenticatedConnection,
) -> StudySessionResponse | JSONResponse:
    return _update(connection, courseId, sessionId, finish=True)
