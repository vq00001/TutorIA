from contextlib import nullcontext

from fastapi.testclient import TestClient
from sqlalchemy import insert

from backend.api import app as http_api
from backend.db.sessions import issue_session
from backend.api.routes import dependencies
from db.courses import create_course, memberships
from db.generated_materials import GeneratedMaterial, store_generated_materials
from .test_courses import _as_user, _user
from .test_exams import _generation


def test_study_sessions_and_generated_materials_are_private(postgres, monkeypatch):
    owner, peer = _user(postgres), _user(postgres)
    tokens = {user: issue_session(postgres, user) for user in (owner, peer)}
    _as_user(postgres, owner)
    course = create_course(postgres, name="Estudio")
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(insert(memberships).values(course_id=course, user_id=peer, role="student"))
    _as_user(postgres, owner)
    generation = _generation(postgres, course, kind="chat")
    summary = GeneratedMaterial("Resumen", "summary", {"text": "Una síntesis"})
    explanation = GeneratedMaterial("Explicación", "explanation", {"text": "Un ejemplo"})
    store_generated_materials(postgres, course, generation, [summary, explanation])
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))

    client = TestClient(http_api.app)
    base = f"/api/v1/courses/{course}"
    own = {"Authorization": f"Bearer {tokens[owner]}"}
    other = {"Authorization": f"Bearer {tokens[peer]}"}
    sessions = base + "/study-sessions"
    started = client.post(sessions, headers=own)
    assert started.status_code == 200
    opened = started.json()["data"]
    assert opened["endedAt"] is None and opened["activeSeconds"] == 0
    assert client.post(sessions, headers=own).json() == started.json()
    session = f"{sessions}/{opened['id']}"
    assert client.post(session + "/heartbeat", headers=own).status_code == 200
    finished = client.post(session + "/finish", headers=own)
    assert finished.status_code == 200
    assert finished.json()["data"]["endedAt"] is not None
    assert client.post(session + "/finish", headers=own).json() == finished.json()
    assert client.get(session, headers=own).json() == finished.json()
    assert client.get(sessions, headers=own).json() == {"data": [finished.json()["data"]]}
    assert client.get(sessions, headers=other).json() == {"data": []}
    assert client.post(session + "/heartbeat", headers=other).status_code == 404

    materials = base + "/materials"
    page = client.get(materials, params={"limit": 1}, headers=own).json()
    second = client.get(materials, params={"limit": 1, **page["nextCursor"]}, headers=own).json()
    rows = page["data"] + second["data"]
    assert {row["id"] for row in rows} == {str(summary.id), str(explanation.id)}
    assert all("content" not in row for row in rows)
    assert second["nextCursor"] is None
    material = f"{materials}/{summary.id}"
    assert client.get(material, headers=own).json()["data"]["content"] == summary.content
    assert client.get(materials, headers=other).json()["data"] == []
    assert client.get(material, headers=other).status_code == 404
