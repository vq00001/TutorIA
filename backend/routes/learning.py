"""Catálogo, grafo, progreso y planificación del aprendizaje."""

from datetime import date, datetime
from typing import Callable, Generic, Literal, TypeVar
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel
from sqlalchemy.exc import DBAPIError

from ..db.courses import get_member_course
from ..db.learning_catalog import (
    add_concept_alias, add_prerequisite, create_concept, has_concept, list_concept_aliases,
    list_concepts, list_course_edges,
)
from ..db.learning_plans import (
    build_roadmap, create_learning_goal, get_roadmap, has_learning_goal, list_learning_goals,
    list_roadmap_steps,
    list_roadmaps, set_learning_goal_status,
)
from ..db.learning_progress import list_concept_mastery
from .dependencies import AuthenticatedConnection, ErrorResponse, error_response

router = APIRouter(prefix="/courses/{courseId}", tags=["Learning"])
_ERRORS = {code: {"model": ErrorResponse} for code in (401, 404, 422)}
_WRITE_ERRORS = {**_ERRORS, **{code: {"model": ErrorResponse} for code in (403, 409)}}
T = TypeVar("T")


class Result(BaseModel, Generic[T]):
    data: T


class LearningRow(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Identifier(BaseModel):
    id: UUID


class Concept(LearningRow):
    id: UUID
    label: str
    description: str


class Alias(LearningRow):
    alias: str
    normalized_alias: str


class GraphNode(BaseModel):
    key: UUID
    attributes: Concept


class GraphEdge(BaseModel):
    key: UUID
    source: UUID
    target: UUID


class Graph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class Mastery(LearningRow):
    concept_id: UUID
    label: str
    evidence_count: int
    score: float
    level: str


class Goal(LearningRow):
    id: UUID
    title: str
    status: str
    target_date: date | None
    concept_ids: list[UUID]
    created_at: datetime


class Roadmap(LearningRow):
    id: UUID
    version_no: int
    graph_revision: int
    algorithm_version: str
    created_at: datetime


class RoadmapStep(LearningRow):
    concept_id: UUID
    position: int
    label: str
    mastery_level: str


class RoadmapDetail(Roadmap):
    steps: list[RoadmapStep]


class LearningInput(BaseModel):
    @field_validator("*")
    @classmethod
    def check_text(cls, value):
        if isinstance(value, str) and "\x00" in value:
            raise ValueError("Texto inválido")
        return value


class ConceptInput(LearningInput):
    label: str = Field(min_length=1, max_length=255)
    description: str = ""


class AliasInput(LearningInput):
    alias: str = Field(min_length=1, max_length=255)


class PrerequisiteInput(BaseModel):
    prerequisiteId: UUID


class GoalInput(LearningInput):
    title: str = Field(min_length=1, max_length=255)
    conceptIds: list[UUID] = Field(min_length=1, max_length=100)
    targetDate: date | None = None


class GoalStatusInput(BaseModel):
    status: Literal["completed", "archived"]


def _missing() -> JSONResponse:
    return error_response(404, "LEARNING_RESOURCE_NOT_FOUND", "Recurso de aprendizaje no encontrado.")


def _write(connection, operation: Callable, *args, **kwargs):
    try:
        with connection.begin_nested():
            return operation(connection, *args, **kwargs)
    except DBAPIError as exc:
        code = getattr(exc.orig, "sqlstate", None)
        primary = getattr(getattr(exc.orig, "diag", None), "message_primary", None)
        if code == "42501":
            return error_response(403, "LEARNING_FORBIDDEN", "La cuenta no puede realizar la operación.")
        if code == "23505" or primary in ("PREREQUISITE_CYCLE", "GOAL_INACTIVE", "INVALID_GOAL_TRANSITION"):
            return error_response(409, "LEARNING_CONFLICT", "La operación entra en conflicto con el estado actual.")
        if code in ("23514", "23503", "22001"):
            return error_response(422, "INVALID_LEARNING_DATA", "Revisa los datos de aprendizaje.")
        raise


def _identifier(result):
    return result if isinstance(result, JSONResponse) else Result(data=Identifier(id=result))


@router.get("/knowledge-graph", response_model=Result[Graph], responses=_ERRORS)
def knowledge_graph(courseId: UUID, connection: AuthenticatedConnection):
    if get_member_course(connection, courseId) is None:
        return _missing()
    rows = list_concepts(connection, courseId)
    nodes = [GraphNode(key=row.id, attributes=Concept.model_validate(dict(row))) for row in rows]
    edges = [
        GraphEdge(key=edge.id, source=edge.source_id, target=edge.target_id)
        for edge in list_course_edges(connection, courseId)
    ]
    return Result(data=Graph(nodes=nodes, edges=edges))


@router.get("/progress", response_model=Result[list[Mastery]], responses=_ERRORS)
def progress(courseId: UUID, connection: AuthenticatedConnection):
    if get_member_course(connection, courseId) is None:
        return _missing()
    return Result(data=[
        Mastery.model_validate(dict(row)) for row in list_concept_mastery(connection, courseId)
    ])


@router.post("/concepts", status_code=201, response_model=Result[Identifier], responses=_WRITE_ERRORS)
def concepts_create(courseId: UUID, payload: ConceptInput, connection: AuthenticatedConnection):
    if get_member_course(connection, courseId) is None:
        return _missing()
    return _identifier(_write(connection, create_concept, courseId, **payload.model_dump()))


@router.get("/concepts/{conceptId}/aliases", response_model=Result[list[Alias]], responses=_ERRORS)
def concept_aliases(courseId: UUID, conceptId: UUID, connection: AuthenticatedConnection):
    if not has_concept(connection, courseId, conceptId):
        return _missing()
    return Result(data=[
        Alias.model_validate(dict(row)) for row in list_concept_aliases(connection, courseId, conceptId)
    ])


@router.post("/concepts/{conceptId}/aliases", status_code=201,
             response_model=Result[Alias], responses=_WRITE_ERRORS)
def concept_alias_create(
    courseId: UUID, conceptId: UUID, payload: AliasInput, connection: AuthenticatedConnection
):
    if not has_concept(connection, courseId, conceptId):
        return _missing()
    result = _write(connection, add_concept_alias, courseId, conceptId, payload.alias)
    if isinstance(result, JSONResponse):
        return result
    return Result(data=Alias(alias=payload.alias, normalized_alias=result))


@router.post("/concepts/{conceptId}/prerequisites", status_code=201,
             response_model=Result[Identifier], responses=_WRITE_ERRORS)
def prerequisite_create(
    courseId: UUID, conceptId: UUID, payload: PrerequisiteInput, connection: AuthenticatedConnection
):
    if not has_concept(connection, courseId, conceptId):
        return _missing()
    return _identifier(_write(connection, add_prerequisite, courseId, payload.prerequisiteId, conceptId))


@router.get("/learning-goals", response_model=Result[list[Goal]], responses=_ERRORS)
def goals_index(courseId: UUID, connection: AuthenticatedConnection):
    if get_member_course(connection, courseId) is None:
        return _missing()
    return Result(data=[Goal.model_validate(dict(row)) for row in list_learning_goals(connection, courseId)])


@router.post("/learning-goals", status_code=201, response_model=Result[Identifier], responses=_WRITE_ERRORS)
def goals_create(courseId: UUID, payload: GoalInput, connection: AuthenticatedConnection):
    if get_member_course(connection, courseId) is None:
        return _missing()
    return _identifier(_write(
        connection, create_learning_goal, courseId, title=payload.title,
        concept_ids=payload.conceptIds, target_date=payload.targetDate,
    ))


@router.patch("/learning-goals/{goalId}", response_model=Result[Identifier], responses=_WRITE_ERRORS)
def goals_status(
    courseId: UUID, goalId: UUID, payload: GoalStatusInput, connection: AuthenticatedConnection
):
    if not has_learning_goal(connection, courseId, goalId):
        return _missing()
    return _identifier(_write(connection, set_learning_goal_status, courseId, goalId, payload.status))


@router.get("/learning-goals/{goalId}/roadmaps", response_model=Result[list[Roadmap]], responses=_ERRORS)
def roadmaps_index(courseId: UUID, goalId: UUID, connection: AuthenticatedConnection):
    if not has_learning_goal(connection, courseId, goalId):
        return _missing()
    return Result(data=[Roadmap.model_validate(dict(row)) for row in list_roadmaps(connection, courseId, goalId)])


@router.post("/learning-goals/{goalId}/roadmaps", response_model=Result[Identifier], responses=_WRITE_ERRORS)
def roadmaps_build(courseId: UUID, goalId: UUID, connection: AuthenticatedConnection):
    if not has_learning_goal(connection, courseId, goalId):
        return _missing()
    return _identifier(_write(connection, build_roadmap, courseId, goalId))


@router.get("/learning-goals/{goalId}/roadmaps/{roadmapId}",
            response_model=Result[RoadmapDetail], responses=_ERRORS)
def roadmaps_detail(courseId: UUID, goalId: UUID, roadmapId: UUID, connection: AuthenticatedConnection):
    row = get_roadmap(connection, courseId, goalId, roadmapId)
    if row is None:
        return _missing()
    steps = [RoadmapStep.model_validate(dict(step)) for step in list_roadmap_steps(connection, courseId, roadmapId)]
    return Result(data=RoadmapDetail(**Roadmap.model_validate(dict(row)).model_dump(), steps=steps))
