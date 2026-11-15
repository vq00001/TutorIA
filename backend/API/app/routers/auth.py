from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from app import stubs
from app.auth import bearer_scheme, create_jwt, expire_token
from app.consts import ACCESS_TOKEN_EXPIRE_MINUTES
from app.deps import error_responses, no_content, require_auth
from app.schemas import (
    AuthData,
    Envelope,
    ErrorResponse,
    LoginRequest,
    RegisterRequest,
)
from app.stubs import _user

router = APIRouter(tags=["Auth"])


def mock_credential_is_valid(email: str, password: str) -> bool:
    return True  # TODO: Implementar verificación real de credenciales


@router.post(
    "/auth/register",
    status_code=201,
    response_model=Envelope[AuthData],
    summary="Registra un usuario",
    responses=error_responses(409, 422),
)
async def register(body: RegisterRequest):
    # TODO: crear usuario; 409 si el email ya existe.
    return stubs.auth_response()


@router.post(
    "/auth/login",
    response_model=Envelope[AuthData],
    summary="Inicia sesión",
    responses=error_responses(401, 422),
)
async def login(body: LoginRequest):
    # TODO: Usar la base de datos para verificar credenciales y obtener el usuario.
    if not mock_credential_is_valid(body.email, body.password):
        return JSONResponse(
            status_code=401,
            content=ErrorResponse(
                error="invalid_credentials", message="Credenciales inválidas"
            ).model_dump(),
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_jwt(
        data={"sub": body.email},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    return Envelope[AuthData](
        # QUESTION: Este return es segun el contrato, pero de verdad es necesario devolver tantos datos del usuario? No deberia ser solo el token y el tipo de token? Esto puede ser un riesgo de seguridad si se devuelve informacion sensible del usuario.
        data=AuthData(
            accessToken=access_token,
            tokenType="Bearer",
            expiresIn=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user=_user(),
        )
    )


@router.post(
    "/auth/logout",
    status_code=204,
    response_class=Response,
    summary="Cierra la sesión actual",
    dependencies=[Depends(require_auth)],
    responses=error_responses(401),
)
async def logout(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
):
    expire_token(credentials.credentials)
    return no_content()