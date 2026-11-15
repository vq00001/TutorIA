"""Copia archivos subidos a una estructura legible por curso."""

import os
import re
import unicodedata
from pathlib import Path
from uuid import UUID

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FILES_DIR = Path(os.getenv("TUTORIA_FILES_DIR", PROJECT_ROOT / "archivos")).expanduser().resolve()


def _folder_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    return slug[:60] or "ramo"


def _safe_filename(value: str) -> str:
    name = unicodedata.normalize("NFC", value.replace("\\", "/").rsplit("/", 1)[-1])
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name or name in {".", ".."}:
        name = "archivo"
    return name[:180]


def store_uploaded_document(
    *,
    course_name: str,
    course_id: UUID,
    document_id: UUID,
    filename: str,
    content: bytes,
) -> Path:
    """Guarda el original en `archivos/<ramo-id>/<id>_<nombre>`."""
    course_folder = f"{_folder_name(course_name)}-{course_id.hex}"
    directory = FILES_DIR / course_folder
    directory.mkdir(parents=True, exist_ok=True)

    target = directory / f"{document_id.hex}_{_safe_filename(filename)}"
    try:
        with target.open("xb") as output:
            output.write(content)
    except FileExistsError:
        if target.read_bytes() != content:
            raise OSError(f"El archivo guardado no coincide con el documento {document_id}.")
    return target
