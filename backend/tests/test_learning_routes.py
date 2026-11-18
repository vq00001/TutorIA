from contextlib import nullcontext

from fastapi.testclient import TestClient
from sqlalchemy import insert

from backend.api import app as http_api
from backend.api.routes import dependencies
from backend.db.sessions import issue_session
from db.courses import create_course, memberships

from .test_courses import _as_user, _user


def test_learning_graph_progress_and_private_roadmaps(postgres, monkeypatch):
    owner, student = _user(postgres), _user(postgres)
    tokens = {user: issue_session(postgres, user) for user in (owner, student)}
    _as_user(postgres, owner)
    course = create_course(postgres, name="Aprendizaje")
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(insert(memberships).values(course_id=course, user_id=student, role="student"))
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    client = TestClient(http_api.app)
    base = f"/api/v1/courses/{course}"
    own = {"Authorization": f"Bearer {tokens[owner]}"}
    peer = {"Authorization": f"Bearer {tokens[student]}"}

    def concept(label):
        response = client.post(base + "/concepts", json={"label": label}, headers=own)
        assert response.status_code == 201
        return response.json()["data"]["id"]

    a, b = concept("Fracciones"), concept("Álgebra")
    assert client.post(base + "/concepts", json={"label": "Otro"}, headers=peer).status_code == 403
    alias = f"{base}/concepts/{a}/aliases"
    assert client.post(alias, json={"alias": "Quebrados"}, headers=own).status_code == 201
    assert client.get(alias, headers=peer).json()["data"][0]["normalizedAlias"] == "quebrados"
    assert (
        client.post(
            f"{base}/concepts/{b}/prerequisites", json={"prerequisiteId": a}, headers=own
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"{base}/concepts/{a}/prerequisites", json={"prerequisiteId": b}, headers=own
        ).status_code
        == 409
    )

    graph = client.get(base + "/knowledge-graph", headers=peer).json()["data"]
    assert {node["key"] for node in graph["nodes"]} == {a, b}
    assert [(edge["source"], edge["target"]) for edge in graph["edges"]] == [(a, b)]
    progress = client.get(base + "/progress", headers=peer).json()["data"]
    assert {row["conceptId"] for row in progress} == {a, b}
    assert all(row["evidenceCount"] == 0 for row in progress)

    goals = base + "/learning-goals"
    created = client.post(
        goals, json={"title": "Aprender álgebra", "conceptIds": [b]}, headers=peer
    )
    assert created.status_code == 201
    goal = created.json()["data"]["id"]
    assert client.get(goals, headers=peer).json()["data"][0]["conceptIds"] == [b]
    assert client.get(goals, headers=own).json()["data"] == []
    route = f"{goals}/{goal}/roadmaps"
    assert client.get(route, headers=own).status_code == 404
    built = client.post(route, headers=peer)
    assert built.status_code == 200
    assert client.post(route, headers=peer).json() == built.json()
    roadmap = built.json()["data"]["id"]
    assert client.get(route, headers=peer).json()["data"][0]["id"] == roadmap
    detail = client.get(f"{route}/{roadmap}", headers=peer).json()["data"]
    assert [step["conceptId"] for step in detail["steps"]] == [a, b]
    assert (
        client.patch(f"{goals}/{goal}", json={"status": "completed"}, headers=peer).status_code
        == 200
    )
    assert client.post(route, headers=peer).status_code == 409
