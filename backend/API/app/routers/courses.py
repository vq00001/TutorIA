from uuid import UUID

from app import stubs
from app.deps import Page, PageSize, error_responses, no_content, require_auth
from app.schemas import (
    Course,
    CreateCourseRequest,
    Envelope,
    Invitation,
    InvitationDecisionRequest,
    InviteRequest,
    ListEnvelope,
    Member,
    PagedEnvelope,
    UpdateCourseRequest,
    UpdateMemberRequest,
)
from fastapi import APIRouter, Depends, Response

router = APIRouter(
    tags=["Courses"],
    dependencies=[Depends(require_auth)],
    responses=error_responses(401, 422),
)


@router.get(
    "/courses",
    response_model=PagedEnvelope[Course],
    summary="Lista los cursos del usuario",
)
async def list_courses(page: Page = 1, pageSize: PageSize = 20):
    return stubs.course_list()


@router.post(
    "/courses",
    status_code=201,
    response_model=Envelope[Course],
    summary="Crea un curso",
)
async def create_course(body: CreateCourseRequest):
    return stubs.course_response()


@router.get(
    "/courses/{courseId}",
    response_model=Envelope[Course],
    summary="Obtiene la información de un curso",
    responses=error_responses(404),
)
async def get_course(courseId: UUID):
    return stubs.course_response()


@router.patch(
    "/courses/{courseId}",
    response_model=Envelope[Course],
    summary="Configura un curso",
    description="Requiere permisos de propietario o administrador del curso.",
    responses=error_responses(403),
)
async def update_course(courseId: UUID, body: UpdateCourseRequest):
    return stubs.course_response()


@router.delete(
    "/courses/{courseId}",
    status_code=204,
    response_class=Response,
    summary="Elimina un curso",
    description="Requiere permisos de propietario o administrador del curso.",
    responses=error_responses(403),
)
async def delete_course(courseId: UUID):
    return no_content()


@router.post(
    "/courses/{courseId}/leave",
    status_code=204,
    response_class=Response,
    summary="Abandona un curso",
    operation_id="leaveCourse",
)
async def leave_course(courseId: UUID):
    return no_content()


@router.get(
    "/courses/{courseId}/members",
    response_model=ListEnvelope[Member],
    summary="Lista los miembros de un curso",
)
async def list_members(courseId: UUID):
    return stubs.member_list()


@router.patch(
    "/courses/{courseId}/members/{userId}",
    response_model=Envelope[Member],
    summary="Configura permisos de un miembro",
    responses=error_responses(403),
)
async def update_member(courseId: UUID, userId: UUID, body: UpdateMemberRequest):
    return stubs.member_response()


@router.post(
    "/courses/{courseId}/invitations",
    status_code=201,
    response_model=Envelope[Invitation],
    summary="Invita a un usuario a un curso",
    responses=error_responses(403),
)
async def invite_user(courseId: UUID, body: InviteRequest):
    return stubs.invitation_response()


@router.get(
    "/invitations",
    response_model=ListEnvelope[Invitation],
    summary="Lista las invitaciones pendientes del usuario actual",
)
async def list_invitations():
    return stubs.invitation_list()


@router.patch(
    "/invitations/{invitationId}",
    response_model=Envelope[Invitation],
    summary="Acepta o rechaza una invitación",
)
async def decide_invitation(invitationId: UUID, body: InvitationDecisionRequest):
    return stubs.invitation_response()
