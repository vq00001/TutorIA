from contextlib import nullcontext
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from db import connection
from db import initialize as bootstrap
from db.users import get_current_user


@pytest.fixture(autouse=True)
def reset_engine_cache():
    connection.engine.cache_clear()
    yield
    connection.engine.cache_clear()


@pytest.mark.parametrize("url", [None, "sqlite://", "postgresql://localhost/tutoria"])
def test_engine_rejects_invalid_database_url(monkeypatch, url):
    if url is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(RuntimeError, match="PostgreSQL with psycopg"):
        connection.engine()


@pytest.mark.parametrize("role,owns_tables", [((True, False), False), ((False, True), False), ((False, False), True)])
def test_engine_rejects_roles_that_can_bypass_user_isolation(monkeypatch, role, owns_tables):
    conn, engine = Mock(), Mock()
    conn.execute.return_value.one.return_value = role
    conn.execute.return_value.scalar_one.return_value = owns_tables
    engine.connect.return_value = nullcontext(conn)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://localhost/tutoria")
    monkeypatch.setenv("DB_POOL_SIZE", "1")
    monkeypatch.setattr(connection, "create_engine", Mock(return_value=engine))
    with pytest.raises(RuntimeError, match="Application role must not"):
        connection.engine()
    engine.dispose.assert_called_once()


def test_transaction_sets_local_identity(monkeypatch):
    conn, engine = Mock(), Mock()
    engine.begin.return_value = nullcontext(conn)
    monkeypatch.setattr(connection, "engine", Mock(return_value=engine))
    identifier = uuid4()
    with connection.transaction(identifier) as active:
        assert active is conn
    statement, parameters = conn.execute.call_args.args
    assert "set_config('app.user_id', :uid, true)" in str(statement)
    assert parameters == {"uid": str(identifier)}


def test_installation_prepares_vector_and_schema_before_users(monkeypatch):
    conn = Mock()
    install = Mock()
    monkeypatch.setattr(bootstrap, "install_users", install)
    bootstrap.initialize(conn)
    assert "pg_advisory_xact_lock" in str(conn.execute.call_args_list[0].args[0])
    assert "CREATE SCHEMA IF NOT EXISTS app" in str(conn.execute.call_args_list[1].args[0])
    assert conn.exec_driver_sql.call_args_list[0].args == ("CREATE EXTENSION IF NOT EXISTS vector",)
    install.assert_called_once_with(conn)


def test_profile_query_uses_local_identity_and_excludes_password_hash():
    conn = Mock()
    get_current_user(conn)
    statement = conn.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "current_setting" in sql and "LIMIT" in sql
    assert "password_hash" not in sql
