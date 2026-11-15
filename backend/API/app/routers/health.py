from app import stubs
from app.deps import error_responses
from app.schemas import HealthResponse
from fastapi import APIRouter

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Comprueba el estado del backend",
    responses=error_responses(503),
)
async def health():
    # TODO: comprobar dependencias reales (BD, vector store, LLM) y devolver
    # 503 con ErrorResponse si el backend no está disponible.
    return stubs.health()
