"""Generación de respuestas fundamentadas en documentos recuperados."""

from __future__ import annotations

from typing import Any

from .config import Config

import requests
import re


def chat_completion(
    config: Config,
    prompt: str,
    *,
    temperature: float = 0,
    max_tokens: int = 500,
    timeout: int = 180,
) -> str:
    headers = {"Content-Type": "application/json"}
    if config.gateway_api_key:
        headers["Authorization"] = f"Bearer {config.gateway_api_key}"

    response = requests.post(
        f"{config.gateway_url}/v1/chat/completions",
        headers=headers,
        json={
            "model": config.model_llm,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    body: Any = response.json()
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        content = None
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"El AI gateway retornó una respuesta inválida: {body}")
    return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()


def generate_answer(config: Config, question: str, documents: list[dict[str, Any]]) -> str:
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
    return chat_completion(config, prompt, temperature=0, max_tokens=500, timeout=180)
