"""Preparación de documentos para el flujo de ingestión."""

from pathlib import Path

from ..core.config import Config
from ..core.models import Child, Parent
from .chunking import chunk_text
from .documents import document_id, read_document


def prepare_document(
    config: Config, file: Path, root: Path | None = None
) -> tuple[list[Parent], list[Child]]:
    """Lee y divide un documento."""
    return chunk_text(read_document(file), document_id(file, root), config)
