import os
from contextlib import contextmanager
from functools import lru_cache
from uuid import UUID

from sqlalchemy import URL, create_engine, text


def compose_database_url() -> URL:
    return URL.create(
        "postgresql+psycopg",
        host=os.environ["POSTGRES_HOST"],
        database=os.environ["POSTGRES_DB"],
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


@lru_cache
def engine():
    url = os.environ.get("DATABASE_URL")
    if not url or not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("DATABASE_URL must specify PostgreSQL with psycopg")
    pool_size = int(os.environ.get("DB_POOL_SIZE", "5"))
    if not 1 <= pool_size <= 20:
        raise RuntimeError("DB_POOL_SIZE must be between 1 and 20")
    result = create_engine(
        url, pool_size=pool_size, max_overflow=0, pool_pre_ping=True, hide_parameters=True
    )
    with result.connect() as conn:
        role = conn.execute(
            text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
        ).one()
        if any(role):
            result.dispose()
            raise RuntimeError("Application role must not be superuser or BYPASSRLS")
        owns_content = conn.execute(
            text("""SELECT EXISTS(SELECT 1 FROM pg_class c
          JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_roles r ON r.oid=c.relowner
          WHERE n.nspname='app' AND r.rolname=current_user AND c.relkind IN ('r','p'))""")
        ).scalar_one()
        if owns_content:
            result.dispose()
            raise RuntimeError("Application role must not own application tables")
    return result


def set_user_context(conn, user_id: UUID | None) -> None:
    conn.execute(
        text("SELECT set_config('app.user_id', :uid, true)"),
        {"uid": str(user_id) if user_id else ""},
    )


@contextmanager
def transaction(user_id: UUID | None = None):
    with engine().begin() as conn:
        set_user_context(conn, user_id)
        yield conn


def rows(conn, sql, **params):
    return conn.execute(text(sql), params).mappings().all()


def one(conn, sql, **params):
    return conn.execute(text(sql), params).mappings().first()
