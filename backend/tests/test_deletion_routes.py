from contextlib import nullcontext
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from backend import http_api
from backend.routes import dependencies
from db.courses import create_course, memberships
from db.jobs import jobs
from db.purge import deletion_requests
from db.sessions import issue_session
from test_courses import _as_user, _user


def test_edit_and_delete_course_account_and_conversation(postgres, monkeypatch):
    owner, student = _user(postgres), _user(postgres)
    tokens = {actor: issue_session(postgres, actor) for actor in (owner, student)}
    _as_user(postgres, owner)
    course = create_course(postgres, name="Clase")
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(insert(memberships).values(course_id=course, user_id=student, role="student"))
    _as_user(postgres, owner)
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    client = TestClient(http_api.app)
    own = {"Authorization": f"Bearer {tokens[owner]}"}
    peer = {"Authorization": f"Bearer {tokens[student]}"}
    base = f"/api/v1/courses/{course}"

    version = client.get(base, headers=own).json()["data"]["rowVersion"]
    update = {"rowVersion": version, "name": "Clase actualizada"}
    assert client.patch(base, json=update, headers=peer).status_code == 403
    changed = client.patch(base, json=update, headers=own)
    assert changed.status_code == 200 and changed.json()["data"]["name"] == update["name"]
    assert client.patch(base, json=update, headers=own).status_code == 409
    assert client.delete(base, headers=peer).status_code == 403
    assert client.delete("/api/v1/users/me", headers=own).status_code == 409

    chat = client.post(
        base + "/conversations", json={"question": "Hola"},
        headers=peer | {"Idempotency-Key": "chat-delete"},
    )
    assert chat.status_code == 202
    data = chat.json()["data"]
    path = f"{base}/conversations/{data['conversationId']}"
    assert client.delete(path, headers=own).status_code == 404
    assert client.delete(path, headers=peer).status_code == 204
    assert client.get(path, headers=peer).status_code == 404
    postgres.exec_driver_sql("RESET ROLE")
    assert postgres.scalar(select(jobs.c.id).where(jobs.c.id == UUID(data["jobId"]))) is None
    _as_user(postgres, owner)

    course_request = client.delete(base, headers=own)
    assert course_request.status_code == 202
    assert client.get(base, headers=own).status_code == 404
    account_request = client.delete("/api/v1/users/me", headers=peer)
    assert account_request.status_code == 202
    assert client.get("/api/v1/users/me", headers=peer).status_code == 401
    postgres.exec_driver_sql("RESET ROLE")
    requests = postgres.execute(select(deletion_requests).where(
        deletion_requests.c.id.in_([
            UUID(course_request.json()["data"]["requestId"]),
            UUID(account_request.json()["data"]["requestId"]),
        ])
    )).mappings().all()
    assert {(row.scope, row.subject_id, row.completed_at) for row in requests} == {
        ("course", course, None), ("account", student, None),
    }
