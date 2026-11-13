"""Respuestas por defecto (stubs) de cada endpoint.

Cada función pública devuelve un payload válido según el contrato.
Cuando se implemente la lógica real, se debera reemplazar la llamada
a estas funciones en el router correspondiente; el test
``test_stubs_match_contract`` dejara de ser necesario para ese endpoint,
pero los tests de ``test_responses_contract`` seguirán verificando que
la respuesta real cumpla el contrato.
"""

from datetime import datetime, timezone
from uuid import UUID

from app import schemas as s

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

USER_ID = UUID("00000000-0000-4000-8000-000000000001")
COURSE_ID = UUID("00000000-0000-4000-8000-000000000002")
DOCUMENT_ID = UUID("00000000-0000-4000-8000-000000000003")
CONVERSATION_ID = UUID("00000000-0000-4000-8000-000000000004")
MESSAGE_ID = UUID("00000000-0000-4000-8000-000000000005")
EXAM_ID = UUID("00000000-0000-4000-8000-000000000006")
QUESTION_ID = UUID("00000000-0000-4000-8000-000000000007")
INVITATION_ID = UUID("00000000-0000-4000-8000-000000000008")
NOTIFICATION_ID = UUID("00000000-0000-4000-8000-000000000009")
ACTIVITY_ID = UUID("00000000-0000-4000-8000-00000000000a")
MATERIAL_ID = UUID("00000000-0000-4000-8000-00000000000b")


# --------------------------------------------------------------------------
# Objetos de dominio de ejemplo (privados)
# --------------------------------------------------------------------------
def _user() -> s.User:
    return s.User(
        id=USER_ID,
        email="usuario@example.com",
        displayName="Usuario Demo",
        role="student",
        avatarUrl=None,
        createdAt=NOW,
    )


def _member() -> s.Member:
    return s.Member(
        userId=USER_ID, displayName="Usuario Demo", role="owner", joinedAt=NOW
    )


def _course() -> s.Course:
    return s.Course(
        id=COURSE_ID,
        name="Curso de ejemplo",
        description="Descripción de ejemplo",
        objectives="Objetivos de ejemplo",
        subject="General",
        progress=0,
        color="#4F46E5",
        icon="book",
        nextTopic="Introducción",
        lastActivity="Hace un momento",
        ownerId=USER_ID,
        members=[_member()],
    )


def _invitation() -> s.Invitation:
    return s.Invitation(
        id=INVITATION_ID,
        courseId=COURSE_ID,
        courseName="Curso de ejemplo",
        email="invitado@example.com",
        status="pending",
        createdAt=NOW,
    )


def _document() -> s.Document:
    return s.Document(
        id=DOCUMENT_ID,
        courseId=COURSE_ID,
        name="apuntes.md",
        size=0,
        mimeType="text/markdown",
        status="queued",
        errorMessage=None,
        uploadedBy=_member(),
        uploadedAt=NOW,
        processedAt=None,
    )


def _message() -> s.Message:
    return s.Message(
        id=MESSAGE_ID,
        role="assistant",
        content="Respuesta de ejemplo.",
        createdAt=NOW,
        usedDocuments=False,
        sourceDocuments=[],
    )


def _conversation() -> s.Conversation:
    return s.Conversation(
        id=CONVERSATION_ID,
        courseId=COURSE_ID,
        title="Conversación de ejemplo",
        messages=[_message()],
        createdAt=NOW,
        updatedAt=NOW,
    )


def _exam_summary() -> s.ExamSummary:
    return s.ExamSummary(
        id=EXAM_ID,
        courseId=COURSE_ID,
        title="Examen de ejemplo",
        type="generated",
        topics=["Introducción"],
        scheduledAt=None,
        durationMinutes=30,
        questionCount=1,
        status="available",
        score=None,
    )


def _exam() -> s.Exam:
    return s.Exam(
        id=EXAM_ID,
        courseId=COURSE_ID,
        title="Examen de ejemplo",
        durationMinutes=30,
        questions=[
            s.ExamQuestion(
                id=QUESTION_ID,
                type="alternatives",
                topic="Introducción",
                prompt="Pregunta de ejemplo",
                options=["Opción A", "Opción B"],
            )
        ],
    )


# --------------------------------------------------------------------------
# Respuestas por endpoint (públicas, una por cada schema de respuesta)
# --------------------------------------------------------------------------
def health() -> s.HealthResponse:
    return s.HealthResponse(
        status="ok",
        timestamp=datetime.now(timezone.utc),
        version="1.0.0",
        services={"api": "up"},
    )


def auth_response() -> s.Envelope[s.AuthData]:
    return s.Envelope[s.AuthData](
        data=s.AuthData(
            accessToken="stub-access-token",
            tokenType="Bearer",
            expiresIn=3600,
            user=_user(),
        )
    )


def user_response() -> s.Envelope[s.User]:
    return s.Envelope[s.User](data=_user())


def dashboard_summary() -> s.Envelope[s.DashboardSummaryData]:
    return s.Envelope[s.DashboardSummaryData](
        data=s.DashboardSummaryData(
            studyHours=0,
            studyHoursPeriod="semana",
            studyHoursByDay=[0, 0, 0, 0, 0, 0, 0],
            streakDays=0,
            masteredConcepts=0,
            masteredConceptsChange=0,
            roadmapStep=0,
            roadmapTotal=0,
            nextSessionMinutes=0,
            nextCourseId=None,
        )
    )


def course_list() -> s.PagedEnvelope[s.Course]:
    return s.PagedEnvelope[s.Course](
        data=[_course()], meta=s.Pagination(page=1, pageSize=20, total=1)
    )


def course_response() -> s.Envelope[s.Course]:
    return s.Envelope[s.Course](data=_course())


def member_list() -> s.ListEnvelope[s.Member]:
    return s.ListEnvelope[s.Member](data=[_member()])


def member_response() -> s.Envelope[s.Member]:
    return s.Envelope[s.Member](data=_member())


def invitation_response() -> s.Envelope[s.Invitation]:
    return s.Envelope[s.Invitation](data=_invitation())


def invitation_list() -> s.ListEnvelope[s.Invitation]:
    return s.ListEnvelope[s.Invitation](data=[_invitation()])


def document_list() -> s.ListEnvelope[s.Document]:
    return s.ListEnvelope[s.Document](data=[_document()])


def document_response() -> s.Envelope[s.Document]:
    return s.Envelope[s.Document](data=_document())


def document_status() -> s.Envelope[s.DocumentStatusData]:
    return s.Envelope[s.DocumentStatusData](
        data=s.DocumentStatusData(
            documentId=DOCUMENT_ID, status="queued", progress=0, errorMessage=None
        )
    )


def conversation_list() -> s.ListEnvelope[s.Conversation]:
    return s.ListEnvelope[s.Conversation](data=[_conversation()])


def conversation_response() -> s.Envelope[s.Conversation]:
    return s.Envelope[s.Conversation](data=_conversation())


def message_response() -> s.Envelope[s.Message]:
    return s.Envelope[s.Message](data=_message())


def exam_summary_list() -> s.ListEnvelope[s.ExamSummary]:
    return s.ListEnvelope[s.ExamSummary](data=[_exam_summary()])


def exam_response() -> s.Envelope[s.Exam]:
    return s.Envelope[s.Exam](data=_exam())


def exam_result_response() -> s.Envelope[s.ExamResult]:
    return s.Envelope[s.ExamResult](
        data=s.ExamResult(
            examId=EXAM_ID,
            score=0,
            answers=[
                s.ExamAnswerResult(
                    questionId=QUESTION_ID,
                    answer="Opción A",
                    correctAnswer="Opción B",
                    correct=False,
                    explanation="Explicación de ejemplo",
                    usedDocuments=False,
                    sourceDocuments=[],
                )
            ],
        )
    )


def progress_response() -> s.Envelope[s.CourseProgress]:
    return s.Envelope[s.CourseProgress](
        data=s.CourseProgress(
            courseId=COURSE_ID,
            progress=0,
            masteredConcepts=0,
            masteredConceptsChange=0,
            nextTopic="Introducción",
        )
    )


def knowledge_graph_response() -> s.Envelope[s.KnowledgeGraphData]:
    return s.Envelope[s.KnowledgeGraphData](
        data=s.KnowledgeGraphData(
            attributes={},
            nodes=[
                {"key": "n1", "attributes": {"label": "Concepto A"}},
                {"key": "n2", "attributes": {"label": "Concepto B"}},
            ],
            edges=[{"key": "e1", "source": "n1", "target": "n2", "attributes": {}}],
        )
    )


def materials_list() -> s.ListEnvelope[s.GenerateMaterial]:
    return s.ListEnvelope[s.GenerateMaterial](
        data=[
            s.GenerateMaterial(
                id=MATERIAL_ID,
                courseId=COURSE_ID,
                title="Resumen de ejemplo",
                type="summary",
                content="Contenido de ejemplo",
                createdAt=NOW,
            )
        ]
    )


def notification_list() -> s.ListEnvelope[s.Notification]:
    return s.ListEnvelope[s.Notification](
        data=[
            s.Notification(
                id=NOTIFICATION_ID,
                courseId=COURSE_ID,
                type="document_processed",
                title="Documento procesado",
                message="Tu documento ya está listo.",
                read=False,
                createdAt=NOW,
            )
        ]
    )


def activity_list() -> s.PagedEnvelope[s.Activity]:
    return s.PagedEnvelope[s.Activity](
        data=[
            s.Activity(
                id=ACTIVITY_ID,
                type="login",
                description="Inicio de sesión",
                courseId=None,
                metadata={},
                createdAt=NOW,
            )
        ],
        meta=s.Pagination(page=1, pageSize=20, total=1),
    )
