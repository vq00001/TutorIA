"""Previsualización del grafo a partir de textos enviados por coordinación."""

from time import monotonic
from uuid import UUID

import requests
from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from ...core.config import Config
from ...db.connection import set_user_context, transaction
from ...db.courses import get_member_course
from ...db.sessions import get_session_user
from ...knowledge_graph.preview import GraphPreview, TextSource, generate_graph_preview
from .dependencies import BearerToken, ErrorResponse, InvalidSession, error_response

router = APIRouter(prefix="/courses/{courseId}", tags=["Learning"])
_MAX_TEXT_CHARS = 200_000
_PREVIEW_TIMEOUT_SECONDS = 600


class PreviewRequest(BaseModel):
    sources: list[TextSource] = Field(min_length=1, max_length=10)
    targetConcepts: int | None = Field(default=None, ge=1)
    includePrerequisites: bool = False

    @model_validator(mode="after")
    def check_sources(self):
        if sum(len(source.text) for source in self.sources) > _MAX_TEXT_CHARS:
            raise ValueError("GRAPH_PREVIEW_TEXT_TOO_LARGE")
        if len({source.source_id for source in self.sources}) != len(self.sources):
            raise ValueError("DUPLICATE_GRAPH_SOURCE_ID")
        return self


class PreviewResponse(BaseModel):
    data: GraphPreview


@router.post(
    "/knowledge-graph/preview",
    response_model=PreviewResponse,
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 422, 502, 503, 504)},
)
def graph_preview(courseId: UUID, payload: PreviewRequest, token: BearerToken):
    with transaction() as connection:
        user_id = get_session_user(connection, token)
        if user_id is None:
            raise InvalidSession
        set_user_context(connection, user_id)
        course = get_member_course(connection, courseId)
        if course is None:
            return error_response(404, "COURSE_NOT_FOUND", "Clase no encontrada.")
        if course.my_role != "coordinator" or course.status != "active":
            return error_response(403, "GRAPH_PREVIEW_FORBIDDEN", "La clase no admite esta operación.")

    deadline = monotonic() + _PREVIEW_TIMEOUT_SECONDS
    try:
        result = generate_graph_preview(
            payload.sources,
            Config.from_env(),
            target_concepts=payload.targetConcepts,
            include_prerequisites=payload.includePrerequisites,
            remaining=lambda: max(0.001, deadline - monotonic()),
        )
    except requests.exceptions.Timeout:
        return error_response(504, "GRAPH_PREVIEW_TIMEOUT", "El modelo tardó demasiado en responder.")
    except requests.exceptions.ConnectionError:
        return error_response(503, "GRAPH_PREVIEW_MODEL_UNAVAILABLE", "El modelo no está disponible.")
    except requests.exceptions.RequestException:
        return error_response(502, "GRAPH_PREVIEW_MODEL_ERROR", "El modelo devolvió un error.")
    except (ValueError, KeyError, IndexError):
        return error_response(502, "GRAPH_PREVIEW_INVALID_MODEL_RESPONSE", "El modelo devolvió una respuesta inválida.")
    return PreviewResponse(data=result)
