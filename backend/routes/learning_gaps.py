"""Hipótesis de repaso a partir de notas suministradas para una clase."""

from typing import Literal
from uuid import UUID

import requests
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from ..db.connection import set_user_context, transaction
from ..db.courses import get_member_course
from ..db.learning_catalog import list_concepts
from ..db.sessions import get_session_user
from ..knowledge_graph.learning_gaps import infer_gaps
from .dependencies import BearerToken, ErrorResponse, InvalidSession, error_response

router = APIRouter(prefix="/courses/{courseId}", tags=["Learning"])
_MAX_TEXT_CHARS = 200_000


class GapModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class GapScore(GapModel):
    concept_id: UUID
    correct: int = Field(ge=0)
    total: int = Field(ge=1)

    @model_validator(mode="after")
    def check_correct(self):
        if self.correct > self.total:
            raise ValueError("INVALID_GAP_SCORE")
        return self


class GapRequest(GapModel):
    results: list[GapScore] = Field(min_length=1, max_length=10)
    threshold: float = Field(default=0.4, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def check_unique_results(self):
        ids = [score.concept_id for score in self.results]
        if len(set(ids)) != len(ids):
            raise ValueError("DUPLICATE_GAP_SCORE_CONCEPT")
        return self


class WeakConcept(GapModel):
    concept_id: UUID
    correct: int
    total: int


class NewGapNode(GapModel):
    ref: str
    label: str
    description: str
    status: Literal["hypothesis"]


class GapRecommendation(GapModel):
    target_concept_id: UUID
    existing_concept_id: UUID | None
    new_node_ref: str | None
    kind: Literal["new", "existing"]
    reason: str
    status: Literal["hypothesis"]


class GapResult(GapModel):
    user_id: UUID
    course_id: UUID
    weak: list[WeakConcept]
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
        concepts = list_concepts(connection, courseId)

    if not concepts or len(concepts) > 400 or sum(
        len(concept.label) + len(concept.description) for concept in concepts
    ) > _MAX_TEXT_CHARS:
        return error_response(422, "INVALID_COURSE_GRAPH", "El grafo de la clase no admite esta operación.")

    refs_by_id = {concept.id: f"C{index}" for index, concept in enumerate(concepts, 1)}
    if any(score.concept_id not in refs_by_id for score in payload.results):
        return error_response(422, "UNKNOWN_GAP_CONCEPT", "Hay resultados de conceptos ajenos a la clase.")
    ids_by_ref = {ref: identifier for identifier, ref in refs_by_id.items()}
    nodes = [{"label": concept.label, "description": concept.description} for concept in concepts]
    scores = {
        refs_by_id[score.concept_id]: {"correct": score.correct, "total": score.total}
        for score in payload.results
    }
    try:
        inferred = infer_gaps(nodes, scores, payload.threshold)
        recommendations = [GapRecommendation(
            target_concept_id=ids_by_ref[item["target_ref"]],
            existing_concept_id=(ids_by_ref[item["node_ref"]] if item["kind"] == "existing" else None),
            new_node_ref=(item["node_ref"] if item["kind"] == "new" else None),
            kind=item["kind"], reason=item["reason"], status=item["status"],
        ) for item in inferred["recommendations"]]
        return GapResponse(data=GapResult(
            user_id=user_id,
            course_id=courseId,
            weak=[WeakConcept(concept_id=ids_by_ref[ref], **score)
                  for ref, score in inferred["weak"].items()],
            new_nodes=[NewGapNode.model_validate(node) for node in inferred["new_nodes"]],
            recommendations=recommendations,
            discarded=inferred["discarded"],
        ))
    except requests.exceptions.Timeout:
        return error_response(504, "GAP_PREVIEW_TIMEOUT", "El modelo tardó demasiado en responder.")
    except requests.exceptions.ConnectionError:
        return error_response(503, "GAP_PREVIEW_MODEL_UNAVAILABLE", "El modelo no está disponible.")
    except requests.exceptions.RequestException:
        return error_response(502, "GAP_PREVIEW_MODEL_ERROR", "El modelo devolvió un error.")
    except (ValueError, KeyError, IndexError):
        return error_response(502, "GAP_PREVIEW_INVALID_MODEL_RESPONSE", "El modelo devolvió una respuesta inválida.")
