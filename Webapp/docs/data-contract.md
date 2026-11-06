# Contrato HTTP incorporado

Base `/api/v1`, mismo origen, cookie de sesión HttpOnly. La UI obtiene un token CSRF mediante `GET /auth/csrf` y lo devuelve en `X-CSRF-Token` para escrituras. Cada escritura incorpora `Idempotency-Key`; las ediciones versionadas añaden `If-Match`. No se guardan tokens de sesión ni respuestas de examen en localStorage.

La fuente ejecutable del contrato es `GET /openapi.json`. Las lecturas devuelven `{data: ...}`; los listados de cursos/materiales incluyen `meta.nextCursor`. Errores: `{error:{code,message,requestId}}`. No usar DTO de revisión para rendición.

## Flujos conectados

- Registro, verificación, login/logout y recuperación; perfil propio.
- Cursos, creación y metadatos; materiales reales multipart, estado, descarga y retiro asíncrono.
- Conversaciones privadas, envío asíncrono y consulta del estado final. SSE expone cambios de estado confirmado, no promete reproducción de cada token.
- Grafo aprobado, propuestas y aprobación; objetivos y ruta personal.
- Generación de práctica, inicio/recuperación de intento, guardado por UUID de opción y entrega. El servidor fija `deadline_at`; el temporizador del navegador es informativo. Las claves se devuelven solo para intentos graded/needs_review.
- Dominio y resumen derivados; heartbeat de actividad visible con interacción reciente.

`Course.myRole` es el rol de membresía. `StudentProfile.role` solo es presentación. Un coordinador no puede leer chats o prácticas de otras personas.

`MaterialDto.status` diferencia queued/running/ready/failed; `activeRunId` puede conservar una versión consultable mientras falla la actualización. Un DELETE aceptado significa retiro inmediato y purga pendiente, no borrado físico confirmado.

El adaptador `src/services/databaseApi.ts` ya utiliza HTTP. Los datos de `src/data/demoDatabase.ts` no se importan desde el adaptador. `src/data/exams.ts` llama al servidor y no contiene preguntas ni claves demo.

Quedan pendientes contratos tipados exhaustivos de respuesta, validación automática OpenAPI/frontend, componentes de todos los estados y pruebas E2E. La existencia de una ruta en OpenAPI no acredita su aceptación integral.
