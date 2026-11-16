"""Verificación de correo y recuperación mediante tokens de un solo uso."""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Response
from pydantic import BaseModel, EmailStr, Field, SecretStr

from ..db.account_tokens import Purpose, issue_account_token, reset_password, verify_email
from . import dependencies
from .auth import password_hasher
from .dependencies import ErrorResponse, error_response

router = APIRouter(prefix="/auth", tags=["Auth"])
logger = logging.getLogger(__name__)
Token = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{43}$")]
_VALIDATION = {422: {"model": ErrorResponse}}
_ISSUE_ERRORS = {**_VALIDATION, 503: {"model": ErrorResponse}}
_CONFIRM_ERRORS = {**_VALIDATION, 400: {"model": ErrorResponse}}


class EmailRequest(BaseModel):
    email: EmailStr = Field(max_length=254)


class TokenRequest(BaseModel):
    token: Token


class ResetRequest(TokenRequest):
    password: SecretStr = Field(min_length=8, max_length=128)


def send_token(host: str, sender: str, recipient: str, purpose: Purpose, token: str) -> None:
    try:
        message = EmailMessage()
        message["From"] = sender
        message["To"] = recipient
        if purpose == "verify_email":
            message["Subject"] = "Verifica tu correo en TutorIA"
        else:
            message["Subject"] = "Recupera tu contraseña de TutorIA"
        message.set_content(f"Introduce este código en TutorIA:\n\n{token}\n")
        with smtplib.SMTP_SSL(
            host, int(os.getenv("SMTP_PORT", "465")), timeout=10,
            context=ssl.create_default_context(),
        ) as smtp:
            username = os.getenv("SMTP_USERNAME")
            if username:
                smtp.login(username, os.getenv("SMTP_PASSWORD", ""))
            smtp.send_message(message)
    except (OSError, ValueError):
        logger.warning("Falló la entrega del correo de cuenta.")


def _issue(payload: EmailRequest, purpose: Purpose, background: BackgroundTasks) -> Response:
    host, sender = os.getenv("SMTP_HOST"), os.getenv("SMTP_FROM")
    if not host or not sender:
        return error_response(503, "MAIL_UNAVAILABLE", "El servicio de correo no está disponible.")
    email = payload.email.lower()
    with dependencies.transaction() as connection:
        token = issue_account_token(connection, email, purpose)
    if token is not None:
        background.add_task(send_token, host, sender, email, purpose, token)
    return Response(status_code=202)


def _confirmation(applied: bool) -> Response:
    if not applied:
        return error_response(400, "INVALID_ACCOUNT_TOKEN", "El código no es válido o venció.")
    return Response(status_code=204)


@router.post("/email-verification/request", status_code=202, responses=_ISSUE_ERRORS)
def request_verification(payload: EmailRequest, background: BackgroundTasks) -> Response:
    return _issue(payload, "verify_email", background)


@router.post("/email-verification/confirm", status_code=204, responses=_CONFIRM_ERRORS)
def confirm_verification(payload: TokenRequest) -> Response:
    with dependencies.transaction() as connection:
        applied = verify_email(connection, payload.token)
    return _confirmation(applied)


@router.post("/password-reset/request", status_code=202, responses=_ISSUE_ERRORS)
def request_password_reset(payload: EmailRequest, background: BackgroundTasks) -> Response:
    return _issue(payload, "reset_password", background)


@router.post("/password-reset/confirm", status_code=204, responses=_CONFIRM_ERRORS)
def confirm_password_reset(payload: ResetRequest) -> Response:
    password_hash = password_hasher.hash(payload.password.get_secret_value())
    with dependencies.transaction() as connection:
        applied = reset_password(connection, payload.token, password_hash)
    return _confirmation(applied)
