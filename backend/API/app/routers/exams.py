from uuid import UUID

from app import stubs
from app.deps import error_responses, require_auth
from app.schemas import (
    Envelope,
    Exam,
    ExamResult,
    ExamSummary,
    GenerateExamRequest,
    ListEnvelope,
    SubmitExamRequest,
)
from fastapi import APIRouter, Depends

router = APIRouter(
    tags=["Exams"],
    dependencies=[Depends(require_auth)],
    responses=error_responses(401, 422),
)


@router.get(
    "/courses/{courseId}/exams",
    response_model=ListEnvelope[ExamSummary],
    summary="Lista exámenes pendientes y realizados",
)
async def list_exams(courseId: UUID):
    return stubs.exam_summary_list()


@router.post(
    "/courses/{courseId}/exams",
    status_code=201,
    response_model=Envelope[Exam],
    summary="Genera un examen sobre un tema",
)
async def generate_exam(courseId: UUID, body: GenerateExamRequest):
    return stubs.exam_response()


@router.get(
    "/courses/{courseId}/exams/{examId}",
    response_model=Envelope[Exam],
    summary="Obtiene las preguntas de un examen",
)
async def get_exam(courseId: UUID, examId: UUID):
    # Nunca incluir respuestas correctas aquí.
    return stubs.exam_response()


@router.post(
    "/courses/{courseId}/exams/{examId}/submissions",
    response_model=Envelope[ExamResult],
    summary="Envía y evalúa las respuestas de un examen",
)
async def submit_exam(courseId: UUID, examId: UUID, body: SubmitExamRequest):
    return stubs.exam_result_response()
