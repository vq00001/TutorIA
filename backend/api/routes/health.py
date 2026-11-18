"""Comprobación de disponibilidad de PostgreSQL."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from . import dependencies
from .dependencies import ErrorResponse, error_response

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    timestamp: datetime
    services: dict[str, str]


@router.get("/health", response_model=HealthResponse,
         responses={503: {"model": ErrorResponse}})
def health() -> HealthResponse | JSONResponse:
    try:
        with dependencies.transaction() as connection:
            timestamp = connection.execute(select(func.clock_timestamp())).scalar_one()
    except (SQLAlchemyError, RuntimeError):
        return error_response(503, "DATABASE_UNAVAILABLE", "Base de datos no disponible.")
    return HealthResponse(status="ok", timestamp=timestamp, services={"postgresql": "ok"})
