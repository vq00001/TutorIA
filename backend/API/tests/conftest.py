import pytest
from app.auth import create_jwt


@pytest.fixture(scope="session")
def client():
    from app.main import app
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture(scope="session")
def auth_headers()  -> dict[str, str]:
    token = create_jwt(data={"sub": "test@example.com"})
    return {"Authorization": f"Bearer {token}"}