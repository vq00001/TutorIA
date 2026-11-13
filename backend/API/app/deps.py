"""Dependencias y utilidades compartidas por los routers."""

from typing import Annotated

from app.schemas import ActivityType, ErrorResponse
from fastapi import Depends, HTTPException, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False, bearerFormat="JWT")

_ERROR_DESCRIPTIONS = {
    401: "Autenticación requerida o inválida",
    403: "El usuario no tiene permisos suficientes",
    404: "Recurso no encontrado",
    409: "El recurso ya existe",
    415: "Tipo de archivo no soportado",
    422: "Datos inválidos",
    503: "Servicio no disponible",
}


def error_responses(*codes: int) -> dict[int | str, dict]:
    """Documenta respuestas de error con el schema ``ErrorResponse`` del contrato."""
    return {
        c: {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[c]}
        for c in codes
    }


async def require_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """Exige un ``Authorization: Bearer <token>``.

    STUB: solo comprueba que el header exista. Falta implementar la validación real
    del JWT y devolver el usuario actual.
    """
    if credentials is None or not credentials.credentials.strip():
        raise HTTPException(
            status_code=401,
            detail="Autenticación requerida o inválida",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


def no_content() -> Response:
    return Response(status_code=204)


Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
ActivityTypeFilter = Annotated[ActivityType | None, Query(alias="type")]

__all__ = [
    "ActivityTypeFilter",
    "Page",
    "PageSize",
    "error_responses",
    "no_content",
    "require_auth",
]
