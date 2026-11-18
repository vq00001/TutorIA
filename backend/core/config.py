"""Configuración de Ollama, LiteLLM y los parámetros del pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(
    dotenv_path=Path(__file__).resolve().parents[2] / ".env",
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

    Los tamaños de tokens controlan el chunking. Ollama genera embeddings y LiteLLM sirve el chat.
    """

    ollama_url: str
    model_embed: str
    model_llm: str
    child_target_tokens: int = 160
    parent_max_tokens: int = 1200
    min_section_tokens: int = 60
    overlap_ratio: float = 0.12
    token_estimate_ratio: float = 1.3
    use_context: bool = False
    limit_query: int = 4
    similarity_threshold: float = 0.5
    retrieval_limit: int = 4
    gateway_url: str = "http://localhost:4000"
    gateway_api_key: str = ""
    model_llm_revision: str = "unspecified"

    @classmethod
    def from_env(cls) -> Config:
        """Construye la configuración usando `.env` y valores predeterminados."""

        ollama_port = os.getenv("OLLAMA_PORT", "11434")
        litellm_port = os.getenv("LITELLM_PORT", "4000")
        return cls(
            ollama_url=os.getenv("OLLAMA_URL", f"http://localhost:{ollama_port}").rstrip("/"),
            model_embed=os.getenv("MODEL_EMBED", "all-minilm"),
            model_llm=os.getenv("MODEL_LLM", "chat-principal"),
            child_target_tokens=int(os.getenv("CHILD_TARGET_TOKENS", "160")),
            parent_max_tokens=int(os.getenv("PARENT_MAX_TOKENS", "1200")),
            min_section_tokens=int(os.getenv("MIN_SECTION_TOKENS", "60")),
            overlap_ratio=float(os.getenv("OVERLAP_RATIO", "0.12")),
            token_estimate_ratio=float(os.getenv("TOKEN_ESTIMATE_RATIO", "1.3")),
            use_context=_env_bool("USE_CONTEXT"),
            limit_query=int(os.getenv("LIMIT_QUERY", "4")),
            similarity_threshold=float(os.getenv("SIMILARITY_THRESHOLD", "0.3")),
            retrieval_limit=int(os.getenv("RETRIEVAL_LIMIT", os.getenv("LIMIT_QUERY", "4"))),
            gateway_url=os.getenv("AI_GATEWAY_URL", f"http://localhost:{litellm_port}").rstrip("/"),
            gateway_api_key=os.getenv("AI_GATEWAY_API_KEY", ""),
            model_llm_revision=os.getenv("MODEL_LLM_REVISION", "unspecified"),
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

        if not 1 <= self.retrieval_limit <= 12:
            raise ValueError("retrieval_limit debe estar entre 1 y 12")
