"""Hipótesis de repaso a partir de notas suministradas para una clase."""

from typing import Literal
from uuid import UUID

import requests
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from ..db.connection import set_user_context, transaction
from ..db.courses import get_member_course
from ..db.learning_catalog import list_concepts
from ..db.personal_graph import (
    create_personal_node, create_personal_recommendation,
    list_personal_nodes, list_personal_recommendations,
)
from ..db.sessions import get_session_user
from ..knowledge_graph.learning_gaps import infer_gaps, normalized
from .dependencies import AuthenticatedConnection, BearerToken, ErrorResponse, InvalidSession, error_response

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


class AcceptedNodeInput(GapModel):
    ref: str = Field(max_length=12, pattern=r"^N[1-9][0-9]*$")
    label: str = Field(min_length=1, max_length=255)
    description: str = Field(max_length=2000)

    @field_validator("label", "description")
    @classmethod
    def reject_null_bytes(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("INVALID_PERSONAL_NODE_TEXT")
        return value


class AcceptedRecommendationInput(GapModel):
    target_concept_id: UUID
    existing_concept_id: UUID | None = None
    new_node_ref: str | None = Field(default=None, max_length=12, pattern=r"^N[1-9][0-9]*$")
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def reject_null_bytes(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("INVALID_GAP_RECOMMENDATION_REASON")
        return value

    @model_validator(mode="after")
    def check_source(self):
        if (self.existing_concept_id is None) == (self.new_node_ref is None):
            raise ValueError("INVALID_GAP_RECOMMENDATION_SOURCE")
        if not self.reason.strip():
            raise ValueError("EMPTY_GAP_RECOMMENDATION_REASON")
        return self


class AcceptGapRequest(GapModel):
    new_nodes: list[AcceptedNodeInput] = Field(default_factory=list, max_length=20)
    recommendations: list[AcceptedRecommendationInput] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def check_references(self):
        refs = [node.ref for node in self.new_nodes]
        used = [item.new_node_ref for item in self.recommendations if item.new_node_ref]
        if len(set(refs)) != len(refs) or set(refs) != set(used):
            raise ValueError("INVALID_PERSONAL_NODE_REFS")
        labels = [normalized(node.label) for node in self.new_nodes]
        if any(not label for label in labels) or len(set(labels)) != len(labels):
            raise ValueError("DUPLICATE_OR_EMPTY_PERSONAL_NODE")
        pairs = [(item.target_concept_id, item.existing_concept_id or item.new_node_ref)
                 for item in self.recommendations]
        if len(set(pairs)) != len(pairs):
            raise ValueError("DUPLICATE_GAP_RECOMMENDATION")
        return self


class AcceptedGapResult(GapModel):
    personal_node_ids_by_ref: dict[str, UUID]
    recommendation_ids: list[UUID]


class AcceptedGapResponse(GapModel):
    data: AcceptedGapResult


class SavedPersonalNode(GapModel):
    id: UUID
    label: str
    description: str


class SavedRecommendation(GapModel):
    id: UUID
    target_concept_id: UUID
    existing_concept_id: UUID | None
    personal_node_id: UUID | None
    reason: str


class SavedGapResult(GapModel):
    nodes: list[SavedPersonalNode]
    recommendations: list[SavedRecommendation]


class SavedGapResponse(GapModel):
    data: SavedGapResult


def _student_course(connection, course_id: UUID):
    course = get_member_course(connection, course_id)
    if course is None:
        return error_response(404, "COURSE_NOT_FOUND", "Clase no encontrada.")
    if course.status != "active" or course.my_role != "student":
        return error_response(403, "PERSONAL_GRAPH_FORBIDDEN", "La clase no admite esta operación.")
    return None


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


@router.post(
    "/knowledge-graph/learning-gaps/accept",
    status_code=201,
    response_model=AcceptedGapResponse,
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422)},
)
def accept_learning_gaps(courseId: UUID, payload: AcceptGapRequest, connection: AuthenticatedConnection):
    denied = _student_course(connection, courseId)
    if denied is not None:
        return denied
    concepts = list_concepts(connection, courseId)
    known = {concept.id for concept in concepts}
    course_labels = {normalized(concept.label) for concept in concepts}
    if any(normalized(node.label) in course_labels for node in payload.new_nodes):
        return error_response(422, "PERSONAL_NODE_ALREADY_IN_COURSE", "El tema ya existe en la clase.")
    if any(item.target_concept_id not in known
           or (item.existing_concept_id is not None and
               (item.existing_concept_id not in known
                or item.existing_concept_id == item.target_concept_id))
           for item in payload.recommendations):
        return error_response(422, "INVALID_GAP_CONCEPT", "La propuesta contiene conceptos inválidos.")

    user_id = connection.scalar(select(func.app.course_actor()))
    try:
        with connection.begin_nested():
            node_ids = {
                node.ref: create_personal_node(
                    connection, courseId, user_id, label=node.label.strip(),
                    description=node.description.strip(),
                ) for node in payload.new_nodes
            }
            recommendation_ids = [create_personal_recommendation(
                connection, courseId, user_id,
                target_concept_id=item.target_concept_id,
                existing_concept_id=item.existing_concept_id,
                personal_node_id=node_ids.get(item.new_node_ref),
                reason=item.reason.strip(),
            ) for item in payload.recommendations]
    except DBAPIError as exc:
        code = getattr(exc.orig, "sqlstate", None)
        if code == "42501":
            return error_response(403, "PERSONAL_GRAPH_FORBIDDEN", "La clase no admite esta operación.")
        if code == "23505":
            return error_response(409, "PERSONAL_GRAPH_CONFLICT", "El refuerzo ya está registrado.")
        if code in ("23503", "23514", "22001"):
            return error_response(422, "INVALID_PERSONAL_GRAPH", "Revisa los refuerzos aceptados.")
        raise
    return AcceptedGapResponse(data=AcceptedGapResult(
        personal_node_ids_by_ref=node_ids, recommendation_ids=recommendation_ids,
    ))


@router.get(
    "/knowledge-graph/learning-gaps/accepted",
    response_model=SavedGapResponse,
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404)},
)
def accepted_learning_gaps(courseId: UUID, connection: AuthenticatedConnection):
    denied = _student_course(connection, courseId)
    if denied is not None:
        return denied
    return SavedGapResponse(data=SavedGapResult(
        nodes=[SavedPersonalNode(id=row.id, label=row.label, description=row.description)
               for row in list_personal_nodes(connection, courseId)],
        recommendations=[SavedRecommendation(
            id=row.id, target_concept_id=row.target_concept_id,
            existing_concept_id=row.existing_concept_id,
            personal_node_id=row.personal_node_id, reason=row.reason,
        )
                         for row in list_personal_recommendations(connection, courseId)],
    ))
