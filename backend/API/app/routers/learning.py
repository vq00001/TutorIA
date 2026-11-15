from uuid import UUID

from app import stubs
from app.deps import error_responses, require_auth
from app.schemas import (
    CourseProgress,
    DashboardSummaryData,
    Envelope,
    GenerateMaterial,
    KnowledgeGraphData,
    ListEnvelope,
)
from fastapi import APIRouter, Depends

router = APIRouter(
    tags=["Learning"],
    dependencies=[Depends(require_auth)],
    responses=error_responses(401, 422),
)


@router.get(
    "/dashboard/summary",
    response_model=Envelope[DashboardSummaryData],
    summary="Obtiene el resumen del panel del usuario",
)
async def dashboard_summary():
    return stubs.dashboard_summary()


@router.get(
    "/courses/{courseId}/progress",
    response_model=Envelope[CourseProgress],
    summary="Obtiene el progreso del curso",
)
async def get_progress(courseId: UUID):
    return stubs.progress_response()


@router.get(
    "/courses/{courseId}/knowledge-graph",
    response_model=Envelope[KnowledgeGraphData],
    summary="Obtiene el grafo de conocimiento del curso",
)
async def get_knowledge_graph(courseId: UUID):
    return stubs.knowledge_graph_response()


@router.get(
    "/courses/{courseId}/materials",
    response_model=ListEnvelope[GenerateMaterial],
    summary="Lista los materiales generados del curso",
)
async def list_materials(courseId: UUID):
    return stubs.materials_list()
