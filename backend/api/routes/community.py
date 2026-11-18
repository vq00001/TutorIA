"""Edición de perfil, membresías e invitaciones directas."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy.exc import DBAPIError

from ...db.courses import change_course_member, get_member_course, leave_course
from ...db.invitations import invite_member, list_my_invitations, respond_to_invitation
from ...db.users import update_current_display_name
from .auth import CurrentUser, CurrentUserResponse
from .dependencies import AuthenticatedConnection, ErrorResponse, InvalidSession, error_response

router = APIRouter(tags=["Community"])
_ERRORS = {code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422)}


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    displayName: str = Field(min_length=1, max_length=100)

    @field_validator("displayName", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        if isinstance(value, str):
            if "\x00" in value:
                raise ValueError("Nombre inválido")
            return value.strip()
        return value


class MemberUpdate(BaseModel):
    role: Literal["coordinator", "student"]
    status: Literal["active", "removed"] = "active"
    rowVersion: int = Field(ge=1, lt=9223372036854775807)


class InviteRequest(BaseModel):
    email: EmailStr = Field(max_length=254)


class Invitation(BaseModel):
    id: UUID
    courseId: UUID
    courseName: str
    createdAt: datetime
    expiresAt: datetime


class InvitationsResponse(BaseModel):
    data: list[Invitation]


class InvitationDecision(BaseModel):
    decision: Literal["accept", "reject"]


class InvitationResult(BaseModel):
    courseId: UUID
    status: Literal["accepted", "declined"]


class InvitationResponse(BaseModel):
    data: InvitationResult


def _course_not_found() -> JSONResponse:
    return error_response(404, "COURSE_NOT_FOUND", "Clase no encontrada.")


def _membership_error(exc: DBAPIError) -> JSONResponse | None:
    code = getattr(exc.orig, "sqlstate", None)
    primary = getattr(getattr(exc.orig, "diag", None), "message_primary", None)
    if code == "42501":
        return error_response(
            403, "COMMUNITY_FORBIDDEN", "La cuenta no puede realizar la operación."
        )
    if (code, primary) == ("23514", "LAST_COORDINATOR"):
        return error_response(409, primary, "La clase debe conservar un coordinador.")
    return None


def _check_membership_constraints(connection: AuthenticatedConnection) -> None:
    # Valida el cambio dentro del savepoint, antes de devolver la respuesta HTTP.
    connection.exec_driver_sql("SET CONSTRAINTS app.membership_coordinator IMMEDIATE")
    connection.exec_driver_sql("SET CONSTRAINTS app.membership_coordinator DEFERRED")


@router.patch("/users/me", response_model=CurrentUserResponse, responses=_ERRORS)
def profile_update(
    payload: ProfileUpdate, connection: AuthenticatedConnection, response: Response
) -> CurrentUserResponse:
    user = update_current_display_name(connection, payload.displayName)
    if user is None:
        raise InvalidSession
    response.headers["Cache-Control"] = "no-store"
    return CurrentUserResponse(
        data=CurrentUser(
            id=user.id,
            nickname=user.nickname,
            email=user.email,
            displayName=user.display_name,
            status=user.status,
            createdAt=user.created_at,
            emailVerifiedAt=user.email_verified_at,
        )
    )


@router.patch("/courses/{courseId}/members/{userId}", status_code=204, responses=_ERRORS)
def membership_update(
    courseId: UUID, userId: UUID, payload: MemberUpdate, connection: AuthenticatedConnection
) -> Response:
    if get_member_course(connection, courseId) is None:
        return _course_not_found()
    try:
        with connection.begin_nested():
            updated = change_course_member(
                connection,
                courseId,
                userId,
                role=payload.role,
                status=payload.status,
                expected_version=payload.rowVersion,
            )
            if not updated:
                return error_response(
                    409, "MEMBERSHIP_CONFLICT", "La membresía o su versión cambió."
                )
            _check_membership_constraints(connection)
    except DBAPIError as exc:
        error = _membership_error(exc)
        if error is not None:
            return error
        raise
    return Response(status_code=204)


@router.post("/courses/{courseId}/leave", status_code=204, responses=_ERRORS)
def membership_leave(courseId: UUID, connection: AuthenticatedConnection) -> Response:
    if get_member_course(connection, courseId) is None:
        return _course_not_found()
    try:
        with connection.begin_nested():
            leave_course(connection, courseId)
            _check_membership_constraints(connection)
    except DBAPIError as exc:
        error = _membership_error(exc)
        if error is not None:
            return error
        raise
    return Response(status_code=204)


@router.post("/courses/{courseId}/invitations", status_code=202, responses=_ERRORS)
def invitations_create(
    courseId: UUID, payload: InviteRequest, connection: AuthenticatedConnection
) -> Response:
    if get_member_course(connection, courseId) is None:
        return _course_not_found()
    try:
        with connection.begin_nested():
            invite_member(connection, courseId, payload.email.lower())
    except DBAPIError as exc:
        error = _membership_error(exc)
        if error is not None:
            return error
        raise
    return Response(status_code=202)


@router.get("/invitations", response_model=InvitationsResponse, responses=_ERRORS)
def invitations_index(connection: AuthenticatedConnection) -> InvitationsResponse:
    return InvitationsResponse(
        data=[
            Invitation(
                id=row.id,
                courseId=row.course_id,
                courseName=row.course_name,
                createdAt=row.created_at,
                expiresAt=row.expires_at,
            )
            for row in list_my_invitations(connection)
        ]
    )


@router.patch("/invitations/{invitationId}", response_model=InvitationResponse, responses=_ERRORS)
def invitations_respond(
    invitationId: UUID, payload: InvitationDecision, connection: AuthenticatedConnection
) -> InvitationResponse | JSONResponse:
    accepted = payload.decision == "accept"
    course_id = respond_to_invitation(connection, invitationId, accept=accepted)
    if course_id is None:
        return error_response(404, "INVITATION_NOT_FOUND", "Invitación no disponible.")
    return InvitationResponse(
        data=InvitationResult(
            courseId=course_id,
            status="accepted" if accepted else "declined",
        )
    )
