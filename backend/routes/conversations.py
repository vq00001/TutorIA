"""Conversaciones privadas y envío de mensajes a la cola."""

import os
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

import requests
from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse, Response
from pydantic import AwareDatetime, BaseModel, Field, field_validator
from sqlalchemy.exc import DBAPIError

from ..config import Config
from ..db.citations import list_message_citations
from ..db.conversations import (
    append_user_message,
    create_conversation,
    delete_own_conversation,
    get_conversation,
    list_conversations,
    list_messages,
)
from ..db.courses import get_member_course
from ..db.generations import start_generation
from ..db.idempotency import run_idempotent
from ..db.jobs import enqueue_job
from ..llm import chat_completion
from .dependencies import AuthenticatedConnection, ErrorResponse, error_response

router = APIRouter(prefix="/courses/{courseId}/conversations", tags=["Conversations"])
_ERRORS = {code: {"model": ErrorResponse} for code in (401, 404, 422)}
_SEND_ERRORS = {**_ERRORS, **{code: {"model": ErrorResponse} for code in (403, 409, 429)}}
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
Limit = Annotated[int, Query(ge=1, le=100)]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=20000)
    includeDocuments: bool = True

    @field_validator("question", mode="before")
    @classmethod
    def normalize_question(cls, value: object) -> object:
        if isinstance(value, str):
            if "\x00" in value:
                raise ValueError("Pregunta inválida")
            return value.strip()
        return value


class Conversation(BaseModel):
    id: UUID
    courseId: UUID
    title: str
    status: Literal["active", "closed"]
    createdAt: datetime
    updatedAt: datetime


class ConversationCursor(BaseModel):
    beforeUpdatedAt: datetime
    beforeId: UUID


class ConversationPage(BaseModel):
    data: list[Conversation]
    nextCursor: ConversationCursor | None


class Message(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    content: str
    sequence: int
    status: Literal["completed", "redacted"]
    inReplyTo: UUID | None
    generationId: UUID | None
    createdAt: datetime
    sourceDocuments: list[dict] = Field(default_factory=list)


class History(BaseModel):
    data: Conversation
    messages: list[Message]
    nextAfterSequence: int | None


class AcceptedMessage(BaseModel):
    conversationId: UUID
    messageId: UUID
    generationId: UUID
    jobId: UUID


class SendResponse(BaseModel):
    data: AcceptedMessage


class DirectMessage(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    content: str
    createdAt: datetime
    usedDocuments: bool = False
    sourceDocuments: list = Field(default_factory=list)


class DirectConversation(BaseModel):
    id: UUID
    courseId: UUID
    title: str
    messages: list[DirectMessage]
    createdAt: datetime
    updatedAt: datetime


class DirectResponse(BaseModel):
    data: DirectConversation


def _conversation(row) -> Conversation:
    return Conversation(
        id=row.id, courseId=row.course_id, title=row.title, status=row.status,
        createdAt=row.created_at, updatedAt=row.updated_at,
    )


def _message(connection, row) -> Message:
    content = "\n".join(
        part["text"] for part in row.parts
        if isinstance(part, dict) and part.get("type") == "text"
        and isinstance(part.get("text"), str)
    )
    return Message(
        id=row.id, role=row.role, content=content, sequence=row.sequence_no, status=row.status,
        inReplyTo=row.in_reply_to, generationId=row.generation_id, createdAt=row.created_at,
        sourceDocuments=[
            {
                "documentId": str(citation["source_document_id"]),
                "name": citation["source_name"],
                "relevance": citation["score"],
                "section": " > ".join(citation["section_path"]),
                "locator": citation["locator"],
                "snippet": citation["snippet"],
            }
            for citation in list_message_citations(connection, row.id)
        ],
    )


def _not_found() -> JSONResponse:
    return error_response(404, "CONVERSATION_NOT_FOUND", "Clase o conversación no encontrada.")


@router.post("/direct", response_model=DirectResponse, responses=_SEND_ERRORS)
def conversations_direct(
    courseId: UUID, payload: AskRequest, connection: AuthenticatedConnection,
) -> DirectResponse | JSONResponse:
    course = get_member_course(connection, courseId)
    if course is None:
        return _not_found()
    if course.status != "active":
        return error_response(403, "CHAT_NOT_ACTIVE", "La conversación no admite mensajes.")

    try:
        config = Config.from_env()
        config.model_llm = "chat-principal"
        answer = chat_completion(config, payload.question, timeout=180)
        if not answer:
            raise ValueError("EMPTY_MODEL_RESPONSE")
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return error_response(503, "TUTOR_UNAVAILABLE", "El servicio del tutor no está disponible en este momento.")

    now = datetime.now(timezone.utc)
    return DirectResponse(data=DirectConversation(
        id=uuid4(), courseId=courseId, title=payload.question[:80],
        messages=[
            DirectMessage(id=uuid4(), role="user", content=payload.question, createdAt=now),
            DirectMessage(id=uuid4(), role="assistant", content=answer, createdAt=now),
        ],
        createdAt=now, updatedAt=now,
    ))


@router.get("", response_model=ConversationPage, responses=_ERRORS)
def conversations_index(
    courseId: UUID, connection: AuthenticatedConnection, limit: Limit = 20,
    beforeUpdatedAt: AwareDatetime | None = None, beforeId: UUID | None = None,
) -> ConversationPage | JSONResponse:
    if (beforeUpdatedAt is None) != (beforeId is None):
        return error_response(422, "INVALID_CURSOR", "Envía ambos campos del cursor.")
    if get_member_course(connection, courseId) is None:
        return _not_found()
    before = (beforeUpdatedAt, beforeId) if beforeUpdatedAt is not None and beforeId is not None else None
    rows = list_conversations(connection, courseId, limit=limit, before=before, lookahead=True)
    page = rows[:limit]
    cursor = None
    if len(rows) > limit:
        last = page[-1]
        cursor = ConversationCursor(beforeUpdatedAt=last.updated_at, beforeId=last.id)
    return ConversationPage(data=[_conversation(row) for row in page], nextCursor=cursor)


@router.get("/{conversationId}", response_model=History, responses=_ERRORS)
def conversations_history(
    courseId: UUID, conversationId: UUID, connection: AuthenticatedConnection,
    limit: Limit = 50, afterSequence: Annotated[int, Query(ge=0, le=9223372036854775807)] = 0,
) -> History | JSONResponse:
    row = get_conversation(connection, courseId, conversationId)
    if row is None:
        return _not_found()
    messages = list_messages(
        connection, courseId, conversationId, limit=limit, after_sequence=afterSequence,
        lookahead=True,
    )
    page = messages[:limit]
    return History(
        data=_conversation(row), messages=[_message(connection, message) for message in page],
        nextAfterSequence=page[-1].sequence_no if len(messages) > limit else None,
    )


def _send(
    connection: AuthenticatedConnection, course_id: UUID, payload: AskRequest,
    key: str, conversation_id: UUID | None = None,
) -> SendResponse | JSONResponse:
    if not key.strip():
        return error_response(422, "INVALID_IDEMPOTENCY_KEY", "La clave no puede estar vacía.")
    course = get_member_course(connection, course_id)
    conversation = (
        get_conversation(connection, course_id, conversation_id)
        if conversation_id is not None else None
    )
    if course is None or (conversation_id is not None and conversation is None):
        return _not_found()
    if course.status != "active" or (conversation is not None and conversation.status != "active"):
        return error_response(403, "CHAT_NOT_ACTIVE", "La conversación no admite mensajes.")

    def enqueue(conn):
        target = conversation_id
        if target is None:
            title = " ".join(payload.question.split())[:255]
            target = create_conversation(conn, course_id, title)
        message = append_user_message(
            conn, course_id, target, [{"type": "text", "text": payload.question}]
        )
        generation = start_generation(
            conn, course_id, kind="chat", provider="ai_gateway",
            model_name=os.getenv("MODEL_LLM", "chat-principal"),
            model_revision=os.getenv("MODEL_LLM_REVISION", "unspecified"),
            prompt_version="v1",
        )
        job = enqueue_job(
            conn, course_id, kind="generate_chat", target_id=generation,
            idempotency_key=str(generation), deadline_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            payload={"conversation_id": str(target), "reply_id": str(message),
                     "include_documents": payload.includeDocuments},
        )
        return {"conversationId": str(target), "messageId": str(message),
                "generationId": str(generation), "jobId": str(job)}

    scope = f"chat:{course_id}:{conversation_id or 'new'}"
    try:
        result = run_idempotent(
            connection, scope=scope, key=key, payload=payload.model_dump(), operation=enqueue
        )
    except DBAPIError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        primary = getattr(getattr(exc.orig, "diag", None), "message_primary", None)
        if (sqlstate, primary) == ("23505", "IDEMPOTENCY_CONFLICT"):
            return error_response(409, primary, "La clave ya se usó con otra pregunta.")
        if (sqlstate, primary) == ("23514", "GENERATION_QUOTA_EXCEEDED"):
            return error_response(429, primary, "Se alcanzó la cuota de generación.")
        if sqlstate == "42501":
            return error_response(403, "CHAT_FORBIDDEN", "La cuenta no puede enviar el mensaje.")
        raise
    return SendResponse(data=AcceptedMessage(**result))


@router.post("", status_code=202, response_model=SendResponse, responses=_SEND_ERRORS)
def conversations_create(
    courseId: UUID, payload: AskRequest, connection: AuthenticatedConnection, key: Key
) -> SendResponse | JSONResponse:
    return _send(connection, courseId, payload, key)


@router.post("/{conversationId}/messages", status_code=202, response_model=SendResponse,
             responses=_SEND_ERRORS)
def conversations_send(
    courseId: UUID, conversationId: UUID, payload: AskRequest,
    connection: AuthenticatedConnection, key: Key,
) -> SendResponse | JSONResponse:
    return _send(connection, courseId, payload, key, conversationId)

@router.delete("/{conversationId}", status_code=204, responses=_ERRORS)
def conversations_delete(courseId: UUID, conversationId: UUID, connection: AuthenticatedConnection) -> Response:
    if not delete_own_conversation(connection, courseId, conversationId):
        return _not_found()
    return Response(status_code=204)
