import asyncio
import logging
import os
from datetime import datetime, timezone
from functools import partial
from uuid import UUID, uuid4

import requests
from app import stubs
from app.deps import error_responses, no_content, require_auth
from app.schemas import (
    AskRequest,
    Conversation,
    Envelope,
    ListEnvelope,
    Message,
)
from fastapi import APIRouter, Depends, HTTPException, Response, status
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

AI_GATEWAY_URL = os.getenv("AI_GATEWAY_URL", "http://litellm:4000").rstrip("/")
AI_GATEWAY_API_KEY = os.getenv("AI_GATEWAY_API_KEY", "sk-cambia-esto")
AI_GATEWAY_TIMEOUT_SECONDS = os.getenv("AI_GATEWAY_TIMEOUT_SECONDS", 65)

# The current deployment runs a single API process. This prevents overlapping
# completions for the same authenticated user and releases the slot on errors.
_active_chat_users: set[str] = set()
_active_chat_users_lock = asyncio.Lock()

router = APIRouter(
    tags=["Conversations"],
    dependencies=[Depends(require_auth)],
    responses=error_responses(401, 422),
)


@router.get(
    "/courses/{courseId}/conversations",
    response_model=ListEnvelope[Conversation],
    summary="Lista las conversaciones del usuario en el curso",
)
async def list_conversations(courseId: UUID):
    return stubs.conversation_list()


@router.post(
    "/courses/{courseId}/conversations",
    status_code=201,
    response_model=Envelope[Conversation],
    responses=error_responses(429, 503),
    summary="Crea una conversación y realiza una consulta al LLM",
)
async def create_conversation(
    courseId: UUID,
    body: AskRequest,
    current_user: str = Depends(require_auth),
):
    current_user = current_user.casefold()
    async with _active_chat_users_lock:
        if current_user in _active_chat_users:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Ya hay una solicitud del tutor en curso para este usuario.",
            )
        _active_chat_users.add(current_user)

    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {AI_GATEWAY_API_KEY}",
        }
        payload = {
            "model": "chat-principal",
            "messages": [{"role": "user", "content": body.question}],
        }

        try:
            response = await run_in_threadpool(
                partial(
                    requests.post,
                    f"{AI_GATEWAY_URL}/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=AI_GATEWAY_TIMEOUT_SECONDS,
                )
            )
            response.raise_for_status()
            gateway_result = response.json()
            answer = gateway_result["choices"][0]["message"]["content"]
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("Gateway returned an empty completion")
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as error:
            logger.warning("AI gateway request failed: %s", type(error).__name__)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="El servicio del tutor no está disponible en este momento.",
            ) from error

        now = datetime.now(timezone.utc)
        conversation = Conversation(
            id=uuid4(),
            courseId=courseId,
            title=body.question[:80],
            messages=[
                Message(
                    id=uuid4(),
                    role="user",
                    content=body.question,
                    createdAt=now,
                    usedDocuments=False,
                    sourceDocuments=[],
                ),
                Message(
                    id=uuid4(),
                    role="assistant",
                    content=answer,
                    createdAt=now,
                    usedDocuments=False,
                    sourceDocuments=[],
                ),
            ],
            createdAt=now,
            updatedAt=now,
        )
        return Envelope[Conversation](data=conversation)
    finally:
        async with _active_chat_users_lock:
            _active_chat_users.discard(current_user)


@router.get(
    "/courses/{courseId}/conversations/{conversationId}",
    response_model=Envelope[Conversation],
    summary="Retoma una conversación",
)
async def get_conversation(courseId: UUID, conversationId: UUID):
    return stubs.conversation_response()


@router.delete(
    "/courses/{courseId}/conversations/{conversationId}",
    status_code=204,
    response_class=Response,
    summary="Elimina una conversación",
)
async def delete_conversation(courseId: UUID, conversationId: UUID):
    return no_content()


@router.post(
    "/courses/{courseId}/conversations/{conversationId}/messages",
    status_code=201,
    response_model=Envelope[Message],
    summary="Envía una pregunta a una conversación existente",
)
async def send_message(courseId: UUID, conversationId: UUID, body: AskRequest):
    return stubs.message_response()
