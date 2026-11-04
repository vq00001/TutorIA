"""Adaptador de almacenamiento del experimento de recuperación."""

from __future__ import annotations

import uuid
from typing import Any

from qdrant_client import QdrantClient, models

from backend.config import Config
from backend.embeddings import embed

_config = Config.from_env()
COLLECTION = _config.collection
MODEL_EMBED = _config.model_embed
LIMIT_QUERY = _config.limit_query
UMBRAL_SIMILITUD = _config.similarity_threshold
qdrant = QdrantClient(url=_config.qdrant_url)


def _config_for_model(model: str) -> Config:
    return Config(
        ollama_url=_config.ollama_url,
        qdrant_url=_config.qdrant_url,
        model_embed=model,
        model_llm=_config.model_llm,
        collection=_config.collection,
    )


def _ensure_collection(vector_size: int, collection_name: str) -> None:
    if qdrant.collection_exists(collection_name):
        configured_size = qdrant.get_collection(
            collection_name
        ).config.params.vectors.size
        if configured_size != vector_size:
            raise ValueError(
                f"La colección {collection_name!r} espera vectores de tamaño "
                f"{configured_size}, pero se recibió tamaño {vector_size}"
            )
        return
    qdrant.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(
            size=vector_size, distance=models.Distance.COSINE
        ),
    )


def save_chunk(
    document_id: str,
    filename: str,
    page_start: int,
    page_end: int,
    chunk_index: int,
    text: str,
    collection_name: str = COLLECTION,
    model: str = MODEL_EMBED,
) -> None:
    """Guarda un chunk individual para el benchmark histórico de embeddings."""
    if not document_id.strip() or not filename.strip() or not text.strip():
        raise ValueError("document_id, filename y text no pueden estar vacíos")
    if page_start < 0 or page_end < page_start or chunk_index < 0:
        raise ValueError("Los rangos de página y el índice deben ser válidos")
    vector = embed(_config_for_model(model), [text])[0]
    _ensure_collection(len(vector), collection_name)
    point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document_id}:{chunk_index}"))
    qdrant.upsert(
        collection_name=collection_name,
        points=[
            models.PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "document_id": document_id,
                    "filename": filename,
                    "page_start": page_start,
                    "page_end": page_end,
                    "chunk_index": chunk_index,
                    "text": text,
                },
            )
        ],
    )


def retrieve(
    question: str,
    limit: int = LIMIT_QUERY,
    score_threshold: float = UMBRAL_SIMILITUD,
    collection_name: str = COLLECTION,
    model: str = MODEL_EMBED,
) -> list[dict[str, Any]]:
    """Recupera chunks del benchmark aplicando límite y threshold de similitud."""
    if not question.strip():
        raise ValueError("La pregunta no puede estar vacía")
    if limit <= 0:
        raise ValueError("El límite debe ser mayor que cero")
    vector = embed(_config_for_model(model), [question])[0]
    _ensure_collection(len(vector), collection_name)
    points = qdrant.query_points(
        collection_name=collection_name,
        query=vector,
        limit=limit,
        score_threshold=score_threshold,
        with_payload=True,
    ).points
    return [
        {
            "text": (point.payload or {}).get("text", ""),
            "filename": (point.payload or {}).get("filename", ""),
            "page_start": (point.payload or {}).get("page_start"),
            "page_end": (point.payload or {}).get("page_end"),
            "score": point.score,
        }
        for point in points
    ]
