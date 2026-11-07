import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from backend.api import (
    artifacts,
    auth,
    chat,
    community,
    courses,
    dashboard,
    deletions,
    embeddings,
    exams,
    graph,
    learning,
    materials,
)
from backend.api.common import ApiError, csrf
from backend.api.serialization import utc_endpoint
from backend.db.connection import engine
from backend.services.documents import DocumentError


@asynccontextmanager
async def lifespan(app):
    for key in (
        "DATABASE_URL",
        "APP_ORIGIN",
        "SMTP_HOST",
        "SMTP_FROM",
        "MODEL_LLM",
        "MODEL_LLM_REVISION",
        "EMBEDDING_SPACE_ID",
        "LEDGER_KEY",
        "LEDGER_PATH",
    ):
        if not os.environ.get(key):
            raise RuntimeError(f"Missing mandatory setting: {key}")
    origin = urlsplit(os.environ["APP_ORIGIN"])
    if (
        origin.scheme not in ("http", "https")
        or not origin.netloc
        or origin.path not in ("", "/")
        or origin.query
        or origin.fragment
        or origin.username
    ):
        raise RuntimeError("APP_ORIGIN must be an HTTP(S) origin without a path")
    expected = ScriptDirectory.from_config(
        Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    ).get_current_head()
    with engine().connect() as conn:
        if (
            conn.execute(text("SELECT version_num FROM public.alembic_version")).scalar()
            != expected
        ):
            raise RuntimeError("Run Alembic migrations before starting the API")
        if not conn.execute(text("SELECT auth.restore_is_ready()")).scalar_one():
            raise RuntimeError("Restore verification must complete before serving requests")
    yield


app = FastAPI(title="TutorIA", version="1.1.0", lifespan=lifespan)


@app.middleware("http")
async def browser_security(request: Request, call_next):
    request.state.request_id = str(uuid4())
    try:
        csrf(request)
        response = await call_next(request)
    except ApiError as exc:
        return error_response(request, exc.code, exc.status, exc.message)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def error_response(request, code, status, message=None):
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message or code,
                "requestId": getattr(request.state, "request_id", str(uuid4())),
            }
        },
    )


@app.exception_handler(ApiError)
async def api_error(request, exc):
    return error_response(request, exc.code, exc.status, exc.message)


@app.exception_handler(DocumentError)
async def document_error(request, exc):
    return error_response(request, exc.code, exc.status)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return error_response(request, "INVALID_INPUT", 422)


@app.exception_handler(IntegrityError)
async def integrity_error(request, exc):
    message = getattr(getattr(exc.orig, "diag", None), "message_primary", "")
    code = next(
        (
            c
            for c in (
                "LAST_COORDINATOR",
                "DUPLICATE_DOCUMENT",
                "GRAPH_CYCLE",
                "ATTEMPT_CLOSED",
                "INVALID_GRADE",
                "INVALID_TRANSITION",
                "CONFIRMATION_REQUIRED",
                "GRADE_IN_PROGRESS",
                "GENERATION_QUOTA_EXCEEDED",
            )
            if c == message
        ),
        "CONSTRAINT_CONFLICT",
    )
    return error_response(request, code, 429 if code == "GENERATION_QUOTA_EXCEEDED" else 409)


@app.exception_handler(DBAPIError)
async def database_error(request, exc):
    if getattr(exc.orig, "sqlstate", None) == "42501":
        return error_response(request, "FORBIDDEN", 403)
    return error_response(request, "DATABASE_UNAVAILABLE", 503)


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    return error_response(request, "INTERNAL_ERROR", 500)


@app.get("/health")
@app.get("/api/v1/health")
def health():
    with engine().connect() as conn:
        conn.execute(text("SELECT 1"))
        if not conn.execute(text("SELECT auth.restore_is_ready()")).scalar_one():
            raise ApiError("RESTORE_NOT_VERIFIED", 503)
    return {"status": "ok"}


for router in (
    artifacts.router,
    community.router,
    auth.router,
    courses.router,
    materials.router,
    chat.router,
    graph.router,
    exams.router,
    learning.router,
    dashboard.router,
    deletions.router,
    embeddings.router,
):
    for route in router.routes:
        if isinstance(route, APIRoute):
            route.endpoint = utc_endpoint(route.endpoint)
    app.include_router(router, prefix="/api/v1")
