from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api import app as http_api
from backend.api.routes import auth, dependencies

PROFILE = "/api/v1/users/me"
LOGOUT = "/api/v1/auth/logout"
TOKEN = "a" * 43
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.parametrize("path", [PROFILE, LOGOUT])
@pytest.mark.parametrize("authorization", [None, "Basic eA==", "Bearer short", "Bearer x.y.z"])
def test_protected_routes_reject_missing_or_invalid_bearer_without_database(
    monkeypatch,
    path,
    authorization,
):
    transaction = Mock()
    monkeypatch.setattr(dependencies, "transaction", transaction)
    headers = {"Authorization": authorization} if authorization else {}
    method = "GET" if path == PROFILE else "POST"

    response = TestClient(http_api.app).request(method, path, headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"] == "INVALID_SESSION"
    transaction.assert_not_called()


def test_profile_sets_user_context_before_reading(monkeypatch):
    connection = Mock()
    identifier = uuid4()
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(connection))
    lookup = Mock(return_value=identifier)
    monkeypatch.setattr(dependencies, "get_session_user", lookup)
    context = Mock()
    monkeypatch.setattr(dependencies, "set_user_context", context)

    def read_profile(conn):
        assert conn is connection
        context.assert_called_once_with(connection, identifier)
        return {
            "id": identifier,
            "nickname": "persona",
            "display_name": "Persona",
            "email": "persona@example.com",
            "status": "pending",
            "created_at": datetime.now(timezone.utc),
            "email_verified_at": None,
        }

    monkeypatch.setattr(auth, "get_current_user", read_profile)
    response = TestClient(http_api.app).get(PROFILE, headers=HEADERS)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["data"]["id"] == str(identifier)
    assert response.json()["data"]["emailVerifiedAt"] is None
    lookup.assert_called_once_with(connection, TOKEN)


@pytest.mark.parametrize("revoked,expected", [(True, 204), (False, 401)])
def test_logout_reports_whether_the_session_was_revoked(monkeypatch, revoked, expected):
    connection = Mock()
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(connection))
    revoke = Mock(return_value=revoked)
    monkeypatch.setattr(auth, "revoke_session", revoke)

    response = TestClient(http_api.app).post(LOGOUT, headers=HEADERS)

    assert response.status_code == expected
    if revoked:
        assert response.content == b""
    revoke.assert_called_once_with(connection, TOKEN)
