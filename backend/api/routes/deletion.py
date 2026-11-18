"""Solicitudes de borrado de cuenta y clase."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import DBAPIError

from ...db.courses import get_member_course
from ...db.purge import request_own_purge
from ...db.users import get_current_user
from .dependencies import AuthenticatedConnection, ErrorResponse, InvalidSession, error_response

router = APIRouter(tags=["Deletion"])
_ERRORS = {code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422)}


class DeletionRequest(BaseModel):
    requestId: UUID
    status: Literal["deletion_pending"] = "deletion_pending"


class DeletionResponse(BaseModel):
    data: DeletionRequest


def _request(connection, scope: str, subject: UUID) -> DeletionResponse | JSONResponse:
    try:
        with connection.begin_nested():
            receipt = request_own_purge(connection, scope=scope, subject_id=subject)
    except DBAPIError as exc:
        state = getattr(exc.orig, "sqlstate", None)
        primary = getattr(getattr(exc.orig, "diag", None), "message_primary", None)
        if (state, primary) == ("23514", "LAST_COORDINATOR"):
            return error_response(409, primary, "La clase debe conservar un coordinador.")
        if state == "42501":
            return error_response(403, "DELETION_FORBIDDEN", "La cuenta no puede solicitar el borrado.")
        raise
    return DeletionResponse(data=DeletionRequest(requestId=receipt))


@router.delete("/users/me", status_code=202, response_model=DeletionResponse, responses=_ERRORS)
def account_delete(connection: AuthenticatedConnection) -> DeletionResponse | JSONResponse:
    actor = get_current_user(connection)
    if actor is None:
        raise InvalidSession
    return _request(connection, "account", actor.id)


@router.delete("/courses/{courseId}", status_code=202, response_model=DeletionResponse,
               responses=_ERRORS)
def course_delete(courseId: UUID, connection: AuthenticatedConnection) -> DeletionResponse | JSONResponse:
    course = get_member_course(connection, courseId)
    if course is None:
        return error_response(404, "COURSE_NOT_FOUND", "Clase no encontrada.")
    return _request(connection, "course", courseId)
