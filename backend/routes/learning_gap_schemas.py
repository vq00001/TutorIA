"""Modelos de entrada y salida para las rutas de refuerzos personales."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from ..knowledge_graph.learning_gaps import normalized


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


class PersonalizedGraphNode(GapModel):
    key: UUID
    attributes: SavedPersonalNode
    origin: Literal["course", "personal"]


class PersonalizedGraphEdge(GapModel):
    key: UUID
    source: UUID
    target: UUID


class PersonalizedGraphRecommendation(GapModel):
    id: UUID
    source: UUID
    target: UUID
    kind: Literal["existing", "new"]
    reason: str


class PersonalizedGraph(GapModel):
    nodes: list[PersonalizedGraphNode]
    edges: list[PersonalizedGraphEdge]
    recommendations: list[PersonalizedGraphRecommendation]


class PersonalizedGraphResponse(GapModel):
    data: PersonalizedGraph


