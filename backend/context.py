"""Generación opcional de contexto semántico para mejorar embeddings."""

from __future__ import annotations

import re
import sys

import requests

from .config import Config


def generate_chunk_context(config: Config, breadcrumb: str, text: str) -> str:
    """Genera una frase de contexto opcional para mejorar el embedding del parent."""
    prompt = (
        "Escribe UNA sola frase corta (máximo 25 palabras) que ubique el fragmento "
        "dentro de su documento. Responde solo con la frase y en su mismo idioma.\n\n"
        f"Ubicación: {breadcrumb}\n\nFragmento:\n{text[:1500]}"
    )
    try:
        response = requests.post(
            f"{config.ollama_url}/api/chat",
            json={
                "model": config.model_llm,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "num_predict": 80},
            },
            timeout=180,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        return (
            re.sub(r"<think>.*?</think>", "", content, flags=re.S)
            .replace("\n", " ")
            .strip()
        )
    except (requests.RequestException, KeyError, TypeError, ValueError) as error:
        print(f"  [aviso] no se pudo generar contexto: {error}", file=sys.stderr)
        return ""
