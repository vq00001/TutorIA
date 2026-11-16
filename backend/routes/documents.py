"""Listado, carga y seguimiento de documentos."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, File, Header, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import AwareDatetime, BaseModel
from sqlalchemy.exc import DBAPIError

from ..db.courses import get_member_course
from ..db.documents import delete_document, download_document, get_document_metadata, list_documents
from ..db.idempotency import run_idempotent
from ..db.submissions import enqueue_upload
from ..file_storage import store_uploaded_document
from .dependencies import AuthenticatedConnection, ErrorResponse, error_response

router = APIRouter(tags=["Documents"])
_MAX_BATCH_BYTES = 20 * 1024 * 1024
_FILE_MIME_TYPES = {
    ".txt": ("text/plain", {"text/plain", "application/octet-stream"}),
    ".md": ("text/markdown", {"text/markdown", "text/plain", "application/octet-stream"}),
    ".pdf": ("application/pdf", {"application/pdf", "application/octet-stream"}),
}

ProcessingStatus = Literal["queued", "processing", "ready", "failed"]
_ERRORS = {code: {"model": ErrorResponse} for code in (401, 404, 422)}


class DocumentStatus(BaseModel):
    documentId: UUID
    status: ProcessingStatus
    errorCode: str | None


class Document(BaseModel):
    id: UUID
    courseId: UUID
    name: str
    title: str
    size: int
    mimeType: str
    status: ProcessingStatus
    uploadedById: UUID
    uploadedAt: datetime


class DocumentResponse(BaseModel):
    data: Document


class Cursor(BaseModel):
    afterCreatedAt: datetime
    afterId: UUID


class DocumentList(BaseModel):
    data: list[Document]
    nextCursor: Cursor | None


class StatusResponse(BaseModel):
    data: DocumentStatus


class UploadResult(BaseModel):
    documentId: UUID | None
    jobId: UUID | None
    duplicate: bool


class UploadResponse(BaseModel):
    data: list[UploadResult]


def _status(raw: str) -> ProcessingStatus:
    return "queued" if raw == "uploaded" else raw


def _document(row) -> Document:
    return Document(
        id=row.id, courseId=row.course_id, name=row.original_name, title=row.title,
        size=row.size_bytes, mimeType=row.mime_type, status=_status(row.processing_status),
        uploadedById=row.uploaded_by, uploadedAt=row.created_at,
    )


def _not_found() -> JSONResponse:
    return error_response(404, "DOCUMENT_NOT_FOUND", "Clase o documento no encontrado.")


@router.get("/courses/{courseId}/documents", response_model=DocumentList, responses=_ERRORS)
def documents_index(
    courseId: UUID,
    connection: AuthenticatedConnection,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    afterCreatedAt: AwareDatetime | None = None,
    afterId: UUID | None = None,
) -> DocumentList | JSONResponse:
    if (afterCreatedAt is None) != (afterId is None):
        return error_response(422, "INVALID_CURSOR", "Envía ambos campos del cursor.")
    if get_member_course(connection, courseId) is None:
        return _not_found()
    after = (afterCreatedAt, afterId) if afterCreatedAt is not None and afterId is not None else None
    rows = list_documents(connection, courseId, limit=limit, after=after, lookahead=True)
    page = rows[:limit]
    cursor = None
    if len(rows) > limit:
        last = page[-1]
        cursor = Cursor(afterCreatedAt=last.created_at, afterId=last.id)
    return DocumentList(
        data=[
            _document(row)
            for row in page
        ],
        nextCursor=cursor,
    )


@router.post(
    "/courses/{courseId}/documents", status_code=202, response_model=UploadResponse,
    responses={**_ERRORS, **{code: {"model": ErrorResponse} for code in (403, 409, 413, 415, 429)}},
)
def documents_upload(
    courseId: UUID,
    connection: AuthenticatedConnection,
    files: Annotated[list[UploadFile], File(min_length=1, max_length=20)],
    key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> UploadResponse | JSONResponse:
    if not key.strip():
        return error_response(422, "INVALID_IDEMPOTENCY_KEY", "La clave no puede estar vacía.")
    course = get_member_course(connection, courseId)
    if course is None:
        return _not_found()
    if course.status != "active":
        return error_response(403, "COURSE_NOT_ACTIVE", "La clase no admite cargas.")
    remaining = _MAX_BATCH_BYTES
    batch = []
    fingerprint = []
    for upload in files:
        name = upload.filename or ""
        if not name.strip() or len(name) > 255 or "\x00" in name:
            return error_response(422, "INVALID_FILENAME", "Nombre de archivo inválido.")
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        extension = f".{extension}"
        file_type = _FILE_MIME_TYPES.get(extension)
        mime = (upload.content_type or "").split(";", 1)[0].strip().lower()
        if file_type is None or mime not in file_type[1]:
            return error_response(
                415, "UNSUPPORTED_DOCUMENT_TYPE", "Usa archivos PDF, Markdown o texto."
            )
        mime = file_type[0]
        data = upload.file.read(remaining + 1)
        if len(data) > remaining:
            return error_response(413, "UPLOAD_TOO_LARGE", "El lote supera 20 MiB.")
        if not data:
            return error_response(422, "EMPTY_DOCUMENT", "El archivo está vacío.")
        if extension == ".pdf" and b"%PDF-" not in data[:1024]:
            return error_response(422, "INVALID_DOCUMENT", "El archivo no contiene un PDF válido.")
        remaining -= len(data)
        batch.append((name, mime, data))
        fingerprint.append([name, mime, sha256(data).hexdigest()])

    def enqueue_batch(conn):
        deadline = datetime.now(timezone.utc) + timedelta(minutes=15)
        return [
            enqueue_upload(
                conn, courseId, deadline_at=deadline,
                filename=name, data=data, mime_type=mime, warn_on_duplicate=False,
            )
            for name, mime, data in batch
        ]

    try:
        result = run_idempotent(
            connection, scope=f"document_upload_batch:{courseId}", key=key,
            payload=fingerprint, operation=enqueue_batch,
        )
    except DBAPIError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        primary = getattr(getattr(exc.orig, "diag", None), "message_primary", None)
        if (sqlstate, primary) == ("23505", "IDEMPOTENCY_CONFLICT"):
            return error_response(409, primary, "La clave ya se usó con otro lote.")
        if (sqlstate, primary) == ("23514", "UPLOAD_QUOTA_EXCEEDED"):
            return error_response(429, primary, "Se alcanzó la cuota de carga.")
        if sqlstate == "42501":
            return error_response(403, "UPLOAD_FORBIDDEN", "La cuenta no puede cargar documentos.")
        raise
    for (name, _mime, data), item in zip(batch, result):
        if item["document_id"] is not None:
            store_uploaded_document(
                course_name=course.name,
                course_id=courseId,
                document_id=UUID(item["document_id"]),
                filename=name,
                content=data,
            )
    return UploadResponse(data=[
        UploadResult(documentId=item["document_id"], jobId=item["job_id"],
                     duplicate=item["document_id"] is None)
        for item in result
    ])


@router.get(
    "/courses/{courseId}/documents/{documentId}/status",
    response_model=StatusResponse, responses=_ERRORS,
)
def documents_status(
    courseId: UUID, documentId: UUID, connection: AuthenticatedConnection
) -> StatusResponse | JSONResponse:
    row = get_document_metadata(connection, courseId, documentId)
    if row is None:
        return _not_found()
    return StatusResponse(data=DocumentStatus(
        documentId=documentId, status=_status(row.processing_status), errorCode=row.error_code
    ))


@router.get(
    "/courses/{courseId}/documents/{documentId}",
    response_model=DocumentResponse, responses=_ERRORS,
)
def documents_detail(
    courseId: UUID, documentId: UUID, connection: AuthenticatedConnection
) -> DocumentResponse | JSONResponse:
    row = get_document_metadata(connection, courseId, documentId)
    if row is None:
        return _not_found()
    return DocumentResponse(data=_document(row))


@router.get(
    "/courses/{courseId}/documents/{documentId}/download",
    response_class=Response,
    responses={
        **_ERRORS,
        200: {"content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}},
    },
)
def documents_download(courseId: UUID, documentId: UUID, connection: AuthenticatedConnection) -> Response:
    original = download_document(connection, courseId, documentId)
    if original is None:
        return _not_found()
    filename = quote(original.original_name, safe="")
    return Response(
        content=bytes(original.bytes),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete(
    "/courses/{courseId}/documents/{documentId}", status_code=204,
    responses={**_ERRORS, 403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def documents_delete(courseId: UUID, documentId: UUID, connection: AuthenticatedConnection) -> Response:
    if get_document_metadata(connection, courseId, documentId) is None:
        return _not_found()
    try:
        with connection.begin_nested():
            deleted = delete_document(connection, courseId, documentId)
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "42501":
            return error_response(403, "DOCUMENT_DELETE_FORBIDDEN", "La cuenta no puede borrar el documento.")
        raise
    if not deleted:
        if get_document_metadata(connection, courseId, documentId) is None:
            return _not_found()
        return error_response(409, "DOCUMENT_DELETE_CONFLICT", "El documento tiene un borrado pendiente.")
    return Response(status_code=204)
