"""Generación opcional de contexto semántico para mejorar embeddings."""

from __future__ import annotations

import sys

import requests

from .config import Config
from .llm import chat_completion


def generate_chunk_context(config: Config, breadcrumb: str, text: str) -> str:
    """Genera una frase de contexto opcional para mejorar el embedding del parent."""
    prompt = (
        "Escribe UNA sola frase corta (máximo 25 palabras) que ubique el fragmento "
        "dentro de su documento. Responde solo con la frase y en su mismo idioma.\n\n"
        f"Ubicación: {breadcrumb}\n\nFragmento:\n{text[:1500]}"
    )
    try:
        content = chat_completion(config, prompt, temperature=0, max_tokens=80, timeout=180)
        return content.replace("\n", " ").strip()
    except (requests.RequestException, KeyError, TypeError, ValueError) as error:
        print(f"  [aviso] no se pudo generar contexto: {error}", file=sys.stderr)
        return ""
