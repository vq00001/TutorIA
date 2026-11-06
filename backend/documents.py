"""Lectura y descubrimiento de documentos que pueden ingresar al sistema."""

from __future__ import annotations

from pathlib import Path

SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt"}


def iter_documents(path: Path) -> list[Path]:
    """Devuelve archivos soportados desde una ruta de archivo o directorio."""
    if path.is_file():
        return [path] if path.suffix.lower() in SUPPORTED_EXTENSIONS else []
    return sorted(
        file
        for file in path.rglob("*")
        if file.is_file() and file.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def read_document(path: Path) -> str:
    """Valida UTF-8 estricto; los originales no se alteran."""
    from .services.documents import validate_file
    return validate_file(path.name,path.read_bytes())[2]


def document_id(path: Path, root: Path | None = None) -> str:
    """Genera un ID estable: relativo a `root` o basado en el nombre del archivo."""
    return str(path.relative_to(root)) if root else path.name
