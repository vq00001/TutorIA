"""Orquestación de ingestión y recuperación del pipeline RAG."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .chunking import chunk_text
from .config import Config
from .context import generate_chunk_context
from .documents import document_id, read_document
from .embeddings import embed
from .models import Child, Parent
from .vector_store import QdrantStore


def prepare_document(
    config: Config, file: Path, root: Path | None = None
) -> tuple[list[Parent], list[Child]]:
    """Lee y chunkea un archivo sin llamar todavía a Ollama ni Qdrant."""
    return chunk_text(read_document(file), document_id(file, root), config)


def ingest_document(
    config: Config, file: Path, root: Path | None = None
) -> tuple[int, int]:
    """Indexa un documento: lectura, chunking, embeddings y persistencia."""
    parents, children = prepare_document(config, file, root)
    if not children:
        return 0, 0
    if config.use_context:
        # El contexto se genera una sola vez por parent, no una vez por child.
        contexts = {
            parent.id: generate_chunk_context(
                config,
                " > ".join([Path(parent.doc_id).stem, *parent.path]),
                parent.text,
            )
            for parent in parents
        }
        for child in children:
            context = contexts[child.parent_id]
            child.embed_text = "\n".join(
                value for value in (child.embed_text, context) if value
            )
    vectors = embed(config, [child.embed_text for child in children])
    store = QdrantStore(config)
    store.ensure_collections(len(vectors[0]))
    store.save(parents, children, vectors)
    return len(parents), len(children)


def retrieve_documents(
    config: Config,
    question: str,
    limit: int | None = None,
    score_threshold: float | None = None,
) -> list[dict[str, Any]]:
    """Recupera parents relevantes para una pregunta mediante búsqueda semántica."""
    if not question.strip():
        raise ValueError("La pregunta no puede estar vacía")
    limit = limit or config.limit_query
    if limit <= 0:
        raise ValueError("El límite debe ser mayor que cero")
    vectors = embed(config, [question])
    store = QdrantStore(config)
    store.ensure_collections(len(vectors[0]))
    results = store.search(vectors[0], limit)
    threshold = (
        config.similarity_threshold if score_threshold is None else score_threshold
    )
    return [result for result in results if result["score"] >= threshold]
