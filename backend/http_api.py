"""Punto de entrada HTTP de TutorIA."""

from dotenv import load_dotenv
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .routes import auth, conversations, courses, documents, health, jobs
from .routes.dependencies import InvalidSession, error_response

load_dotenv()
app = FastAPI(title="TutorIA API")


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, _exc: RequestValidationError) -> JSONResponse:
    # El cuerpo y los valores rechazados pueden contener la contraseña.
    if _request.url.path.endswith("/login"):
        return JSONResponse(
            status_code=422,
            content={"error": "INVALID_LOGIN", "message": "Revisa los datos del inicio de sesión."},
        )
    if _request.url.path.endswith("/register"):
        return error_response(422, "INVALID_REGISTRATION", "Revisa los datos del registro.")
    return error_response(422, "INVALID_REQUEST", "Revisa los datos de la solicitud.")


@app.exception_handler(InvalidSession)
async def invalid_session(_request: Request, _error: InvalidSession) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        headers={"WWW-Authenticate": "Bearer"},
        content={"error": "INVALID_SESSION", "message": "Sesión inválida o caducada."},
    )


for router in (auth.router, conversations.router, courses.router, documents.router, health.router, jobs.router):
    app.include_router(router, prefix="/api/v1")
