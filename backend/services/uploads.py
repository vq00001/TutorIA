"""Inspect multipart uploads without retaining a complete batch in memory."""

from dataclasses import dataclass
from hashlib import sha256

from fastapi import UploadFile

from backend.services.documents import MAX_FILE_BYTES


@dataclass(frozen=True)
class UploadSummary:
    name: str
    size: int
    digest: str
    error: str | None


async def inspect_upload(file: UploadFile, limit=MAX_FILE_BYTES):
    hashed = sha256()
    size = 0
    while chunk := await file.read(65536):
        size += len(chunk)
        hashed.update(chunk)
    await file.seek(0)
    return UploadSummary(
        file.filename or "", size, hashed.hexdigest(), "FILE_TOO_LARGE" if size > limit else None
    )
