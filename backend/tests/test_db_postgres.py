"""Integration tests for a local initialized database. Seed rows are rolled back."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, insert, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from db.users import get_current_user, users


@pytest.fixture
def postgres():
    url = os.environ.get("TUTORIA_TEST_ADMIN_URL")
    if not url:
        pytest.skip("Set TUTORIA_TEST_ADMIN_URL to a local initialized PostgreSQL database")
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            try:
                yield connection
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def test_postgres_has_vector_and_restricted_application_roles(postgres):
    assert postgres.scalar(text("SELECT EXISTS(SELECT FROM pg_extension WHERE extname='vector')"))
    roles = postgres.execute(text(
        """SELECT rolname, rolsuper, rolbypassrls
        FROM pg_roles WHERE rolname IN ('tutoria_api', 'tutoria_worker')"""
    )).all()
    assert {row.rolname for row in roles} == {"tutoria_api", "tutoria_worker"}
    assert all(not row.rolsuper and not row.rolbypassrls for row in roles)


def _seed_user(conn, nickname=None):
    identifier = uuid4()
    conn.execute(insert(users).values(
        id=identifier, nickname=nickname or "u_" + identifier.hex,
        display_name="Synthetic", email=identifier.hex + "@example.test",
        password_hash="test-only",
    ))
    return identifier


def test_user_cannot_read_another_profile_or_any_password_hash(postgres):
    # Adapted from the earlier DAT_01_03 role and transaction-context test.
    first, second = _seed_user(postgres), _seed_user(postgres)
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    assert get_current_user(postgres) is None
    for identifier in (first, second):
        postgres.execute(select(func.set_config("app.user_id", str(identifier), True)))
        assert get_current_user(postgres)["id"] == identifier
        assert postgres.execute(select(users.c.id)).scalars().all() == [identifier]
    with pytest.raises(ProgrammingError), postgres.begin_nested():
        postgres.execute(select(users.c.password_hash))


def test_nickname_is_unique_regardless_of_case(postgres):
    nickname = "u_" + uuid4().hex
    _seed_user(postgres, nickname)
    with pytest.raises(IntegrityError), postgres.begin_nested():
        _seed_user(postgres, nickname.upper())
