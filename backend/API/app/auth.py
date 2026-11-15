import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status

from app.consts import ACCESS_TOKEN_EXPIRE_MINUTES, ALGORITHM, SECRET_KEY_JWT

from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer_scheme = HTTPBearer(auto_error=False)

########################################################################################################################
# Funciones mock
# TODO: Implementar almacenamiento de jti (identificador único de token) en una base de datos como Redis para permitir la revocación de tokens; la idea es que solo los tokens que estén en la DB son validos, y cuando un usuario cierra sesión, se elimina el jti de la DB para que el token ya no sea válido.

token_store = (
    set()
)  # Simulación de almacenamiento de jti en memoria; reemplazar con Redis u otra base de datos en producción

def validate_credentials(user_email: str) -> bool:
    # TODO: Implementar verificación de credenciales contra una base de datos de usuarios
    # - Validar que el usuario existe y sigue activo
    return True

########################################################################################################################

def create_jwt(data: dict, expires_delta: timedelta | None = None) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=ACCESS_TOKEN_EXPIRE_MINUTES
        )

    payload = {
        "sub": data.get("sub"),
        "exp": expire,
        "jti": str(uuid.uuid4()),  # Genera un identificador único para el token
    }

    token_store.add(payload["jti"])  # Almacena el jti en la "base de datos" simulada

    return jwt.encode(payload, SECRET_KEY_JWT, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY_JWT, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )


def authenticate_token(token: str) -> str:
    """Valida el JWT completo y devuelve el email del usuario (sub)."""
    payload = decode_access_token(token)

    def unauthorized(detail: str) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("jti") not in token_store:
        raise unauthorized("Token revocado o inválido")

    user_email = payload.get("sub")
    if user_email is None:
        raise unauthorized("Token inválido: falta el identificador del usuario (sub)")

    if not validate_credentials(user_email):
        raise unauthorized("Credenciales inválidas o usuario no activo")

    return user_email

def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> str:
    """Obtiene el usuario actual a partir del token JWT.

    Lanza una excepción HTTP 401 si el token es inválido o ha sido revocado.
    """
    if credentials is None or not credentials.credentials.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticación requerida o inválida",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authenticate_token(credentials.credentials)


def expire_token(token: str):
    payload = decode_access_token(token)
    jti = payload.get("jti")
    if jti in token_store:
        token_store.remove(
            jti
        )  # Elimina el jti de la "base de datos" simulada para revocar el token
