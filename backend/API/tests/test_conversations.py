import asyncio
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.routers import conversations
from app.schemas import AskRequest
from tests.contract_utils import SAMPLE_UUID


class FakeGatewayResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [
                {"message": {"role": "assistant", "content": "Respuesta de prueba"}}
            ]
        }


def test_create_conversation_wraps_gateway_answer(
    client, auth_headers, monkeypatch
):
    async def fake_run_in_threadpool(call):
        return FakeGatewayResponse()

    monkeypatch.setattr(conversations, "run_in_threadpool", fake_run_in_threadpool)

    response = client.post(
        f"/api/v1/courses/{SAMPLE_UUID}/conversations",
        json={"question": "¿Qué es una integral?"},
        headers=auth_headers,
    )

    assert response.status_code == 201, response.text
    payload = response.json()["data"]
    assert payload["courseId"] == SAMPLE_UUID
    assert [message["role"] for message in payload["messages"]] == [
        "user",
        "assistant",
    ]
    assert payload["messages"][1]["content"] == "Respuesta de prueba"


def test_rejects_overlapping_prompt_for_same_user(monkeypatch):
    async def scenario():
        gateway_started = asyncio.Event()
        release_gateway = asyncio.Event()

        async def blocked_gateway_call(call):
            question = call.keywords["json"]["messages"][0]["content"]
            if question == "Primera pregunta":
                gateway_started.set()
                await release_gateway.wait()
            return FakeGatewayResponse()

        monkeypatch.setattr(
            conversations, "run_in_threadpool", blocked_gateway_call
        )
        first_request = asyncio.create_task(
            conversations.create_conversation(
                UUID(SAMPLE_UUID),
                AskRequest(question="Primera pregunta"),
                "same-user@example.test",
            )
        )

        try:
            await asyncio.wait_for(gateway_started.wait(), timeout=3)
            with pytest.raises(HTTPException) as error:
                await conversations.create_conversation(
                    UUID(SAMPLE_UUID),
                    AskRequest(question="Segunda pregunta"),
                    "same-user@example.test",
                )
            assert error.value.status_code == 429

            other_user_response = await conversations.create_conversation(
                UUID(SAMPLE_UUID),
                AskRequest(question="Pregunta de otra persona"),
                "other-user@example.test",
            )
            assert other_user_response.data.messages[1].content == "Respuesta de prueba"
        finally:
            release_gateway.set()

        assert (await first_request).data.messages[1].content == "Respuesta de prueba"

    asyncio.run(scenario())
