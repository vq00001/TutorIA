"""Consulta de materiales generados para el usuario autenticado."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel

from ..db.courses import get_member_course
from ..db.generated_materials import get_generated_material, list_generated_materials
from .dependencies import AuthenticatedConnection, ErrorResponse, error_response

router = APIRouter(prefix="/courses/{courseId}/materials", tags=["Learning"])
_ERRORS = {code: {"model": ErrorResponse} for code in (401, 404, 422)}


class MaterialSummary(BaseModel):
    id: UUID
    courseId: UUID
    generationId: UUID
    title: str
    type: Literal["summary", "flashcards", "explanation"]
    status: Literal["active", "invalidated", "redacted"]
    schemaVersion: int
    createdAt: datetime


class Material(MaterialSummary):
    content: dict[str, Any]


class MaterialCursor(BaseModel):
    beforeCreatedAt: datetime
    beforeId: UUID


class MaterialListResponse(BaseModel):
    data: list[MaterialSummary]
    nextCursor: MaterialCursor | None


class MaterialResponse(BaseModel):
    data: Material


def _summary(course_id: UUID, row) -> MaterialSummary:
    return MaterialSummary(
        id=row.id, courseId=course_id, generationId=row.generation_id,
        title=row.title, type=row.kind, status=row.status, schemaVersion=row.schema_version,
        createdAt=row.created_at,
    )


@router.get("", response_model=MaterialListResponse, responses=_ERRORS)
def materials_index(
    courseId: UUID, connection: AuthenticatedConnection,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    beforeCreatedAt: AwareDatetime | None = None, beforeId: UUID | None = None,
) -> MaterialListResponse | JSONResponse:
    if (beforeCreatedAt is None) != (beforeId is None):
        return error_response(422, "INVALID_CURSOR", "Envía ambos campos del cursor.")
    if get_member_course(connection, courseId) is None:
        return error_response(404, "COURSE_NOT_FOUND", "Clase no encontrada.")
    before = (beforeCreatedAt, beforeId) if beforeCreatedAt is not None and beforeId is not None else None
    rows = list_generated_materials(connection, courseId, limit=limit, before=before, lookahead=True)
    page = rows[:limit]
    cursor = None
    if len(rows) > limit:
        last = page[-1]
        cursor = MaterialCursor(beforeCreatedAt=last.created_at, beforeId=last.id)
    return MaterialListResponse(data=[_summary(courseId, row) for row in page], nextCursor=cursor)


@router.get("/{materialId}", response_model=MaterialResponse, responses=_ERRORS)
def materials_detail(
    courseId: UUID, materialId: UUID, connection: AuthenticatedConnection,
) -> MaterialResponse | JSONResponse:
    row = get_generated_material(connection, courseId, materialId)
    if row is None:
        return error_response(404, "MATERIAL_NOT_FOUND", "Material no encontrado.")
    content = row.content if row.status == "active" else {}
    return MaterialResponse(data=Material(**_summary(courseId, row).model_dump(), content=content))
