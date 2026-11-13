from datetime import timedelta

from fastapi import APIRouter, Depends, Response

from app import stubs
from app.auth import create_jwt, expire_token, oauth2_scheme
from app.consts import ACCESS_TOKEN_EXPIRE_MINUTES
from app.deps import error_responses, no_content, require_auth
from app.schemas import (
    AuthData,
    Envelope,
    ErrorResponse,
    LoginRequest,
    RegisterRequest,
    User,
)
from app.stubs import _user

router = APIRouter(tags=["Auth"])
from app import schemas as s


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
    # TODO: Usar la base de datos para verificar credenciales y obtener información del usuario.

    def mock_credential_is_valid(email: str, password: str) -> bool:
        return True  # TODO: Implementar verificación real de credenciales

    if not mock_credential_is_valid(body.email, body.password):
        return ErrorResponse(
            error="invalid_credentials", message="Credenciales inválidas"
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_jwt(
        data={"sub": body.email}, expires_delta=access_token_expires
    )

    # QUESTION: Este return es segun el contrato, pero de verdad es necesario devolver tantos datos del usuario? No deberia ser solo el token y el tipo de token? Esto puede ser un riesgo de seguridad si se devuelve informacion sensible del usuario.
    return s.Envelope[s.AuthData](
        data=s.AuthData(
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
async def logout(token: str = Depends(oauth2_scheme)):
    expire_token(token)
    return no_content()
