"""Cliente mínimo para el AI gateway (LiteLLM, API compatible con OpenAI)."""

from __future__ import annotations

import re
from typing import Any

import requests

from .config import Config


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
    return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
