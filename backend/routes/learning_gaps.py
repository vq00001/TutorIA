"""Hipótesis de repaso a partir de notas suministradas para una clase."""

from typing import Literal
from uuid import UUID

import requests
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from ..db.connection import set_user_context, transaction
from ..db.courses import get_member_course
from ..db.sessions import get_session_user
from ..knowledge_graph.learning_gaps import infer_gaps, normalized
from .dependencies import BearerToken, ErrorResponse, InvalidSession, error_response

router = APIRouter(prefix="/courses/{courseId}", tags=["Learning"])
_MAX_TEXT_CHARS = 200_000


class GapModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class GapNode(GapModel):
    label: str = Field(min_length=1, max_length=255)
    description: str = Field(max_length=2000)


class GapScore(GapModel):
    ref: str = Field(max_length=12, pattern=r"^C[1-9][0-9]*$")
    correct: int = Field(ge=0)
    total: int = Field(ge=1)

    @model_validator(mode="after")
    def check_correct(self):
        if self.correct > self.total:
            raise ValueError("INVALID_GAP_SCORE")
        return self


class GapRequest(GapModel):
    nodes: list[GapNode] = Field(min_length=1, max_length=400)
    results: list[GapScore] = Field(min_length=1, max_length=10)
    threshold: float = Field(default=0.4, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def check_references(self):
        if sum(len(node.label) + len(node.description) for node in self.nodes) > _MAX_TEXT_CHARS:
            raise ValueError("GAP_INPUT_TOO_LARGE")
        labels = [normalized(node.label) for node in self.nodes]
        if any(not label for label in labels) or len(set(labels)) != len(labels):
            raise ValueError("DUPLICATE_OR_EMPTY_GAP_NODE")
        refs = [score.ref for score in self.results]
        if len(set(refs)) != len(refs) or any(int(ref[1:]) > len(self.nodes) for ref in refs):
            raise ValueError("INVALID_GAP_SCORE_REF")
        return self


class GapScoreValue(GapModel):
    correct: int
    total: int


class NewGapNode(GapModel):
    ref: str
    label: str
    description: str
    status: Literal["hypothesis"]


class GapRecommendation(GapModel):
    target_ref: str
    node_ref: str
    kind: Literal["new", "existing"]
    reason: str
    status: Literal["hypothesis"]


class GapResult(GapModel):
    weak: dict[str, GapScoreValue]
    new_nodes: list[NewGapNode]
    recommendations: list[GapRecommendation]
    discarded: int


class GapResponse(GapModel):
    data: GapResult


@router.post(
    "/knowledge-graph/learning-gaps/preview",
    response_model=GapResponse,
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 422, 502, 503, 504)},
)
def learning_gaps_preview(courseId: UUID, payload: GapRequest, token: BearerToken):
    with transaction() as connection:
        user_id = get_session_user(connection, token)
        if user_id is None:
            raise InvalidSession
        set_user_context(connection, user_id)
        course = get_member_course(connection, courseId)
        if course is None:
            return error_response(404, "COURSE_NOT_FOUND", "Clase no encontrada.")
        if course.status != "active":
            return error_response(403, "GAP_PREVIEW_FORBIDDEN", "La clase no admite esta operación.")

    nodes = [node.model_dump() for node in payload.nodes]
    scores = {
        score.ref: {"correct": score.correct, "total": score.total}
        for score in payload.results
    }
    try:
        return GapResponse(data=GapResult.model_validate(infer_gaps(nodes, scores, payload.threshold)))
    except requests.exceptions.Timeout:
        return error_response(504, "GAP_PREVIEW_TIMEOUT", "El modelo tardó demasiado en responder.")
    except requests.exceptions.ConnectionError:
        return error_response(503, "GAP_PREVIEW_MODEL_UNAVAILABLE", "El modelo no está disponible.")
    except requests.exceptions.RequestException:
        return error_response(502, "GAP_PREVIEW_MODEL_ERROR", "El modelo devolvió un error.")
    except (ValueError, KeyError, IndexError):
        return error_response(502, "GAP_PREVIEW_INVALID_MODEL_RESPONSE", "El modelo devolvió una respuesta inválida.")
