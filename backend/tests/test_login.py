from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest
from backend import http_api
from backend.routes import auth, dependencies
from fastapi.testclient import TestClient

LOGIN = "/api/v1/auth/login"
PASSWORD = "login-test-password"
PAYLOAD = {"email": "Persona@EXAMPLE.COM", "password": PASSWORD}


def _user(status):
    return {
        "id": uuid4(),
        "email": "persona@example.com",
        "nickname": "persona",
        "display_name": "Persona",
        "status": status,
        "created_at": datetime.now(timezone.utc),
        "password_hash": auth.password_hasher.hash(PASSWORD),
    }


@pytest.mark.parametrize("status", ["active", "pending"])
def test_login_returns_bearer_and_profile(monkeypatch, status):
    connection = Mock()
    user = _user(status)
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(connection))
    lookup = Mock(return_value=user)
    monkeypatch.setattr(auth, "get_login_credentials", lookup)
    issue = Mock(return_value="test-token")
    monkeypatch.setattr(auth, "issue_session", issue)

    response = TestClient(http_api.app).post(LOGIN, json=PAYLOAD)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    session = response.json()["data"]
    assert session["accessToken"] == "test-token"
    assert session["tokenType"] == "Bearer"
    assert session["expiresIn"] == 3600
    assert session["user"]["status"] == status
    assert set(session["user"]) == {"id", "email", "nickname", "displayName", "status", "createdAt"}
    lookup.assert_called_once_with(connection, "persona@example.com")
    issue.assert_called_once_with(connection, user["id"])


@pytest.mark.parametrize("account_exists", [True, False])
def test_login_does_not_disclose_whether_an_account_exists(monkeypatch, account_exists):
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(Mock()))
    user = _user("active") if account_exists else None
    monkeypatch.setattr(auth, "get_login_credentials", Mock(return_value=user))
    issue = Mock()
    monkeypatch.setattr(auth, "issue_session", issue)

    response = TestClient(http_api.app).post(LOGIN, json={**PAYLOAD, "password": "wrong-password"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"error": "INVALID_CREDENTIALS", "message": "Credenciales inválidas."}
    issue.assert_not_called()


def test_login_validation_does_not_echo_password(monkeypatch):
    transaction = Mock()
    monkeypatch.setattr(dependencies, "transaction", transaction)

    response = TestClient(http_api.app).post(LOGIN, json={**PAYLOAD, "email": "invalido"})

    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_LOGIN"
    assert PASSWORD not in response.text
    transaction.assert_not_called()
