"""Interfaz HTTP de TutorIA."""

from datetime import datetime
from secrets import token_urlsafe
from typing import Literal
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator

from .db.connection import transaction
from .db.sessions import SESSION_LIFETIME_SECONDS, get_login_credentials, issue_session
from .db.users import insert_pending_user

load_dotenv()
app = FastAPI(title="TutorIA API")
password_hasher = PasswordHasher()
_dummy_password_hash = password_hasher.hash(token_urlsafe(32))


class LoginRequest(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.lower()
        if len(normalized) > 254:
            raise ValueError("Correo demasiado largo")
        return normalized


class RegisterRequest(LoginRequest):
    displayName: str = Field(min_length=1, max_length=100)

    @field_validator("displayName", mode="before")
    @classmethod
    def strip_display_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("displayName")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Nombre inválido")
        return value


class RegisteredUser(BaseModel):
    id: UUID
    nickname: str
    email: str
    displayName: str
    status: Literal["pending"] = "pending"


class RegisterResponse(BaseModel):
    data: RegisteredUser


class LoginUser(BaseModel):
    id: UUID
    nickname: str
    email: str
    displayName: str
    status: Literal["pending", "active"]
    createdAt: datetime


class SessionData(BaseModel):
    accessToken: str
    tokenType: Literal["Bearer"] = "Bearer"
    expiresIn: int
    user: LoginUser


class LoginResponse(BaseModel):
    data: SessionData


class ErrorResponse(BaseModel):
    error: str
    message: str


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, _error: RequestValidationError) -> JSONResponse:
    # El cuerpo y los valores rechazados pueden contener la contraseña.
    if _request.url.path.endswith("/login"):
        return JSONResponse(
            status_code=422,
            content={"error": "INVALID_LOGIN", "message": "Revisa los datos del inicio de sesión."},
        )
    return JSONResponse(
        status_code=422,
        content={"error": "INVALID_REGISTRATION", "message": "Revisa los datos del registro."},
    )


@app.post(
    "/api/v1/auth/register",
    status_code=status.HTTP_201_CREATED,
    response_model=RegisterResponse,
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
def register(payload: RegisterRequest) -> RegisterResponse | JSONResponse:
    identifier = uuid4()
    nickname = f"u_{identifier.hex}"
    password_hash = password_hasher.hash(payload.password.get_secret_value())
    with transaction() as connection:
        created = insert_pending_user(
            connection,
            identifier=identifier,
            nickname=nickname,
            email=payload.email,
            display_name=payload.displayName,
            password_hash=password_hash,
        )
    if not created:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "REGISTRATION_CONFLICT", "message": "No se pudo crear la cuenta."},
        )
    return RegisterResponse(
        data=RegisteredUser(
            id=identifier,
            nickname=nickname,
            email=payload.email,
            displayName=payload.displayName,
        )
    )


@app.post(
    "/api/v1/auth/login",
    response_model=LoginResponse,
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
def login(payload: LoginRequest, response: Response) -> LoginResponse | JSONResponse:
    with transaction() as connection:
        user = get_login_credentials(connection, payload.email)
        stored_hash = user["password_hash"] if user is not None else _dummy_password_hash
        try:
            verified = password_hasher.verify(stored_hash, payload.password.get_secret_value())
        except (VerificationError, InvalidHashError):
            verified = False
        if not verified or user is None:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": "Bearer"},
                content={"error": "INVALID_CREDENTIALS", "message": "Credenciales inválidas."},
            )
        token = issue_session(connection, user["id"])

    response.headers["Cache-Control"] = "no-store"
    return LoginResponse(
        data=SessionData(
            accessToken=token,
            expiresIn=SESSION_LIFETIME_SECONDS,
            user=LoginUser(
                id=user["id"],
                nickname=user["nickname"],
                email=user["email"],
                displayName=user["display_name"],
                status=user["status"],
                createdAt=user["created_at"],
            ),
        )
    )
