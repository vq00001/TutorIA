from app import stubs
from app.deps import error_responses, no_content, require_auth
from app.schemas import Envelope, UpdateUserRequest, User
from fastapi import APIRouter, Depends, Response

router = APIRouter(
    tags=["Users"],
    dependencies=[Depends(require_auth)],
    responses=error_responses(401, 422),
)


@router.get(
    "/users/me",
    response_model=Envelope[User],
    summary="Obtiene el perfil completo del usuario actual",
)
async def get_me():
    return stubs.user_response()


@router.patch(
    "/users/me",
    response_model=Envelope[User],
    summary="Modifica el perfil del usuario actual",
)
async def update_me(body: UpdateUserRequest):
    return stubs.user_response()


@router.delete(
    "/users/me",
    status_code=204,
    response_class=Response,
    summary="Elimina la cuenta del usuario actual",
)
async def delete_me():
    return no_content()
