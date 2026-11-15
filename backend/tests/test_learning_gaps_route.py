from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import requests
from fastapi.testclient import TestClient

from backend import http_api
from backend.routes import learning_gaps


def _request():
    return {
        "nodes": [
            {"label": "Integral doble", "description": "Integración sobre una región"},
            {"label": "Jacobiano", "description": "Cambio de variables"},
        ],
        "results": [{"ref": "C2", "correct": 2, "total": 10}],
    }


def _setup(monkeypatch):
    state = {"transaction_open": False, "course": SimpleNamespace(status="active")}

    @contextmanager
    def fake_transaction():
        state["transaction_open"] = True
        try:
            yield object()
        finally:
            state["transaction_open"] = False

    monkeypatch.setattr(learning_gaps, "transaction", fake_transaction)
    monkeypatch.setattr(learning_gaps, "get_session_user", lambda *_: uuid4())
    monkeypatch.setattr(learning_gaps, "set_user_context", lambda *_: None)
    monkeypatch.setattr(learning_gaps, "get_member_course", lambda *_: state["course"])
    return state, TestClient(http_api.app), f"/api/v1/courses/{uuid4()}/knowledge-graph/learning-gaps/preview", {
        "Authorization": f"Bearer {'A' * 43}"
    }


def test_member_can_preview_hypotheses_after_transaction_closes(monkeypatch):
    state, client, url, headers = _setup(monkeypatch)
    calls = []

    def fake_infer(nodes, scores, threshold):
        assert not state["transaction_open"]
        calls.append((nodes, scores, threshold))
        return {
            "weak": {"C2": {"correct": 2, "total": 10}},
            "new_nodes": [{"ref": "N1", "label": "Determinantes",
                           "description": "Álgebra lineal", "status": "hypothesis"}],
            "recommendations": [{"target_ref": "C2", "node_ref": "N1", "kind": "new",
                                 "reason": "Base posible", "status": "hypothesis"}],
            "discarded": 0,
        }

    monkeypatch.setattr(learning_gaps, "infer_gaps", fake_infer)
    assert client.post(url, json=_request()).status_code == 401
    response = client.post(url, json=_request(), headers=headers)
    assert response.status_code == 200
    assert calls[0][1:] == ({"C2": {"correct": 2, "total": 10}}, 0.4)
    assert response.json()["data"]["newNodes"][0]["label"] == "Determinantes"
    assert response.json()["data"]["recommendations"][0]["targetRef"] == "C2"
    assert response.json()["data"]["recommendations"][0]["status"] == "hypothesis"


def test_preview_rejects_invalid_scores_and_inactive_course(monkeypatch):
    state, client, url, headers = _setup(monkeypatch)
    monkeypatch.setattr(learning_gaps, "infer_gaps", lambda *_: (_ for _ in ()).throw(
        AssertionError("El modelo no debe recibir entradas inválidas")
    ))
    invalid = _request()
    invalid["results"] = [{"ref": "C3", "correct": 2, "total": 10}]
    assert client.post(url, json=invalid, headers=headers).status_code == 422
    invalid["results"] = [{"ref": "C2", "correct": 11, "total": 10}]
    assert client.post(url, json=invalid, headers=headers).status_code == 422
    state["course"] = SimpleNamespace(status="archived")
    assert client.post(url, json=_request(), headers=headers).status_code == 403


def test_preview_maps_model_connection_error(monkeypatch):
    _state, client, url, headers = _setup(monkeypatch)

    def fail(*_args):
        raise requests.exceptions.ConnectionError("internal detail")

    monkeypatch.setattr(learning_gaps, "infer_gaps", fail)
    response = client.post(url, json=_request(), headers=headers)
    assert response.status_code == 503
    assert "internal detail" not in response.text
