"""Modelos Pydantic que reflejan los schemas de ``contract/openapi.yaml``.

Los envoltorios ``{"data": ...}`` se modelan con genéricos
(``Envelope``, ``ListEnvelope``, ``PagedEnvelope``).
"""

from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator, EmailStr

# --------------------------------------------------------------------------
# Tipos reutilizables
# --------------------------------------------------------------------------
Password = Annotated[str, Field(min_length=8, json_schema_extra={"format": "password"})]
Uri = Annotated[str, Field(json_schema_extra={"format": "uri"})]

UserRole = Literal["student", "teacher", "admin"]
MemberRole = Literal["owner", "admin", "editor", "member"]
AssignableMemberRole = Literal["admin", "editor", "member"]
InvitationStatus = Literal["pending", "accepted", "rejected", "expired"]
InvitationDecision = Literal["accept", "reject"]
DocumentProcessingStatus = Literal["queued", "processing", "ready", "failed"]
MessageRole = Literal["user", "assistant"]
ExamStatus = Literal["upcoming", "available", "completed"]
QuestionType = Literal["alternatives", "multiple", "development", "true-false"]
ActivityType = Literal[
    "login",
    "logout",
    "course",
    "invitation",
    "document",
    "llm_query",
    "exam",
    "profile",
]

T = TypeVar("T")


class AtLeastOneField(BaseModel):
    """Base para PATCH: el contrato exige ``minProperties: 1``."""

    model_config = ConfigDict(json_schema_extra={"minProperties": 1})

    @model_validator(mode="after")
    def _require_at_least_one_field(self):
        if not self.model_fields_set:
            raise ValueError("Se requiere al menos un campo.")
        return self


# --------------------------------------------------------------------------
# Envoltorios genéricos
# --------------------------------------------------------------------------
class Envelope(BaseModel, Generic[T]):
    data: T


class ListEnvelope(BaseModel, Generic[T]):
    data: list[T]


class Pagination(BaseModel):
    page: int | None = Field(default=None, ge=1)
    pageSize: int | None = Field(default=None, ge=1)
    total: int | None = Field(default=None, ge=0)


class PagedEnvelope(BaseModel, Generic[T]):
    data: list[T]
    meta: Pagination | None = None


# --------------------------------------------------------------------------
# Errores y salud
# --------------------------------------------------------------------------
class ErrorResponse(BaseModel):
    error: str
    message: str | None = None
    details: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    timestamp: datetime
    version: str | None = None
    services: dict[str, Literal["up", "down", "degraded"]]


# --------------------------------------------------------------------------
# Auth y usuarios
# --------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: Password
    displayName: str = Field(min_length=1, max_length=100)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(json_schema_extra={"format": "password"})


class User(BaseModel):
    id: UUID
    email: EmailStr
    displayName: str
    role: UserRole
    avatarUrl: Uri | None = None
    createdAt: datetime


class AuthData(BaseModel):
    accessToken: str
    tokenType: Literal["Bearer"]
    expiresIn: int | None = None
    user: User


class UpdateUserRequest(AtLeastOneField):
    displayName: str | None = Field(default=None, min_length=1, max_length=100)
    email: EmailStr | None = None
    avatarUrl: Uri | None = None
    password: Password | None = None


# --------------------------------------------------------------------------
# Cursos, miembros e invitaciones
# --------------------------------------------------------------------------
class Member(BaseModel):
    userId: UUID
    displayName: str
    role: MemberRole
    joinedAt: datetime


class Course(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    objectives: str | None = None
    subject: str
    progress: float = Field(ge=0, le=100)
    color: str
    icon: str
    nextTopic: str
    lastActivity: str
    ownerId: UUID | None = None
    members: list[Member] | None = None


class CreateCourseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str
    objectives: str
    subject: str | None = None
    color: str | None = None
    icon: str | None = None


class UpdateCourseRequest(AtLeastOneField):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    objectives: str | None = None
    color: str | None = None
    icon: str | None = None


class UpdateMemberRequest(BaseModel):
    role: AssignableMemberRole


class InviteRequest(BaseModel):
    email: EmailStr


class Invitation(BaseModel):
    id: UUID
    courseId: UUID
    courseName: str
    email: EmailStr
    status: InvitationStatus
    createdAt: datetime


class InvitationDecisionRequest(BaseModel):
    decision: InvitationDecision


# --------------------------------------------------------------------------
# Documentos
# --------------------------------------------------------------------------
class Document(BaseModel):
    id: UUID
    courseId: UUID
    name: str
    size: int = Field(ge=0)
    mimeType: Literal["text/plain", "text/markdown"]
    status: DocumentProcessingStatus
    errorMessage: str | None = None
    uploadedBy: Member
    uploadedAt: datetime
    processedAt: datetime | None = None


class DocumentStatusData(BaseModel):
    documentId: UUID
    status: DocumentProcessingStatus
    progress: int | None = Field(default=None, ge=0, le=100)
    errorMessage: str | None = None


class SourceDocument(BaseModel):
    documentId: UUID
    name: str
    relevance: float | None = Field(default=None, ge=0, le=1)


# --------------------------------------------------------------------------
# Conversaciones
# --------------------------------------------------------------------------
class Message(BaseModel):
    id: UUID
    role: MessageRole
    content: str
    createdAt: datetime
    usedDocuments: bool | None = None
    sourceDocuments: list[SourceDocument] | None = None


class Conversation(BaseModel):
    id: UUID
    courseId: UUID
    title: str
    messages: list[Message]
    createdAt: datetime
    updatedAt: datetime


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    includeDocuments: bool = True


# --------------------------------------------------------------------------
# Exámenes
# --------------------------------------------------------------------------
class ExamSummary(BaseModel):
    id: UUID
    courseId: UUID
    title: str
    type: str | None = None
    topics: list[str]
    scheduledAt: datetime | None = None
    durationMinutes: int = Field(ge=1)
    questionCount: int = Field(ge=1)
    status: ExamStatus
    score: Annotated[float, Field(ge=0, le=100)] | None = None


class ExamQuestion(BaseModel):
    id: UUID
    type: QuestionType
    topic: str
    prompt: str
    options: list[str] | None = None


class Exam(BaseModel):
    id: UUID
    courseId: UUID
    title: str
    durationMinutes: int = Field(ge=1)
    questions: list[ExamQuestion]


class GenerateExamRequest(BaseModel):
    topic: str = Field(min_length=1)
    questionCount: int = Field(ge=1, le=50)
    durationMinutes: int = Field(default=30, ge=1)
    questionTypes: list[QuestionType] | None = None


class SubmittedAnswer(BaseModel):
    questionId: UUID
    answer: str


class SubmitExamRequest(BaseModel):
    answers: list[SubmittedAnswer]


class ExamAnswerResult(BaseModel):
    questionId: UUID
    answer: str
    correctAnswer: str | None = None
    correct: bool
    explanation: str | None = None
    usedDocuments: bool | None = None
    sourceDocuments: list[SourceDocument] | None = None


class ExamResult(BaseModel):
    examId: UUID
    score: float = Field(ge=0, le=100)
    answers: list[ExamAnswerResult]


# --------------------------------------------------------------------------
# Aprendizaje, notificaciones y actividad
# --------------------------------------------------------------------------
class GenerateMaterial(BaseModel):
    # El nombre replica el del contrato (``GenerateMaterial``).
    id: UUID
    courseId: UUID
    title: str
    type: str
    content: str | None = None
    createdAt: datetime


class CourseProgress(BaseModel):
    courseId: UUID
    progress: float = Field(ge=0, le=100)
    masteredConcepts: int = Field(ge=0)
    masteredConceptsChange: float | None = None
    nextTopic: str | None = None


class KnowledgeGraphData(BaseModel):
    """Grafo serializado al estilo Graphology: ``{attributes, nodes, edges}``."""

    attributes: dict[str, Any] | None = None
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class Notification(BaseModel):
    id: UUID
    courseId: UUID | None = None
    type: str
    title: str
    message: str
    read: bool
    createdAt: datetime


class Activity(BaseModel):
    id: UUID
    type: str
    description: str
    courseId: UUID | None = None
    metadata: dict[str, Any] | None = None
    createdAt: datetime


class DashboardSummaryData(BaseModel):
    studyHours: float = Field(ge=0)
    studyHoursPeriod: str
    studyHoursByDay: list[Annotated[float, Field(ge=0)]] = Field(min_length=7, max_length=7)
    streakDays: int = Field(ge=0)
    masteredConcepts: int = Field(ge=0)
    masteredConceptsChange: float | None = None
    roadmapStep: int = Field(ge=0)
    roadmapTotal: int = Field(ge=0)
    nextSessionMinutes: int = Field(ge=0)
    nextCourseId: UUID | None = None
