import pytest


@pytest.fixture(scope="session")
def client():
    from app.main import app
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture(scope="session")
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}
