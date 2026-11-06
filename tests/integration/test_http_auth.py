import base64
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.api.main import app
from tests.integration.test_materials import configure


def test_registration_verification_session_csrf_and_revocation(database, monkeypatch, tmp_path):
    admin, url = database
    configure(monkeypatch, url)
    space = uuid4()
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app.embedding_spaces(id,provider,model_name,model_revision,dimension) VALUES(:id,'ollama','synthetic','v1',3)"
            ),
            dict(id=space),
        )
    settings = {
        "APP_ORIGIN": "https://testserver",
        "SMTP_HOST": "mail-stub",
        "SMTP_FROM": "test@example.test",
        "MODEL_LLM": "synthetic",
        "MODEL_LLM_REVISION": "v1",
        "EMBEDDING_SPACE_ID": str(space),
        "LEDGER_KEY": base64.b64encode(b"x" * 32).decode(),
        "LEDGER_PATH": str(tmp_path),
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    sent = []
    monkeypatch.setattr(
        "backend.api.auth.send_token", lambda email, purpose, raw: sent.append((purpose, raw))
    )
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/api/v1/health").json() == {"status": "ok"}
        assert client.get("/api/v1/student/me").status_code == 401
        assert client.post("/api/v1/auth/register", json={}).status_code == 403
        csrf = client.get("/api/v1/auth/csrf").json()["data"]["csrfToken"]
        headers = {"origin": "https://testserver", "x-csrf-token": csrf}
        account = dict(
            email="A+synthetic@example.com",
            nickname="synthetic_user",
            displayName="Synthetic",
            password="synthetic passphrase 1234",
            acceptTerms=True,
            acknowledgeAI=True,
        )
        response = client.post("/api/v1/auth/register", json=account, headers=headers)
        assert response.status_code == 201, response.text
        assert len(sent) == 1
        response = client.post(
            "/api/v1/auth/verify-email", json={"token": sent[0][1]}, headers=headers
        )
        assert response.status_code == 200, response.text
        assert (
            client.post(
                "/api/v1/auth/verify-email", json={"token": sent[0][1]}, headers=headers
            ).status_code
            == 422
        )
        response = client.post(
            "/api/v1/auth/login",
            json={"email": account["email"], "password": account["password"]},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
        response = client.get("/api/v1/student/me")
        assert (
            response.status_code == 200
            and response.json()["data"]["email"] == "a+synthetic@example.com"
        )
        assert "password_hash" not in response.text
        headers["Idempotency-Key"] = str(uuid4())
        response = client.post("/api/v1/courses", json={"name": "HTTP synthetic"}, headers=headers)
        assert response.status_code == 201, response.text
        original = response.json()["data"]["id"]
        repeat = client.post("/api/v1/courses", json={"name": "HTTP synthetic"}, headers=headers)
        assert repeat.status_code == 201 and repeat.json()["data"]["id"] == original
        conflict = client.post("/api/v1/courses", json={"name": "Changed payload"}, headers=headers)
        assert conflict.status_code == 409
        assert client.get("/api/v1/courses").json()["data"][0]["id"] == original
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
        assert client.get("/api/v1/student/me").status_code == 401
