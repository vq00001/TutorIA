"""Generate a graph proposal from complete texts, independent of their storage."""

from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from backend.rag.chunking import chunk_text, count_tokens
from backend.core.config import Config

from .concepts import extract_candidates
from .consolidation import consolidate_candidates
from .prerequisites import propose_prerequisites


class PreviewModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class TextSource(PreviewModel):
    source_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1)

    @field_validator("source_id", "title", "text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("El campo no puede estar vacío")
        return value


class PreviewInput(PreviewModel):
    source_id: str
    title: str
    fragment_count: int


class PreviewSource(PreviewModel):
    source_id: str
    title: str
    chunk_index: int
    section_path: list[str]


class PreviewNode(PreviewModel):
    ref: str
    label: str
    description: str
    sources: list[PreviewSource]


class PreviewEdge(PreviewModel):
    source_ref: str
    target_ref: str
    reason: str


class PreviewMerge(PreviewModel):
    from_labels: list[str]
    to_ref: str
    reason: str


class PreviewParameters(PreviewModel):
    target_concepts: int | None
    include_prerequisites: bool


class PreviewStages(PreviewModel):
    extraction: Literal["completed"] = "completed"
    consolidation: Literal["completed", "skipped"]
    prerequisites: Literal["completed", "skipped"]


class GraphPreview(PreviewModel):
    schema_version: int = 1
    model: str
    inputs: list[PreviewInput]
    parameters: PreviewParameters
    stages: PreviewStages
    nodes: list[PreviewNode]
    edges: list[PreviewEdge]
    merges: list[PreviewMerge]


def generate_graph_preview(
    sources: Sequence[TextSource],
    config: Config,
    *,
    target_concepts: int | None = None,
    include_prerequisites: bool = False,
    remaining: Callable[[], float] = lambda: 180,
) -> GraphPreview:
    """Read every text fragment and return a proposal without storing anything."""
    if not sources:
        raise ValueError("EMPTY_GRAPH_SOURCES")
    if target_concepts is not None and target_concepts < 1:
        raise ValueError("INVALID_TARGET_CONCEPT_COUNT")
    if any(not isinstance(source, TextSource) for source in sources):
        raise TypeError("sources must contain TextSource objects")
    if len({source.source_id for source in sources}) != len(sources):
        raise ValueError("DUPLICATE_GRAPH_SOURCE_ID")

    fragments = []
    source_details = {}
    input_details = []
    for source in sources:
        parents, _ = chunk_text(source.text, source.title, config)
        if not parents:
            raise ValueError("EMPTY_GRAPH_SOURCE_FRAGMENTS")
        input_details.append(PreviewInput(
            source_id=source.source_id, title=source.title, fragment_count=len(parents)
        ))
        for parent in parents:
            identity = (source.source_id, parent.index)
            source_details[identity] = PreviewSource(
                source_id=source.source_id,
                title=source.title,
                chunk_index=parent.index,
                section_path=list(parent.path),
            )
            fragments.append({
                "id": identity,
                "text": parent.text,
                "locator": {
                    "title": source.title,
                    "section": " > ".join(parent.path),
                    "index": parent.index,
                },
                "token_estimate": max(1, count_tokens(parent.text, config.token_estimate_ratio)),
            })

    original = extract_candidates(fragments, config.model_llm, remaining)
    candidates = original
    applied_merges = []
    if target_concepts is not None:
        candidates, applied_merges = consolidate_candidates(
            candidates, target_concepts, config.model_llm, remaining
        )
    proposals = (
        propose_prerequisites(candidates, config.model_llm, remaining)
        if include_prerequisites else []
    )

    refs_by_key = {key: f"N{index}" for index, key in enumerate(candidates, 1)}
    source_order = {identity: index for index, identity in enumerate(source_details)}
    nodes = [
        PreviewNode(
            ref=refs_by_key[key],
            label=candidate["label"],
            description=candidate["description"],
            sources=[source_details[identity] for identity in sorted(
                candidate["source_ids"], key=source_order.__getitem__
            )],
        )
        for key, candidate in candidates.items()
    ]
    edges = [
        PreviewEdge(
            source_ref=refs_by_key[item["source_key"]],
            target_ref=refs_by_key[item["target_key"]],
            reason=item["reason"],
        )
        for item in proposals
    ]
    merges = [
        PreviewMerge(
            from_labels=[original[key]["label"] for key in item["from_keys"]],
            to_ref=refs_by_key[item["to_key"]],
            reason=item["reason"],
        )
        for item in applied_merges
    ]
    return GraphPreview(
        model=config.model_llm,
        inputs=input_details,
        parameters=PreviewParameters(
            target_concepts=target_concepts,
            include_prerequisites=include_prerequisites,
        ),
        stages=PreviewStages(
            consolidation="completed" if target_concepts is not None else "skipped",
            prerequisites="completed" if include_prerequisites else "skipped",
        ),
        nodes=nodes,
        edges=edges,
        merges=merges,
    )
