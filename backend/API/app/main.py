from app.errors import register_error_handlers
from app.routers import (
    activity,
    auth,
    conversations,
    courses,
    documents,
    exams,
    health,
    learning,
    notifications,
    users,
)
from fastapi import FastAPI

API_PREFIX = "/api/v1"

TAGS = [
    "Health",
    "Auth",
    "Users",
    "Courses",
    "Documents",
    "Conversations",
    "Exams",
    "Learning",
    "Notifications",
    "Activity",
]

app = FastAPI(
    title="TutorIA API",
    description="API para cursos, aprendizaje asistido por LLM y evaluación.",
    version="1.0.0",
    license_info={"name": "Proprietary"},
    openapi_tags=[{"name": t} for t in TAGS],
    # Usa el schema de validación tanto para entrada como salida
    separate_input_output_schemas=False,
)

register_error_handlers(app)

for module in (
    health,
    auth,
    users,
    learning,
    courses,
    documents,
    conversations,
    exams,
    notifications,
    activity,
):
    app.include_router(module.router, prefix=API_PREFIX)
