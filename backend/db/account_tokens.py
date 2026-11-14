"""Persistencia de tokens para verificar correo y recuperar contraseñas."""

from hashlib import sha256
from secrets import token_urlsafe
from typing import Literal
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Table,
    Uuid,
    func,
    select,
)
from sqlalchemy.engine import Connection

from .users import users

Purpose = Literal["verify_email", "reset_password"]

account_tokens = Table(
    "account_tokens",
    users.metadata,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, ForeignKey("app.users.id", ondelete="CASCADE"), nullable=False),
    Column("purpose", String(20), nullable=False),
    Column("token_hash", LargeBinary(32), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True)),
    CheckConstraint("purpose IN ('verify_email', 'reset_password')", name="account_tokens_purpose"),
    CheckConstraint("octet_length(token_hash) = 32", name="account_tokens_hash_length"),
    CheckConstraint("expires_at > created_at", name="account_tokens_expiration"),
)
Index("account_tokens_user_purpose_idx", account_tokens.c.user_id, account_tokens.c.purpose)
Index(
    "account_tokens_open_key",
    account_tokens.c.user_id,
    account_tokens.c.purpose,
    unique=True,
    postgresql_where=account_tokens.c.consumed_at.is_(None),
)


def install_account_tokens(conn: Connection) -> None:
    """Instala el almacén privado y concede a la API solo las operaciones requeridas."""
    account_tokens.create(conn, checkfirst=True)
    conn.exec_driver_sql("ALTER TABLE app.account_tokens ENABLE ROW LEVEL SECURITY")
    conn.exec_driver_sql("ALTER TABLE app.account_tokens FORCE ROW LEVEL SECURITY")

    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.issue_account_token(
            candidate_email text, token_purpose text, digest bytea, token_id uuid
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            target_user uuid;
            issued_at timestamptz;
        BEGIN
            SELECT u.id INTO target_user
            FROM app.users AS u
            WHERE u.email = candidate_email
              AND (
                  (token_purpose = 'verify_email' AND u.status = 'pending')
                  OR (token_purpose = 'reset_password' AND u.status IN ('pending', 'active'))
              )
            FOR UPDATE;
            IF NOT FOUND THEN
                RETURN false;
            END IF;

            issued_at := clock_timestamp();
            UPDATE app.account_tokens
            SET consumed_at = issued_at
            WHERE user_id = target_user AND purpose = token_purpose AND consumed_at IS NULL;

            INSERT INTO app.account_tokens (id, user_id, purpose, token_hash, created_at, expires_at)
            VALUES (
                token_id, target_user, token_purpose, digest, issued_at,
                issued_at + CASE WHEN token_purpose = 'verify_email'
                                 THEN interval '24 hours' ELSE interval '30 minutes' END
            );
            RETURN true;
        END
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.apply_account_token(
            digest bytea, token_purpose text, new_password_hash text
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            target_user uuid;
            applied_at timestamptz;
        BEGIN
            IF token_purpose IS NULL OR token_purpose NOT IN ('verify_email', 'reset_password') THEN
                RETURN false;
            END IF;
            IF token_purpose = 'reset_password'
               AND (new_password_hash IS NULL OR left(new_password_hash, 10) <> '$argon2id$') THEN
                RETURN false;
            END IF;

            -- Emisión y consumo bloquean primero la cuenta, luego el token.
            SELECT u.id INTO target_user
            FROM app.users AS u
            WHERE u.id = (
                SELECT t.user_id FROM app.account_tokens AS t
                WHERE t.token_hash = digest AND t.purpose = token_purpose
            )
              AND (
                  (token_purpose = 'verify_email' AND u.status = 'pending')
                  OR (token_purpose = 'reset_password' AND u.status IN ('pending', 'active'))
              )
            FOR UPDATE;
            IF NOT FOUND THEN
                RETURN false;
            END IF;

            applied_at := clock_timestamp();
            UPDATE app.account_tokens
            SET consumed_at = applied_at
            WHERE token_hash = digest AND purpose = token_purpose
              AND consumed_at IS NULL AND expires_at > applied_at;
            IF NOT FOUND THEN
                RETURN false;
            END IF;

            IF token_purpose = 'verify_email' THEN
                UPDATE app.users SET status = 'active', email_verified_at = applied_at
                WHERE id = target_user;
            ELSE
                UPDATE app.users SET password_hash = new_password_hash WHERE id = target_user;
                DELETE FROM app.auth_sessions WHERE user_id = target_user;
            END IF;
            RETURN true;
        END
        $$"""
    )
    conn.exec_driver_sql(
        "REVOKE ALL ON FUNCTION app.issue_account_token(text, text, bytea, uuid), "
        "app.apply_account_token(bytea, text, text) FROM PUBLIC"
    )
    conn.exec_driver_sql(
        "GRANT EXECUTE ON FUNCTION app.issue_account_token(text, text, bytea, uuid), "
        "app.apply_account_token(bytea, text, text) TO tutoria_api"
    )


def issue_account_token(conn: Connection, email: str, purpose: Purpose) -> str | None:
    """Emite un token para el servicio de correo. Devuelve None para cuentas no elegibles."""
    token = token_urlsafe(32)
    digest = sha256(token.encode()).digest()
    statement = select(
        func.app.issue_account_token(email.strip().lower(), purpose, digest, uuid4())
    )
    return token if conn.scalar(statement) else None


def verify_email(conn: Connection, token: str) -> bool:
    """Consume un token de verificación y activa la cuenta en la misma transacción."""
    digest = sha256(token.encode()).digest()
    return conn.scalar(select(func.app.apply_account_token(digest, "verify_email", None))) is True


def reset_password(conn: Connection, token: str, password_hash: str) -> bool:
    """Consume un token de recuperación, cambia el hash y revoca las sesiones."""
    digest = sha256(token.encode()).digest()
    statement = select(func.app.apply_account_token(digest, "reset_password", password_hash))
    return conn.scalar(statement) is True
