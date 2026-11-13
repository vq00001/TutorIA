import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.consts import ACCESS_TOKEN_EXPIRE_MINUTES, ALGORITHM, SECRET_KEY_JWT

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# TODO: Implementar almacenamiento de jti (identificador único de token) en una base de datos como Redis para permitir la revocación de tokens; la idea es que solo los tokens que estén en la DB son validos, y cuando un usuario cierra sesión, se elimina el jti de la DB para que el token ya no sea válido.

token_store = (
    set()
)  # Simulación de almacenamiento de jti en memoria; reemplazar con Redis u otra base de datos en producción


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


def validate_jwt(token: str) -> dict:
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
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Error al validar el token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> dict:
    payload = validate_jwt(token)

    http_exception = lambda detail: HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )

    if payload.get("jti") not in token_store:
        raise http_exception("Token revocado o inválido")

    if "sub" not in payload or not payload["sub"]:
        raise http_exception("Token inválido: falta el identificador del usuario (sub)")

    return payload["sub"]  # Retorna el identificador del usuario (sub) del token


def expire_token(token: str):
    payload = validate_jwt(token)
    jti = payload.get("jti")
    if jti in token_store:
        token_store.remove(
            jti
        )  # Elimina el jti de la "base de datos" simulada para revocar el token
