"""Cuenta local reproducible para probar la interfaz sin servicio de correo."""

from datetime import datetime, timezone
from uuid import NAMESPACE_DNS, uuid5

from argon2 import PasswordHasher
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection

from .users import users


def seed_dev_account(connection: Connection, email: str, password: str) -> None:
    """Crea o restablece una cuenta conocida; nunca toma una cuenta ajena."""
    if not email or email != email.strip().lower() or len(password) < 8:
        raise ValueError("INVALID_DEV_ACCOUNT_CONFIG")

    identifier = uuid5(NAMESPACE_DNS, f"tutoria-dev-account:{email}")
    existing = connection.execute(
        select(users.c.id).where(users.c.email == email)
    ).scalar_one_or_none()
    if existing is not None and existing != identifier:
        raise ValueError("DEV_ACCOUNT_EMAIL_ALREADY_USED")

    password_hash = PasswordHasher().hash(password)
    if existing is None:
        connection.execute(insert(users).values(
            id=identifier,
            nickname=f"u_{identifier.hex}",
            display_name="Usuario de desarrollo",
            email=email,
            password_hash=password_hash,
            status="active",
            email_verified_at=datetime.now(timezone.utc),
        ))
    else:
        connection.execute(
            update(users).where(users.c.id == identifier).values(
                password_hash=password_hash,
                status="active",
                email_verified_at=datetime.now(timezone.utc),
            )
        )
