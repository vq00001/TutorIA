"""Preparación de documentos para el futuro flujo de ingestión."""

from pathlib import Path

from .chunking import chunk_text
from .config import Config
from .documents import document_id, read_document
from .models import Child, Parent


def prepare_document(
    config: Config, file: Path, root: Path | None = None
) -> tuple[list[Parent], list[Child]]:
    """Lee y divide un documento sin acceder a servicios externos."""
    return chunk_text(read_document(file), document_id(file, root), config)
