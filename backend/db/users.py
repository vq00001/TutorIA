"""Esquema y lectura del perfil del usuario actual."""

from sqlalchemy import (
    CheckConstraint, Column, DateTime, Index, MetaData, String, Table, Text, Uuid,
    func, select,
)
from sqlalchemy.engine import Connection, RowMapping

users = Table(
    "users",
    MetaData(schema="app"),
    Column("id", Uuid, primary_key=True),
    Column("nickname", String(50), nullable=False),
    Column("display_name", String(100), nullable=False),
    Column("email", String(254), nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("email_verified_at", DateTime(timezone=True)),
    Column("status", String(20), nullable=False, server_default="pending"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("nickname ~ '^[a-zA-Z0-9_]{3,50}$'", name="users_nickname_format"),
    CheckConstraint("length(btrim(display_name)) > 0", name="users_display_name_present"),
    CheckConstraint("email = lower(btrim(email)) AND email <> ''", name="users_email_normalized"),
    CheckConstraint("length(password_hash) > 0", name="users_password_hash_present"),
    CheckConstraint(
        "status IN ('pending', 'active', 'suspended', 'deletion_pending')",
        name="users_valid_status",
    ),
)
Index("users_nickname_ci_key", func.lower(users.c.nickname), unique=True)
Index("users_email_key", users.c.email, unique=True)

_profile_columns = (
    users.c.id,
    users.c.nickname,
    users.c.display_name,
    users.c.email,
    users.c.email_verified_at,
    users.c.status,
    users.c.created_at,
)
_current_user_id = func.nullif(func.current_setting("app.user_id", True), "").cast(Uuid)


def install_users(conn: Connection) -> None:
    """Instala la tabla y concede a la API lectura del perfil propio."""
    users.create(conn, checkfirst=True)
    conn.exec_driver_sql("ALTER TABLE app.users ENABLE ROW LEVEL SECURITY")
    conn.exec_driver_sql("ALTER TABLE app.users FORCE ROW LEVEL SECURITY")
    conn.exec_driver_sql("DROP POLICY IF EXISTS own_profile ON app.users")
    conn.exec_driver_sql(
        """CREATE POLICY own_profile ON app.users FOR SELECT TO tutoria_api
        USING (id = NULLIF(current_setting('app.user_id', true), '')::uuid)"""
    )
    conn.exec_driver_sql(
        """GRANT SELECT (id, nickname, display_name, email, email_verified_at, status, created_at)
        ON app.users TO tutoria_api"""
    )


def get_current_user(conn: Connection) -> RowMapping | None:
    """Lee el perfil asociado a la identidad de la transacción."""
    statement = select(*_profile_columns).where(users.c.id == _current_user_id).limit(1)
    return conn.execute(statement).mappings().one_or_none()
