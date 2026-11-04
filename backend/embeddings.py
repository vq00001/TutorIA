"""Cliente mínimo para generar embeddings mediante Ollama."""

from __future__ import annotations

from typing import Any

import requests

from .config import Config


def embed(config: Config, texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """Genera un vector por texto usando Ollama, procesando la entrada por lotes.

    Raises:
        ValueError: si hay texto vacío o Ollama devuelve una respuesta inválida.
        requests.HTTPError: si el servicio responde con un estado HTTP de error.
    """
    if not texts:
        return []
    if any(not text.strip() for text in texts):
        raise ValueError("No se pueden generar embeddings para texto vacío")
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        response = requests.post(
            f"{config.ollama_url}/api/embed",
            json={
                "model": config.model_embed,
                "input": texts[start : start + batch_size],
            },
            timeout=300,
        )
        response.raise_for_status()
        body: Any = response.json()
        batch = body.get("embeddings") if isinstance(body, dict) else None
        if not isinstance(batch, list) or not all(
            isinstance(vector, list) for vector in batch
        ):
            raise ValueError(f"Ollama retornó embeddings inválidos: {body}")
        vectors.extend([[float(value) for value in vector] for vector in batch])
    if len(vectors) != len(texts) or any(not vector for vector in vectors):
        raise ValueError("Ollama no retornó un embedding por cada texto")
    return vectors
