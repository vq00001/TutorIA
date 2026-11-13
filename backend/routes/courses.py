"""Rutas de clases y membresías."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from ..db.courses import create_course, get_member_course, list_course_members, page_member_courses
from .dependencies import AuthenticatedConnection, ErrorResponse, error_response

router = APIRouter(tags=["Courses"])


class CreateCourseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str
    objectives: str
    subject: str = Field(default="", max_length=150)
    color: Literal["mauve", "blue", "green", "peach", "pink", "yellow", "teal", "lavender"] = "mauve"
    icon: str = Field(default="✦", max_length=16)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("name", "description", "objectives", "subject", "icon")
    @classmethod
    def reject_null_bytes(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Texto inválido")
        return value


class CourseMember(BaseModel):
    userId: UUID
    displayName: str
    role: Literal["coordinator", "student"]
    joinedAt: datetime


class CourseData(BaseModel):
    id: UUID
    name: str
    description: str
    objectives: str
    subject: str
    color: str
    icon: str
    status: Literal["active", "archived"]
    myRole: Literal["coordinator", "student"]
    ownerId: UUID | None
    rowVersion: int
    members: list[CourseMember] | None = None


class CourseResponse(BaseModel):
    data: CourseData


class CoursePage(BaseModel):
    page: int
    pageSize: int
    total: int


class CourseListResponse(BaseModel):
    data: list[CourseData]
    meta: CoursePage


class CourseMembersResponse(BaseModel):
    data: list[CourseMember]


def _course_data(row, members=None) -> CourseData:
    return CourseData(
        id=row.id, name=row.name, description=row.description, objectives=row.objectives,
        subject=row.subject, color=row.color, icon=row.icon, status=row.status,
        myRole=row.my_role, ownerId=row.created_by, rowVersion=row.row_version, members=members,
    )


def _members(connection: Connection, course_id: UUID) -> list[CourseMember]:
    return [
        CourseMember(userId=row.user_id, displayName=row.display_name,
                     role=row.role, joinedAt=row.joined_at)
        for row in list_course_members(connection, course_id)
    ]


def _course_not_found() -> JSONResponse:
    return error_response(404, "COURSE_NOT_FOUND", "Clase no encontrada.")


@router.get("/courses", response_model=CourseListResponse,
         response_model_exclude_unset=True, responses={401: {"model": ErrorResponse}})
def courses_index(
    connection: AuthenticatedConnection,
    page: Annotated[int, Query(ge=1)] = 1,
    pageSize: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CourseListResponse:
    rows, total = page_member_courses(connection, page=page, page_size=pageSize)
    return CourseListResponse(
        data=[_course_data(row) for row in rows],
        meta=CoursePage(page=page, pageSize=pageSize, total=total),
    )


@router.post("/courses", status_code=201, response_model=CourseResponse,
          responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse},
                     422: {"model": ErrorResponse}})
def courses_create(
    payload: CreateCourseRequest, connection: AuthenticatedConnection
) -> CourseResponse | JSONResponse:
    try:
        with connection.begin_nested():
            course_id = create_course(connection, **payload.model_dump())
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) == "42501":
            return error_response(403, "COURSE_FORBIDDEN", "La cuenta no puede crear clases.")
        raise
    row = get_member_course(connection, course_id)
    return CourseResponse(data=_course_data(row, _members(connection, course_id)))


@router.get("/courses/{courseId}", response_model=CourseResponse,
         responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
def courses_detail(courseId: UUID, connection: AuthenticatedConnection) -> CourseResponse | JSONResponse:
    row = get_member_course(connection, courseId)
    if row is None:
        return _course_not_found()
    return CourseResponse(data=_course_data(row, _members(connection, courseId)))


@router.get("/courses/{courseId}/members", response_model=CourseMembersResponse,
         responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
def courses_members(
    courseId: UUID, connection: AuthenticatedConnection
) -> CourseMembersResponse | JSONResponse:
    if get_member_course(connection, courseId) is None:
        return _course_not_found()
    return CourseMembersResponse(data=_members(connection, courseId))
