from uuid import UUID

from app import stubs
from app.deps import error_responses, no_content, require_auth
from app.schemas import Document, DocumentStatusData, Envelope, ListEnvelope
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile

MAX_FILES_PER_UPLOAD = 20  # maxItems del contrato

router = APIRouter(
    tags=["Documents"],
    dependencies=[Depends(require_auth)],
    responses=error_responses(401, 422),
)


@router.get(
    "/courses/{courseId}/documents",
    response_model=ListEnvelope[Document],
    summary="Lista los documentos asociados al curso",
)
async def list_documents(courseId: UUID):
    return stubs.document_list()


@router.post(
    "/courses/{courseId}/documents",
    status_code=202,
    response_model=ListEnvelope[Document],
    summary="Sube documentos al curso",
    responses=error_responses(415),
)
async def upload_documents(courseId: UUID, files: list[UploadFile] = File(...)):
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(
            status_code=422,
            detail=f"Máximo {MAX_FILES_PER_UPLOAD} archivos por solicitud",
        )
    # TODO: validar tipo (text/plain, text/markdown → 415 si no) y encolar el
    # procesamiento de cada archivo.
    return stubs.document_list()


@router.get(
    "/courses/{courseId}/documents/{documentId}",
    response_model=Envelope[Document],
    summary="Obtiene la información de un documento",
)
async def get_document(courseId: UUID, documentId: UUID):
    return stubs.document_response()


@router.delete(
    "/courses/{courseId}/documents/{documentId}",
    status_code=204,
    response_class=Response,
    summary="Elimina un documento",
    description="Requiere permisos de propietario o administrador del curso.",
    responses=error_responses(403),
)
async def delete_document(courseId: UUID, documentId: UUID):
    return no_content()


@router.get(
    "/courses/{courseId}/documents/{documentId}/status",
    response_model=Envelope[DocumentStatusData],
    summary="Consulta el estado de procesamiento de un documento",
)
async def get_document_status(courseId: UUID, documentId: UUID):
    return stubs.document_status()
