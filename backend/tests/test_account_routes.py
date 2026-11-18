from contextlib import nullcontext

from fastapi.testclient import TestClient
from sqlalchemy import update

from backend.api import app as http_api
from backend.api.routes import account, dependencies
from db.sessions import issue_session
from db.users import users
from .test_courses import _user


def test_verification_and_password_reset(postgres, monkeypatch):
    user = _user(postgres, status="pending")
    email = f"{user.hex}@example.com"
    postgres.execute(update(users).where(users.c.id == user).values(email=email))
    session = issue_session(postgres, user)
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "no-reply@example.com")
    sent = []
    monkeypatch.setattr(account, "send_token", lambda *args: sent.append(args))
    client = TestClient(http_api.app)
    verify = "/api/v1/auth/email-verification"
    reset = "/api/v1/auth/password-reset"
    auth = {"Authorization": f"Bearer {session}"}

    unknown = client.post(verify + "/request", json={"email": "absent@example.com"})
    known = client.post(verify + "/request", json={"email": email})
    assert known.status_code == unknown.status_code == 202
    assert known.content == unknown.content == b""
    assert len(sent) == 1 and sent[0][2:4] == (email, "verify_email")
    verification = {"token": sent[0][-1]}
    assert client.post(verify + "/confirm", json=verification).status_code == 204
    assert client.post(verify + "/confirm", json=verification).status_code == 400
    assert client.get("/api/v1/users/me", headers=auth).json()["data"]["status"] == "active"

    assert client.post(reset + "/request", json={"email": email}).status_code == 202
    assert sent[-1][3] == "reset_password"
    recovery = {"token": sent[-1][-1], "password": "new-password-123"}
    assert client.post(reset + "/confirm", json=recovery).status_code == 204
    assert client.get("/api/v1/users/me", headers=auth).status_code == 401
    assert client.post(
        "/api/v1/auth/login", json={"email": email, "password": recovery["password"]}
    ).status_code == 200


def test_requests_require_mail_configuration(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)
    response = TestClient(http_api.app).post(
        "/api/v1/auth/password-reset/request", json={"email": "user@example.com"}
    )
    assert response.status_code == 503
