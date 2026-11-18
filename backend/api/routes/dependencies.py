"""Dependencias y respuestas compartidas por las rutas HTTP."""

import re
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.engine import Connection

from ...db.connection import set_user_context, transaction
from ...db.sessions import get_session_user

_bearer = HTTPBearer(auto_error=False, scheme_name="bearerAuth")
_token_format = re.compile(r"[A-Za-z0-9_-]{43}")


class InvalidSession(Exception):
    pass


class ErrorResponse(BaseModel):
    error: str
    message: str


def require_bearer(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    if credentials is None or _token_format.fullmatch(credentials.credentials) is None:
        raise InvalidSession
    return credentials.credentials


BearerToken = Annotated[str, Depends(require_bearer)]


def authenticated_connection(token: BearerToken) -> Iterator[Connection]:
    with transaction() as connection:
        identifier = get_session_user(connection, token)
        if identifier is None:
            raise InvalidSession
        set_user_context(connection, identifier)
        yield connection


AuthenticatedConnection = Annotated[Connection, Depends(authenticated_connection)]


def error_response(code: int, error: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=code, content={"error": error, "message": message})
