from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from uuid import uuid4

import requests
from fastapi.testclient import TestClient
from sqlalchemy import insert

from backend.api import app as http_api
from backend.api.routes import learning_gaps
from backend.db.learning_catalog import create_concept
from backend.db.sessions import issue_session
from db.courses import create_course, memberships

from .test_courses import _as_user, _user


def _request(concept_id):
    return {"results": [{"conceptId": str(concept_id), "correct": 2, "total": 10}]}


def _setup(monkeypatch):
    concepts = [
        SimpleNamespace(
            id=uuid4(), label="Integral doble", description="Integración sobre una región"
        ),
        SimpleNamespace(id=uuid4(), label="Jacobiano", description="Cambio de variables"),
    ]
    state = {
        "transaction_open": False,
        "course": SimpleNamespace(status="active"),
        "concepts": concepts,
        "user_id": uuid4(),
    }

    @contextmanager
    def fake_transaction():
        state["transaction_open"] = True
        try:
            yield object()
        finally:
            state["transaction_open"] = False

    monkeypatch.setattr(learning_gaps, "transaction", fake_transaction)
    monkeypatch.setattr(learning_gaps, "get_session_user", lambda *_: state["user_id"])
    monkeypatch.setattr(learning_gaps, "set_user_context", lambda *_: None)
    monkeypatch.setattr(learning_gaps, "get_member_course", lambda *_: state["course"])
    monkeypatch.setattr(learning_gaps, "list_concepts", lambda *_: state["concepts"])
    return (
        state,
        TestClient(http_api.app),
        f"/api/v1/courses/{uuid4()}/knowledge-graph/learning-gaps/preview",
        {"Authorization": f"Bearer {'A' * 43}"},
    )


def test_preview_maps_real_ids_and_closes_transaction_before_model(monkeypatch):
    state, client, url, headers = _setup(monkeypatch)
    calls = []

    def fake_infer(nodes, scores, threshold):
        assert not state["transaction_open"]
        calls.append((nodes, scores, threshold))
        return {
            "weak": {"C2": {"correct": 2, "total": 10}},
            "new_nodes": [
                {
                    "ref": "N1",
                    "label": "Determinantes",
                    "description": "Álgebra lineal",
                    "status": "hypothesis",
                }
            ],
            "recommendations": [
                {
                    "target_ref": "C2",
                    "node_ref": "N1",
                    "kind": "new",
                    "reason": "Base posible",
                    "status": "hypothesis",
                },
                {
                    "target_ref": "C2",
                    "node_ref": "C1",
                    "kind": "existing",
                    "reason": "Repasar integrales",
                    "status": "hypothesis",
                },
            ],
            "discarded": 0,
        }

    monkeypatch.setattr(learning_gaps, "infer_gaps", fake_infer)
    assert client.post(url, json=_request(state["concepts"][1].id)).status_code == 401
    response = client.post(url, json=_request(state["concepts"][1].id), headers=headers)
    assert response.status_code == 200
    assert calls == [
        (
            [
                {"label": concept.label, "description": concept.description}
                for concept in state["concepts"]
            ],
            {"C2": {"correct": 2, "total": 10}},
            0.4,
        )
    ]
    data = response.json()["data"]
    assert data["userId"] == str(state["user_id"])
    assert data["weak"] == [{"conceptId": str(state["concepts"][1].id), "correct": 2, "total": 10}]
    assert data["newNodes"][0]["label"] == "Determinantes"
    assert data["recommendations"][0]["newNodeRef"] == "N1"
    assert data["recommendations"][0]["existingConceptId"] is None
    assert data["recommendations"][1]["existingConceptId"] == str(state["concepts"][0].id)
    assert data["recommendations"][1]["newNodeRef"] is None
    assert all(
        item["targetConceptId"] == str(state["concepts"][1].id) for item in data["recommendations"]
    )


def test_preview_rejects_unknown_or_invalid_scores_and_inactive_course(monkeypatch):
    state, client, url, headers = _setup(monkeypatch)
    monkeypatch.setattr(
        learning_gaps,
        "infer_gaps",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("El modelo no debe recibir entradas inválidas")
        ),
    )
    assert client.post(url, json=_request(uuid4()), headers=headers).status_code == 422
    duplicate = _request(state["concepts"][1].id)
    duplicate["results"] *= 2
    assert client.post(url, json=duplicate, headers=headers).status_code == 422
    invalid = _request(state["concepts"][1].id)
    invalid["results"][0]["correct"] = 11
    assert client.post(url, json=invalid, headers=headers).status_code == 422
    state["course"] = SimpleNamespace(status="archived")
    assert (
        client.post(url, json=_request(state["concepts"][1].id), headers=headers).status_code == 403
    )


def test_preview_maps_model_connection_error(monkeypatch):
    state, client, url, headers = _setup(monkeypatch)

    def fail(*_args):
        raise requests.exceptions.ConnectionError("internal detail")

    monkeypatch.setattr(learning_gaps, "infer_gaps", fail)
    response = client.post(url, json=_request(state["concepts"][1].id), headers=headers)
    assert response.status_code == 503
    assert "internal detail" not in response.text


def test_preview_reads_only_the_authenticated_members_course_graph(postgres, monkeypatch):
    owner, student, outsider = (_user(postgres) for _ in range(3))
    tokens = {user: issue_session(postgres, user) for user in (student, outsider)}
    _as_user(postgres, owner)
    course = create_course(postgres, name="Cálculo")
    first = create_concept(postgres, course, label="Integral doble", description="Integrar")
    second = create_concept(postgres, course, label="Jacobiano", description="Cambio de variables")
    other_course = create_course(postgres, name="Otro curso")
    foreign = create_concept(postgres, other_course, label="Topología")
    postgres.exec_driver_sql("RESET ROLE")
    postgres.execute(insert(memberships).values(course_id=course, user_id=student, role="student"))
    postgres.exec_driver_sql("SET LOCAL ROLE tutoria_api")
    monkeypatch.setattr(learning_gaps, "transaction", lambda: nullcontext(postgres))
    received = []

    def fake_infer(nodes, scores, threshold):
        received.append((nodes, scores, threshold))
        return {"weak": scores, "new_nodes": [], "recommendations": [], "discarded": 0}

    monkeypatch.setattr(learning_gaps, "infer_gaps", fake_infer)
    client = TestClient(http_api.app)
    url = f"/api/v1/courses/{course}/knowledge-graph/learning-gaps/preview"
    peer = {"Authorization": f"Bearer {tokens[student]}"}
    outside = {"Authorization": f"Bearer {tokens[outsider]}"}

    assert client.post(url, json=_request(first), headers=outside).status_code == 404
    assert client.post(url, json=_request(foreign), headers=peer).status_code == 422
    response = client.post(url, json=_request(second), headers=peer)
    assert response.status_code == 200
    assert response.json()["data"]["userId"] == str(student)
    assert response.json()["data"]["weak"] == [
        {"conceptId": str(second), "correct": 2, "total": 10}
    ]
    assert received == [
        (
            [
                {"label": "Integral doble", "description": "Integrar"},
                {"label": "Jacobiano", "description": "Cambio de variables"},
            ],
            {"C2": {"correct": 2, "total": 10}},
            0.4,
        )
    ]
