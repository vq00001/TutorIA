from app import stubs
from app.deps import error_responses, no_content, require_auth
from app.schemas import AuthData, Envelope, LoginRequest, RegisterRequest
from fastapi import APIRouter, Depends, Response

router = APIRouter(tags=["Auth"])


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
    # TODO: verificar credenciales; 401 si son inválidas.
    return stubs.auth_response()


@router.post(
    "/auth/logout",
    status_code=204,
    response_class=Response,
    summary="Cierra la sesión actual",
    dependencies=[Depends(require_auth)],
    responses=error_responses(401),
)
async def logout():
    # TODO: invalidar el token / sesión.
    return no_content()
