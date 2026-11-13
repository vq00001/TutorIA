from app import stubs
from app.deps import ActivityTypeFilter, Page, PageSize, error_responses, require_auth
from app.schemas import Activity, PagedEnvelope
from fastapi import APIRouter, Depends

router = APIRouter(
    tags=["Activity"],
    dependencies=[Depends(require_auth)],
    responses=error_responses(401, 422),
)


@router.get(
    "/activity",
    response_model=PagedEnvelope[Activity],
    summary="Lista el historial de actividad del usuario",
)
async def list_activity(
    page: Page = 1,
    pageSize: PageSize = 20,
    activity_type: ActivityTypeFilter = None,
):
    return stubs.activity_list()
