from contextlib import nullcontext
from uuid import UUID

from fastapi.testclient import TestClient

from backend.api import app as http_api
from backend.api.routes import dependencies
from backend.db.sessions import issue_session

from .test_courses import _user
from .test_job_integration import _upload


def test_course_routes_use_authenticated_postgres_membership(postgres, monkeypatch):
    owner, outsider, pending = _user(postgres), _user(postgres), _user(postgres, status="pending")
    tokens = {user: issue_session(postgres, user) for user in (owner, outsider, pending)}
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    client = TestClient(http_api.app)

    def headers(user):
        return {"Authorization": f"Bearer {tokens[user]}"}

    payload = {"name": " Álgebra ", "description": "", "objectives": ""}
    assert client.get("/api/v1/health").json()["services"] == {"postgresql": "ok"}
    assert client.get("/api/v1/courses").status_code == 401
    assert client.post("/api/v1/courses", json=payload, headers=headers(pending)).status_code == 403
    created = client.post("/api/v1/courses", json=payload, headers=headers(owner))
    assert created.status_code == 201
    course = created.json()["data"]
    assert (course["name"], course["myRole"], course["ownerId"]) == (
        "Álgebra", "coordinator", str(owner)
    )
    path = f"/api/v1/courses/{course['id']}"
    assert client.get(path, headers=headers(owner)).json()["data"] == course
    assert client.get(path + "/members", headers=headers(owner)).json()["data"] == course["members"]
    assert client.get(path, headers=headers(outsider)).status_code == 404
    invalid = client.get("/api/v1/courses?page=0", headers=headers(owner))
    assert invalid.status_code == 422
    assert invalid.json()["error"] == "INVALID_REQUEST"

    page = client.get("/api/v1/courses?page=2&pageSize=1", headers=headers(owner)).json()
    assert page == {"data": [], "meta": {"page": 2, "pageSize": 1, "total": 1}}
    assert client.get("/api/v1/courses", headers=headers(outsider)).json()["data"] == []

    dependencies.set_user_context(postgres, owner)
    result = _upload(postgres, UUID(course["id"]), "http-job")
    job_path = path + "/jobs/" + result["job_id"]
    assert client.get(job_path, headers=headers(owner)).json()["data"]["state"] == "queued"
    assert client.get(job_path, headers=headers(outsider)).status_code == 404
