from uuid import UUID

from app import stubs
from app.deps import error_responses, require_auth
from app.schemas import ListEnvelope, Notification
from fastapi import APIRouter, Depends

router = APIRouter(
    tags=["Notifications"],
    dependencies=[Depends(require_auth)],
    responses=error_responses(401, 422),
)


@router.get(
    "/courses/{courseId}/notifications",
    response_model=ListEnvelope[Notification],
    summary="Lista notificaciones de eventos del curso",
)
async def list_course_notifications(courseId: UUID):
    return stubs.notification_list()


@router.get(
    "/notifications",
    response_model=ListEnvelope[Notification],
    summary="Lista notificaciones del usuario actual",
)
async def list_notifications():
    return stubs.notification_list()
