from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import requests
from fastapi.testclient import TestClient

from backend.api import app as http_api
from backend.api.routes import graph_preview


def test_preview_requires_coordinator_and_closes_transaction_before_model(monkeypatch):
    course_id = uuid4()
    token = "A" * 43
    state = {"open": False, "role": "coordinator", "status": "active", "calls": 0}

    @contextmanager
    def fake_transaction():
        state["open"] = True
        try:
            yield object()
        finally:
            state["open"] = False

    monkeypatch.setattr(graph_preview, "transaction", fake_transaction)
    monkeypatch.setattr(graph_preview, "get_session_user", lambda _connection, _token: uuid4())
    monkeypatch.setattr(graph_preview, "set_user_context", lambda *_: None)
    monkeypatch.setattr(
        graph_preview,
        "get_member_course",
        lambda *_: SimpleNamespace(my_role=state["role"], status=state["status"]),
    )
    monkeypatch.setattr(graph_preview.Config, "from_env", lambda: SimpleNamespace(model_llm="test"))

    def fake_generate(sources, _config, *, target_concepts, include_prerequisites, remaining):
        assert not state["open"]
        assert remaining() > 0
        assert [source.source_id for source in sources] == ["apunte-1"]
        assert target_concepts == 5
        assert include_prerequisites is True
        state["calls"] += 1
        return graph_preview.GraphPreview.model_validate(
            {
                "model": "test",
                "inputs": [{"sourceId": "apunte-1", "title": "Apunte", "fragmentCount": 1}],
                "parameters": {"targetConcepts": 5, "includePrerequisites": True},
                "stages": {"consolidation": "completed", "prerequisites": "completed"},
                "nodes": [
                    {
                        "ref": "N1",
                        "label": "Integral doble",
                        "description": "Definición",
                        "sources": [],
                    }
                ],
                "edges": [],
                "merges": [],
            }
        )

    monkeypatch.setattr(graph_preview, "generate_graph_preview", fake_generate)
    client = TestClient(http_api.app)
    url = f"/api/v1/courses/{course_id}/knowledge-graph/preview"
    body = {
        "sources": [{"sourceId": "apunte-1", "title": "Apunte", "text": "Integral doble"}],
        "targetConcepts": 5,
        "includePrerequisites": True,
    }
    headers = {"Authorization": f"Bearer {token}"}

    assert client.post(url, json=body).status_code == 401
    state["role"] = "student"
    assert client.post(url, json=body, headers=headers).status_code == 403
    state["role"] = "coordinator"
    state["status"] = "archived"
    assert client.post(url, json=body, headers=headers).status_code == 403
    assert state["calls"] == 0

    state["status"] = "active"
    response = client.post(url, json=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["nodes"][0]["label"] == "Integral doble"
    assert response.json()["data"]["parameters"] == {
        "targetConcepts": 5,
        "includePrerequisites": True,
    }
    assert state["calls"] == 1


def test_preview_rejects_large_or_duplicate_sources_before_generation(monkeypatch):
    monkeypatch.setattr(graph_preview, "transaction", lambda: None)
    client = TestClient(http_api.app)
    url = f"/api/v1/courses/{uuid4()}/knowledge-graph/preview"
    headers = {"Authorization": f"Bearer {'A' * 43}"}
    source = {"sourceId": "same", "title": "Apunte", "text": "Texto"}
    assert client.post(url, json={"sources": [source, source]}, headers=headers).status_code == 422
    oversized = {**source, "text": "x" * (graph_preview._MAX_TEXT_CHARS + 1)}
    assert client.post(url, json={"sources": [oversized]}, headers=headers).status_code == 422


def test_preview_maps_model_failures_without_exposing_text(monkeypatch):
    @contextmanager
    def fake_transaction():
        yield object()

    monkeypatch.setattr(graph_preview, "transaction", fake_transaction)
    monkeypatch.setattr(graph_preview, "get_session_user", lambda *_: uuid4())
    monkeypatch.setattr(graph_preview, "set_user_context", lambda *_: None)
    monkeypatch.setattr(
        graph_preview,
        "get_member_course",
        lambda *_: SimpleNamespace(my_role="coordinator", status="active"),
    )
    monkeypatch.setattr(graph_preview.Config, "from_env", lambda: SimpleNamespace(model_llm="test"))
    client = TestClient(http_api.app)
    url = f"/api/v1/courses/{uuid4()}/knowledge-graph/preview"
    body = {"sources": [{"sourceId": "x", "title": "x", "text": "material privado"}]}
    headers = {"Authorization": f"Bearer {'A' * 43}"}

    for error, expected_status in [
        (requests.exceptions.ConnectionError("material privado"), 503),
        (requests.exceptions.Timeout("material privado"), 504),
        (ValueError("material privado"), 502),
    ]:

        def fail(*_args, error=error, **_kwargs):
            raise error

        monkeypatch.setattr(graph_preview, "generate_graph_preview", fail)
        response = client.post(url, json=body, headers=headers)
        assert response.status_code == expected_status
        assert "material privado" not in response.text
