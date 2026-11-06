"""Configuración común de Ollama, Qdrant y los parámetros del pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(
    dotenv_path=Path(__file__).resolve().parents[1] / ".env",
    override=False,
)


def _env_bool(name: str, default: bool = False) -> bool:
    """Convierte una variable booleana y rechaza valores no reconocidos."""
    value = os.getenv(name)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True

    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(f"{name} debe ser booleano (true/false), no {value!r}")


@dataclass
class Config:
    """Configuración compartida por todas las etapas del pipeline RAG.

    Los tamaños de tokens controlan el chunking; `collection` identifica las
    colecciones de Qdrant; los modelos y URLs apuntan a los servicios externos.
    """

    ollama_url: str
    qdrant_url: str
    model_embed: str
    model_llm: str
    collection: str = "document_chunks"
    child_target_tokens: int = 160
    parent_max_tokens: int = 1200
    min_section_tokens: int = 60
    overlap_ratio: float = 0.12
    token_estimate_ratio: float = 1.3
    use_context: bool = False
    limit_query: int = 4
    similarity_threshold: float = 0.5

    @classmethod
    def from_env(cls) -> Config:
        """Construye la configuración usando `.env` y valores predeterminados."""

        ollama_port = os.getenv("OLLAMA_PORT", "11434")
        qdrant_port = os.getenv("QDRANT_PORT", "6333")
        return cls(
            ollama_url=os.getenv(
                "OLLAMA_URL", f"http://localhost:{ollama_port}"
            ).rstrip("/"),
            qdrant_url=os.getenv(
                "QDRANT_URL", f"http://localhost:{qdrant_port}"
            ).rstrip("/"),
            model_embed=os.getenv("MODEL_EMBED", "all-minilm"),
            model_llm=os.getenv("MODEL_LLM", "qwen3:1.7b"),
            collection=os.getenv("QDRANT_COLLECTION", "document_chunks"),
            child_target_tokens=int(os.getenv("CHILD_TARGET_TOKENS", "160")),
            parent_max_tokens=int(os.getenv("PARENT_MAX_TOKENS", "1200")),
            min_section_tokens=int(os.getenv("MIN_SECTION_TOKENS", "60")),
            overlap_ratio=float(os.getenv("OVERLAP_RATIO", "0.12")),
            token_estimate_ratio=float(os.getenv("TOKEN_ESTIMATE_RATIO", "1.3")),
            use_context=_env_bool("USE_CONTEXT"),
            limit_query=int(os.getenv("LIMIT_QUERY", "4")),
            similarity_threshold=float(os.getenv("SIMILARITY_THRESHOLD", "0.5")),
        )

    def __post_init__(self) -> None:
        if not 0 <= self.overlap_ratio < 1:
            raise ValueError("overlap_ratio debe estar en [0, 1)")

        if self.token_estimate_ratio <= 0:
            raise ValueError("token_estimate_ratio debe ser mayor que cero")

        if self.child_target_tokens >= self.parent_max_tokens:
            raise ValueError("child_target_tokens debe ser menor que parent_max_tokens")

        if not 0 <= self.similarity_threshold <= 1:
            raise ValueError("similarity_threshold debe estar en [0, 1]")
