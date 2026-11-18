from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from backend.api import app as http_api
from backend.api.routes import conversations, dependencies
from backend.db.sessions import issue_session
from db.courses import create_course
from db.jobs import claim_job

from .test_courses import _as_user, _user
from .test_exams import _role


def test_direct_chat_returns_gateway_answer(monkeypatch):
    course_id = uuid4()
    config = SimpleNamespace(model_llm="qwen3:1.7b")
    completion = Mock(return_value="Respuesta de Qwen")
    monkeypatch.setattr(conversations, "get_member_course", lambda *_: SimpleNamespace(status="active"))
    monkeypatch.setattr(conversations.Config, "from_env", lambda: config)
    monkeypatch.setattr(conversations, "chat_completion", completion)

    response = conversations.conversations_direct(
        course_id, conversations.AskRequest(question="hola"), Mock()
    )

    assert response.data.messages[1].content == "Respuesta de Qwen"
    assert response.data.courseId == course_id
    assert config.model_llm == "qwen3:1.7b"
    completion.assert_called_once_with(config, "hola", timeout=180)


def test_chat_replay_history_and_quota_rollback(postgres, monkeypatch):
    owner, outsider = _user(postgres), _user(postgres)
    owner_token = issue_session(postgres, owner)
    outsider_token = issue_session(postgres, outsider)
    _as_user(postgres, owner)
    course = create_course(postgres, name="Chat")
    monkeypatch.setattr(dependencies, "transaction", lambda: nullcontext(postgres))
    client = TestClient(http_api.app)
    path = f"/api/v1/courses/{course}/conversations"
    headers = {"Authorization": f"Bearer {owner_token}", "Idempotency-Key": "first"}
    payload = {"question": "Primera pregunta", "includeDocuments": False}

    first = client.post(path, json=payload, headers=headers)
    assert first.status_code == 202
    accepted = first.json()["data"]
    assert client.post(path, json=payload, headers=headers).json() == first.json()
    assert client.post(path, json={**payload, "includeDocuments": True}, headers=headers).status_code == 409

    detail = f"{path}/{accepted['conversationId']}"
    followup = {"question": "Segunda pregunta"}
    second = client.post(detail + "/messages", json=followup, headers=headers)
    assert second.status_code == 202
    assert client.post(detail + "/messages", json=followup, headers=headers).json() == second.json()
    third = client.post(
        detail + "/messages", json={"question": "Sin cupo"},
        headers=headers | {"Idempotency-Key": "third"},
    )
    assert third.status_code == 429

    history = client.get(detail, headers=headers, params={"limit": 1}).json()
    assert [message["content"] for message in history["messages"]] == [payload["question"]]
    tail = client.get(detail, headers=headers, params={"afterSequence": history["nextAfterSequence"]})
    assert [message["content"] for message in tail.json()["messages"]] == [followup["question"]]
    assert client.get(path, headers=headers).json()["data"][0]["id"] == accepted["conversationId"]
    private_headers = {"Authorization": f"Bearer {outsider_token}", "Idempotency-Key": "first"}
    assert client.get(detail, headers=private_headers).status_code == 404
    assert client.post(detail + "/messages", json=followup, headers=private_headers).status_code == 404

    _role(postgres, None, worker=True)
    job = claim_job(postgres, "chat", kinds=["generate_chat"])
    assert job.target_id == UUID(accepted["generationId"])
    assert job.payload == {
        "conversation_id": accepted["conversationId"],
        "reply_id": accepted["messageId"],
        "include_documents": False,
    }
