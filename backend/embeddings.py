"""Cliente mínimo para generar embeddings mediante el AI gateway."""

from __future__ import annotations

from typing import Any

import requests

from .config import Config


def embed(config: Config, texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """Genera un vector por texto usando el alias de embeddings del gateway.

    Raises:
        ValueError: si hay texto vacío o Ollama devuelve una respuesta inválida.
        requests.HTTPError: si el servicio responde con un estado HTTP de error.
    """
    if not texts:
        return []
    if any(not text.strip() for text in texts):
        raise ValueError("No se pueden generar embeddings para texto vacío")
    vectors: list[list[float]] = []
    headers = {"Content-Type": "application/json"}
    if config.gateway_api_key:
        headers["Authorization"] = f"Bearer {config.gateway_api_key}"
    for start in range(0, len(texts), batch_size):
        response = requests.post(
            f"{config.gateway_url}/v1/embeddings",
            headers=headers,
            json={
                "model": "embeddings",
                "input": texts[start : start + batch_size],
            },
            timeout=300,
        )
        response.raise_for_status()
        body: Any = response.json()
        batch = body.get("data") if isinstance(body, dict) else None
        if not isinstance(batch, list):
            raise ValueError(f"El AI gateway retornó embeddings inválidos: {body}")
        ordered = sorted(
            batch,
            key=lambda item: item.get("index", -1)
            if isinstance(item, dict) else -1,
        )
        if not all(
            isinstance(item, dict) and isinstance(item.get("embedding"), list)
            for item in ordered
        ):
            raise ValueError(f"El AI gateway retornó embeddings inválidos: {body}")
        vectors.extend([[float(value) for value in item["embedding"]] for item in ordered])
    if len(vectors) != len(texts) or any(not vector for vector in vectors):
        raise ValueError("Ollama no retornó un embedding por cada texto")
    return vectors
