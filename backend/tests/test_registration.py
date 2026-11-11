from contextlib import nullcontext
from unittest.mock import Mock
from uuid import UUID

import pytest
from backend import http_api
from fastapi.testclient import TestClient

REGISTER = "/api/v1/auth/register"
PAYLOAD = {
    "email": "Persona@EXAMPLE.COM",
    "password": "a-test-password",
    "displayName": "  Persona  ",
}


def test_register_returns_pending_account_without_credentials(monkeypatch):
    connection = Mock()
    transaction = Mock(return_value=nullcontext(connection))
    insert_user = Mock(return_value=True)
    monkeypatch.setattr(http_api, "transaction", transaction)
    monkeypatch.setattr(http_api, "insert_pending_user", insert_user)

    response = TestClient(http_api.app).post(REGISTER, json=PAYLOAD)

    assert response.status_code == 201
    data = response.json()["data"]
    identifier = UUID(data["id"])
    assert data == {
        "id": str(identifier),
        "nickname": f"u_{identifier.hex}",
        "email": "persona@example.com",
        "displayName": "Persona",
        "status": "pending",
    }
    transaction.assert_called_once_with()
    insert_user.assert_called_once()
    stored_hash = insert_user.call_args.kwargs["password_hash"]
    assert stored_hash.startswith("$argon2id$")
    assert http_api.password_hasher.verify(stored_hash, PAYLOAD["password"])


@pytest.mark.parametrize(
    "fields",
    [
        {"email": "invalido"},
        {"password": "secreto"},
        {"password": "x" * 129},
        {"displayName": "   "},
        {"displayName": "Persona\x00"},
    ],
)
def test_registration_rejects_invalid_input_without_echoing_password(monkeypatch, fields):
    transaction = Mock()
    monkeypatch.setattr(http_api, "transaction", transaction)
    payload = {**PAYLOAD, **fields}

    response = TestClient(http_api.app).post(REGISTER, json=payload)

    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_REGISTRATION"
    assert payload["password"] not in response.text
    transaction.assert_not_called()


def test_registration_conflict_returns_409(monkeypatch):
    monkeypatch.setattr(http_api, "transaction", lambda: nullcontext(Mock()))
    monkeypatch.setattr(http_api, "insert_pending_user", Mock(return_value=False))

    response = TestClient(http_api.app).post(REGISTER, json=PAYLOAD)

    assert response.status_code == 409
    assert response.json()["error"] == "REGISTRATION_CONFLICT"
