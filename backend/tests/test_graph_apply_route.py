from contextlib import nullcontext

from fastapi.testclient import TestClient
from sqlalchemy import insert

from backend.api import app as http_api
from backend.api.routes import dependencies
from backend.db.sessions import issue_session
from db.courses import create_course, memberships

from .test_courses import _as_user, _user


def _proposal(edges=None, labels=("Integral doble", "Jacobiano")):
    return {
        "model": "test-model",
        "inputs": [{"sourceId": "apunte", "title": "Apunte", "fragmentCount": 1}],
        "parameters": {"targetConcepts": None, "includePrerequisites": bool(edges)},
        "stages": {"consolidation": "skipped", "prerequisites": "completed" if edges else "skipped"},
        "nodes": [
            {"ref": f"N{i}", "label": label, "description": f"Descripción {i}", "sources": []}
            for i, label in enumerate(labels, 1)
        ],
        "edges": [
            {"sourceRef": source, "targetRef": target, "reason": "Necesario antes"}
            for source, target in (edges or [])
        ],
        "merges": [],
    }


def test_apply_persists_initial_graph_once_and_rejects_student(postgres, monkeypatch):
    owner, student = _user(postgres), _user(postgres)
    tokens = {user: issue_session(postgres, user) for user in (owner, student)}
    _as_user(postgres, owner)
    course = create_course(postgres, name="Cálculo")
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(insert(memberships).values(course_id=course, user_id=student, role="student"))
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    client = TestClient(http_api.app)
    base = f"/api/v1/courses/{course}/knowledge-graph"
    own = {"Authorization": f"Bearer {tokens[owner]}"}
    peer = {"Authorization": f"Bearer {tokens[student]}"}
    proposal = _proposal(edges=[("N2", "N1")])

    assert client.post(base + "/apply", json=proposal, headers=peer).status_code == 403
    response = client.post(base + "/apply", json=proposal, headers=own)
    assert response.status_code == 201
    ids = response.json()["data"]["conceptIdsByRef"]
    assert set(ids) == {"N1", "N2"}
    assert response.json()["data"]["edgeCount"] == 1
    graph = client.get(base, headers=own).json()["data"]
    assert {node["key"] for node in graph["nodes"]} == set(ids.values())
    assert [(edge["source"], edge["target"]) for edge in graph["edges"]] == [
        (ids["N2"], ids["N1"])
    ]
    assert client.post(base + "/apply", json=proposal, headers=own).status_code == 409
    assert len(client.get(base, headers=own).json()["data"]["nodes"]) == 2


def test_apply_rolls_back_all_nodes_when_edges_form_cycle(postgres, monkeypatch):
    owner = _user(postgres)
    token = issue_session(postgres, owner)
    _as_user(postgres, owner)
    course = create_course(postgres, name="Cálculo")
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    client = TestClient(http_api.app)
    base = f"/api/v1/courses/{course}/knowledge-graph"
    headers = {"Authorization": f"Bearer {token}"}

    cycle = _proposal(edges=[("N1", "N2"), ("N2", "N1")])
    assert client.post(base + "/apply", json=cycle, headers=headers).status_code == 409
    assert client.get(base, headers=headers).json()["data"] == {"nodes": [], "edges": []}

    invalid = _proposal(edges=[("N3", "N1")])
    assert client.post(base + "/apply", json=invalid, headers=headers).status_code == 422
    assert client.get(base, headers=headers).json()["data"] == {"nodes": [], "edges": []}
