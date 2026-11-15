from uuid import UUID

from app import stubs
from app.deps import error_responses, no_content, require_auth
from app.schemas import AskRequest, Conversation, Envelope, ListEnvelope, Message
from fastapi import APIRouter, Depends, Response

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
    summary="Crea una conversación y realiza una consulta al LLM",
)
async def create_conversation(courseId: UUID, body: AskRequest):
    return stubs.conversation_response()


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
