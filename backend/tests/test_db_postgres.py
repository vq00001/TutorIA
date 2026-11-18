"""Integration tests for a local initialized database. Seed rows are rolled back."""

from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from backend.api.routes import auth, dependencies
from db.users import get_current_user, users


def test_postgres_has_vector_and_restricted_application_roles(postgres):
    assert postgres.scalar(text("SELECT EXISTS(SELECT FROM pg_extension WHERE extname='vector')"))
    roles = postgres.execute(
        text(
            """SELECT rolname, rolsuper, rolbypassrls
        FROM pg_roles WHERE rolname IN ('tutoria_api', 'tutoria_worker')"""
        )
    ).all()
    assert {row.rolname for row in roles} == {"tutoria_api", "tutoria_worker"}
    assert all(not row.rolsuper and not row.rolbypassrls for row in roles)


def _seed_user(conn, nickname=None):
    identifier = uuid4()
    conn.execute(
        insert(users).values(
            id=identifier,
            nickname=nickname or "u_" + identifier.hex,
            display_name="Synthetic",
            email=identifier.hex + "@example.test",
            password_hash="test-only",
        )
    )
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


def test_register_persists_pending_account_through_the_api_role(postgres, monkeypatch):
    from contextlib import nullcontext

    from fastapi.testclient import TestClient

    from backend.api import app as http_api

    client = TestClient(http_api.app)
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    payload = {
        "email": uuid4().hex + "@example.com",
        "password": "registration-password",
        "displayName": "Usuario nuevo",
    }

    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    first = client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201
    duplicate = client.post(
        "/api/v1/auth/register", json={**payload, "email": payload["email"].upper()}
    )
    assert duplicate.status_code == 409
    assert get_current_user(postgres) is None

    with pytest.raises(ProgrammingError), postgres.begin_nested():
        postgres.execute(
            insert(users).values(
                id=uuid4(),
                nickname="u_" + uuid4().hex,
                display_name="Synthetic",
                email=uuid4().hex + "@example.com",
                password_hash="test-only",
                status="active",
            )
        )

    postgres.exec_driver_sql("RESET ROLE")
    stored = (
        postgres.execute(select(users).where(users.c.email == payload["email"])).mappings().one()
    )
    assert str(stored["id"]) == first.json()["data"]["id"]
    assert stored["status"] == "pending"
    assert stored["email_verified_at"] is None
    assert auth.password_hasher.verify(stored["password_hash"], payload["password"])


def test_login_persists_only_token_hash_and_enforces_role_permissions(postgres, monkeypatch):
    from contextlib import nullcontext
    from datetime import timedelta
    from hashlib import sha256

    from fastapi.testclient import TestClient

    from backend.api import app as http_api
    from backend.db.sessions import sessions

    client = TestClient(http_api.app)
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    payload = {
        "email": uuid4().hex + "@example.com",
        "password": "registration-password",
        "displayName": "Usuario nuevo",
    }
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    registration = client.post("/api/v1/auth/register", json=payload)
    assert registration.status_code == 201
    credentials = {"email": payload["email"], "password": payload["password"]}
    response = client.post("/api/v1/auth/login", json=credentials)
    assert response.status_code == 200
    session = response.json()["data"]
    assert session["user"]["id"] == registration.json()["data"]["id"]
    assert session["user"]["status"] == "pending"
    with pytest.raises(ProgrammingError), postgres.begin_nested():
        postgres.execute(select(sessions.c.token_hash))

    postgres.exec_driver_sql("RESET ROLE")
    token_hash = sha256(session["accessToken"].encode("ascii")).digest()
    record = (
        postgres.execute(select(sessions).where(sessions.c.token_hash == token_hash))
        .mappings()
        .one()
    )
    assert str(record["user_id"]) == session["user"]["id"]
    assert record["expires_at"] - record["created_at"] == timedelta(hours=1)

    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_worker")
    with pytest.raises(ProgrammingError), postgres.begin_nested():
        postgres.execute(select(func.app.login_credentials(payload["email"])))


@pytest.mark.parametrize("account_status", ["suspended", "deletion_pending"])
def test_disabled_account_cannot_log_in(postgres, monkeypatch, account_status):
    from contextlib import nullcontext

    from fastapi.testclient import TestClient
    from sqlalchemy import update

    from backend.api import app as http_api

    password = "registration-password"
    identifier = _seed_user(postgres)
    email = uuid4().hex + "@example.com"
    postgres.execute(
        update(users)
        .where(users.c.id == identifier)
        .values(
            status=account_status,
            email=email,
            password_hash=auth.password_hasher.hash(password),
        )
    )
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))

    response = TestClient(http_api.app).post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": password,
        },
    )

    assert response.status_code == 401
    assert response.json()["error"] == "INVALID_CREDENTIALS"


def test_session_isolates_profiles_and_revokes_only_the_presented_token(postgres, monkeypatch):
    from contextlib import nullcontext

    from fastapi.testclient import TestClient

    from backend.api import app as http_api
    from backend.db.sessions import issue_session

    first, second = _seed_user(postgres), _seed_user(postgres)
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    first_token, second_token = issue_session(postgres, first), issue_session(postgres, second)
    another_token = issue_session(postgres, first)
    client = TestClient(http_api.app)
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))

    def headers(token):
        return {"Authorization": f"Bearer {token}"}

    profile_url, logout_url = "/api/v1/users/me", "/api/v1/auth/logout"
    first_profile = client.get(profile_url, headers=headers(first_token))
    assert first_profile.status_code == 200
    assert first_profile.json()["data"]["id"] == str(first)
    assert client.get(profile_url, headers=headers(second_token)).json()["data"]["id"] == str(
        second
    )

    assert client.post(logout_url, headers=headers(first_token)).status_code == 204
    assert client.get(profile_url, headers=headers(first_token)).status_code == 401
    assert client.post(logout_url, headers=headers(first_token)).status_code == 401
    assert client.get(profile_url, headers=headers(another_token)).status_code == 200

    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_worker")
    for statement in (
        select(func.app.session_user(bytes(32))),
        select(func.app.revoke_session(bytes(32))),
    ):
        with pytest.raises(ProgrammingError), postgres.begin_nested():
            postgres.execute(statement)


@pytest.mark.parametrize("condition", ["expired", "suspended", "deletion_pending"])
def test_profile_rejects_sessions_that_are_no_longer_valid(postgres, monkeypatch, condition):
    from contextlib import nullcontext
    from datetime import timedelta

    from fastapi.testclient import TestClient
    from sqlalchemy import update

    from backend.api import app as http_api
    from backend.db.sessions import issue_session, sessions

    identifier = _seed_user(postgres)
    token = issue_session(postgres, identifier)
    if condition == "expired":
        postgres.execute(
            update(sessions)
            .where(sessions.c.user_id == identifier)
            .values(
                created_at=func.now() - timedelta(hours=2),
                expires_at=func.now() - timedelta(hours=1),
            )
        )
    else:
        postgres.execute(update(users).where(users.c.id == identifier).values(status=condition))

    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    response = TestClient(http_api.app).get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json()["error"] == "INVALID_SESSION"
