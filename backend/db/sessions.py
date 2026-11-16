"""Credenciales y almacenamiento de sesiones de autenticación."""

from datetime import timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Table,
    Uuid,
    func,
    insert,
    select,
)
from sqlalchemy.engine import Connection, RowMapping

from .users import users

SESSION_LIFETIME_SECONDS = 3600

sessions = Table(
    "auth_sessions",
    users.metadata,
    Column("token_hash", LargeBinary(32), primary_key=True),
    Column("user_id", Uuid, ForeignKey("app.users.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("octet_length(token_hash) = 32", name="auth_sessions_hash_length"),
    CheckConstraint("expires_at > created_at", name="auth_sessions_expiration"),
)
Index("auth_sessions_user_id_idx", sessions.c.user_id)
Index("auth_sessions_expires_at_idx", sessions.c.expires_at)


def install_sessions(conn: Connection) -> None:
    """Instala las sesiones y limita la lectura de credenciales a la API."""
    sessions.create(conn, checkfirst=True)
    conn.exec_driver_sql("ALTER TABLE app.auth_sessions ENABLE ROW LEVEL SECURITY")
    conn.exec_driver_sql("ALTER TABLE app.auth_sessions FORCE ROW LEVEL SECURITY")
    conn.exec_driver_sql("DROP POLICY IF EXISTS issue_session ON app.auth_sessions")
    conn.exec_driver_sql(
        """CREATE POLICY issue_session ON app.auth_sessions FOR INSERT TO tutoria_api
        WITH CHECK (true)"""
    )
    conn.exec_driver_sql(
        "GRANT INSERT (token_hash, user_id, expires_at) ON app.auth_sessions TO tutoria_api"
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.login_credentials(candidate_email text)
        RETURNS TABLE (
            id uuid, nickname varchar, display_name varchar, email varchar,
            password_hash text, status varchar, created_at timestamptz
        )
        LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
            SELECT u.id, u.nickname, u.display_name, u.email, u.password_hash,
                   u.status, u.created_at
            FROM app.users AS u
            WHERE u.email = candidate_email AND u.status IN ('pending', 'active')
            FOR SHARE OF u
        $$"""
    )
    conn.exec_driver_sql("REVOKE ALL ON FUNCTION app.login_credentials(text) FROM PUBLIC")
    conn.exec_driver_sql("GRANT EXECUTE ON FUNCTION app.login_credentials(text) TO tutoria_api")

    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.session_user(candidate_hash bytea)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
            SELECT s.user_id
            FROM app.auth_sessions AS s JOIN app.users AS u ON u.id = s.user_id
            WHERE s.token_hash = candidate_hash
              AND s.expires_at > statement_timestamp()
              AND u.status IN ('pending', 'active')
        $$"""
    )
    conn.exec_driver_sql(
        """CREATE OR REPLACE FUNCTION app.revoke_session(candidate_hash bytea)
        RETURNS boolean
        LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
        AS $$
            WITH removed AS (
                DELETE FROM app.auth_sessions AS s USING app.users AS u
                WHERE s.token_hash = candidate_hash AND u.id = s.user_id
                  AND s.expires_at > statement_timestamp()
                  AND u.status IN ('pending', 'active')
                RETURNING 1
            )
            SELECT EXISTS (SELECT FROM removed)
        $$"""
    )
    conn.exec_driver_sql(
        "REVOKE ALL ON FUNCTION app.session_user(bytea), app.revoke_session(bytea) FROM PUBLIC"
    )
    conn.exec_driver_sql(
        "GRANT EXECUTE ON FUNCTION app.session_user(bytea), app.revoke_session(bytea) TO tutoria_api"
    )


def get_login_credentials(conn: Connection, email: str) -> RowMapping | None:
    credentials = func.app.login_credentials(email).table_valued(
        "id",
        "nickname",
        "display_name",
        "email",
        "password_hash",
        "status",
        "created_at",
    )
    return conn.execute(select(credentials)).mappings().one_or_none()


def _token_hash(token: str) -> bytes:
    return sha256(token.encode("ascii")).digest()


def get_session_user(conn: Connection, token: str) -> UUID | None:
    return conn.scalar(select(func.app.session_user(_token_hash(token))))


def revoke_session(conn: Connection, token: str) -> bool:
    return conn.scalar(select(func.app.revoke_session(_token_hash(token)))) is True


def issue_session(conn: Connection, user_id: UUID) -> str:
    """Devuelve el token nuevo y persiste solo su hash y su caducidad."""
    token = token_urlsafe(32)
    statement = insert(sessions).values(
        token_hash=_token_hash(token),
        user_id=user_id,
        expires_at=func.now() + timedelta(seconds=SESSION_LIFETIME_SECONDS),
    )
    conn.execute(statement)
    return token
