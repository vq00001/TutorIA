# Backend TutorIA

La aplicación HTTP se ejecuta con `uvicorn backend.api.main:app --host 0.0.0.0 --port 8000` desde la raíz del repositorio. El worker es `python -m backend.workers.main`; el scheduler es `python -m backend.workers.scheduler`.

Aplicar migraciones con `MIGRATION_DATABASE_URL` mediante `alembic upgrade head`. La API/worker usan `DATABASE_URL` con sus roles limitados; nunca el rol de migración. Toda transacción de usuario fija `app.user_id` con alcance local a esa transacción. No existe escritura SQL directa desde el navegador.

Consultar el README raíz para configuración obligatoria, modelos, correo local y pruebas. `/openapi.json` describe los endpoints efectivamente incorporados; `/docs` permite inspeccionarlos. El contrato de aceptación completo aún no está implementado ni validado.

La CLI `python -m backend.main preview <ruta>` se conserva para inspección offline. Los antiguos comandos de ingesta/búsqueda sin curso se rechazan: usar la API autenticada. Los experimentos históricos bajo `experiments/retrieval` utilizan Qdrant y quedan fuera del despliegue y de las dependencias activas.
