"""Resumen, actividad y notificaciones de la cuenta autenticada."""

from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...db.courses import get_member_course
from ...db.dashboard import daily_study_seconds, page_activity
from ...db.invitations import list_my_invitations
from .dependencies import AuthenticatedConnection, ErrorResponse, error_response

router = APIRouter()
_ERRORS = {code: {"model": ErrorResponse} for code in (401, 404, 422)}


class StudyDay(BaseModel):
    date: date
    seconds: int


class DashboardSummary(BaseModel):
    studyHours: float
    studyHoursPeriod: Literal["last7DaysUtc"] = "last7DaysUtc"
    studyHoursByDay: list[float]
    studyByDay: list[StudyDay]
    pendingInvitations: int


class DashboardResponse(BaseModel):
    data: DashboardSummary


class Activity(BaseModel):
    id: UUID
    courseId: UUID | None
    action: str
    type: str
    result: Literal["success", "denied", "error"]
    createdAt: datetime


class Pagination(BaseModel):
    page: int
    pageSize: int
    total: int


class ActivityResponse(BaseModel):
    data: list[Activity]
    meta: Pagination


class Notification(BaseModel):
    id: UUID
    courseId: UUID
    type: Literal["course_invitation"] = "course_invitation"
    title: str
    message: str
    createdAt: datetime
    expiresAt: datetime


class NotificationsResponse(BaseModel):
    data: list[Notification]


@router.get(
    "/dashboard/summary", tags=["Dashboard"], response_model=DashboardResponse, responses=_ERRORS
)
def dashboard_summary(connection: AuthenticatedConnection) -> DashboardResponse:
    today = datetime.now(timezone.utc).date()
    first_day = today - timedelta(days=6)
    totals = dict(daily_study_seconds(connection, first_day))
    days = [
        StudyDay(date=day, seconds=int(totals.get(day, 0)))
        for day in (first_day + timedelta(days=offset) for offset in range(7))
    ]
    return DashboardResponse(
        data=DashboardSummary(
            studyHours=sum(day.seconds for day in days) / 3600,
            studyHoursByDay=[day.seconds / 3600 for day in days],
            studyByDay=days,
            pendingInvitations=len(list_my_invitations(connection)),
        )
    )


@router.get("/activity", tags=["Activity"], response_model=ActivityResponse, responses=_ERRORS)
def activity_index(
    connection: AuthenticatedConnection,
    page: Annotated[int, Query(ge=1, le=2147483647)] = 1,
    pageSize: Annotated[int, Query(ge=1, le=100)] = 20,
    type: Annotated[
        str | None, Query(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]*$")
    ] = None,
) -> ActivityResponse:
    rows, total = page_activity(connection, page=page, page_size=pageSize, entity_type=type)
    return ActivityResponse(
        data=[
            Activity(
                id=row.id,
                courseId=row.course_id,
                action=row.action,
                type=row.entity_type,
                result=row.result,
                createdAt=row.created_at,
            )
            for row in rows
        ],
        meta=Pagination(page=page, pageSize=pageSize, total=total),
    )


def _notifications(connection, course_id: UUID | None = None) -> NotificationsResponse:
    return NotificationsResponse(
        data=[
            Notification(
                id=row.id,
                courseId=row.course_id,
                title="Invitación a una clase",
                message=row.course_name,
                createdAt=row.created_at,
                expiresAt=row.expires_at,
            )
            for row in list_my_invitations(connection)
            if course_id is None or row.course_id == course_id
        ]
    )


@router.get(
    "/notifications",
    tags=["Notifications"],
    response_model=NotificationsResponse,
    responses=_ERRORS,
)
def notifications_index(connection: AuthenticatedConnection) -> NotificationsResponse:
    return _notifications(connection)


@router.get(
    "/courses/{courseId}/notifications",
    tags=["Notifications"],
    response_model=NotificationsResponse,
    responses=_ERRORS,
)
def course_notifications(
    courseId: UUID,
    connection: AuthenticatedConnection,
) -> NotificationsResponse | JSONResponse:
    result = _notifications(connection, courseId)
    if not result.data and get_member_course(connection, courseId) is None:
        return error_response(404, "COURSE_NOT_FOUND", "Clase no encontrada.")
    return result
