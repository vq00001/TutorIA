"""Generación de respuestas fundamentadas en documentos recuperados."""

from __future__ import annotations

import re
from typing import Any

import requests

from .config import Config


def generate_answer(
    config: Config, question: str, documents: list[dict[str, Any]]
) -> str:
    """Responde usando exclusivamente los documentos recuperados como contexto."""
    if not question.strip():
        raise ValueError("La pregunta no puede estar vacía")
    if not documents:
        return "No encontré documentos relevantes para responder la consulta."
    context = "\n\n".join(
        f"[{index}] {document.get('filename', document.get('doc_id', 'documento'))}\n"
        f"{document.get('text', '')}"
        for index, document in enumerate(documents, 1)
    )
    prompt = (
        "Responde la pregunta usando únicamente el contexto. Si el contexto no alcanza, "
        "indícalo claramente. Cita las fuentes como [1], [2].\n\n"
        f"Pregunta: {question}\n\nContexto:\n{context}"
    )
    response = requests.post(
        f"{config.ollama_url}/api/chat",
        json={
            "model": config.model_llm,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "num_predict": 500},
        },
        timeout=180,
    )
    response.raise_for_status()
    body = response.json()
    content = body.get("message", {}).get("content") if isinstance(body, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"Ollama retornó una respuesta inválida: {body}")
    return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
