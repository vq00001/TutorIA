# TutorIA API (FastAPI)

Implementación del contrato `contract/openapi.yaml`. Todos los endpoints devuelven
respuestas por defecto (stubs en `app/stubs.py`); la lógica real queda por implementar.

- Prefijo de la API: `/api/v1`
- Documentación interactiva (Swagger): `http://localhost:8000/docs`
- Estado del backend: `GET /api/v1/health`

## Estructura

```
contract/openapi.yaml     contrato (fuente de verdad)
app/
  main.py                 app FastAPI, prefijo /api/v1
  schemas.py              modelos Pydantic (mismos nombres que el contrato)
  stubs.py                respuestas por defecto, una por schema de respuesta
  deps.py                 auth (stub), paginación, helpers de errores
  errors.py               errores → ErrorResponse
  routers/                un router por tag del contrato
tests/                    tests de conformidad con el contrato
Dockerfile                stages: dev (API) y test (pytest)
requirements.txt          dependencias de ejecución
requirements.test.txt     dependencias de ejecución + tests
pytest.ini                configuración de pytest (pythonpath y testpaths)
```

## Requisitos

- Python 3.12+ (la imagen Docker usa `python:3.12-alpine`)
- Docker (opcional)

## Ejecución local

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

La API queda en `http://localhost:8000`.

## Ejecución con Docker

Construir la imagen (stage `dev`):

```bash
docker build --target dev -t tutoria-api .
```

Levantar el contenedor:

```bash
docker run -d --name tutoria-api -p 8000:8000 tutoria-api
```

Comprobar que funciona:

```bash
curl http://localhost:8000/api/v1/health
docker ps                            # STATUS debe indicar "healthy"
```

Comandos útiles:

```bash
docker logs -f tutoria-api           # ver logs
docker stop tutoria-api              # detener
docker rm tutoria-api                # eliminar el contenedor
```

Desarrollo con recarga automática (los cambios en `app/` se reflejan sin reconstruir):

```bash
docker run --rm -p 8000:8000 -v "$(pwd)/app:/app/app" tutoria-api \
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Si el puerto 8000 está ocupado, cambia el primero: `-p 8080:8000`.

## Autenticación

Los endpoints, salvo `/health`, `/auth/register` y `/auth/login`, exigen el header:

```
Authorization: Bearer <token>
```

Ejemplo:

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "user@example.com", "password": "Passw0rd!123"}'

curl http://localhost:8000/api/v1/users/me \
  -H 'Authorization: Bearer stub-access-token'
```

> [!NOTE]
> Por ahora `require_auth` solo comprueba que exista un header `Bearer` con un token no
> vacío, y `login` no verifica credenciales.

## Formato de respuestas

- Éxito: `{"data": ...}`. Los listados paginados (`/courses`, `/activity`) añaden
  `"meta": {"page", "pageSize", "total"}`.
- Error: `{"error": "<código>", "message": "...", "details": {...}}`.

| Status | `error` |
|---|---|
| 401 | `unauthorized` |
| 403 | `forbidden` |
| 404 | `not_found` |
| 409 | `conflict` |
| 415 | `unsupported_media_type` |
| 422 | `validation_error` (con `details.errors`) |
| 503 | `service_unavailable` |

Paginación: `page` (≥ 1, por defecto 1) y `pageSize` (1–100, por defecto 20).

## Cómo implementar la lógica

En cada handler de `app/routers/*.py`, hay que reemplazar `return stubs.xxx()` por la
lógica real y devolver un modelo de `app/schemas.py`. Los tests seguirán validando que la
respuesta cumpla el contrato.

`require_auth` en `app/deps.py` solo comprueba que exista un header `Bearer`: hay que
reemplazarlo por la validación real del JWT.

Si el contrato cambia, hay que actualizar `contract/openapi.yaml`, los modelos de
`app/schemas.py` y los stubs; los tests indican qué quedó desalineado.

## Tests

Qué validan:

| Archivo | Valida |
|---|---|
| `test_models_vs_contract.py` | Modelos (propiedades, required, enums, límites, nullable) y stubs contra el contrato. No requiere FastAPI. |
| `test_routes_contract.py` | Rutas, métodos, status codes, tags, seguridad, parámetros y request bodies del OpenAPI generado vs. el contrato. |
| `test_responses_contract.py` | Cada endpoint responde con el status y el cuerpo del contrato; 401 sin token; endpoints públicos. |
| `test_validation.py` | Entradas inválidas → 422 con `ErrorResponse`; límites de paginación, enums, UUID, uploads. |

### Local

```bash
pip install -r requirements.test.txt
pytest
```

Más detalle: `pytest -v` muestra cada test, y `pytest -q -rw` muestra los warnings.

### Con Docker

Construir la imagen (stage `test`):

```bash
docker build --target test -t tutoria-api:test .
```

Correr los tests y eliminar el contenedor al terminar:

```bash
docker run --rm tutoria-api:test
```

Correr los tests conservando el contenedor, para poder ver los logs después:

```bash
docker run --name tutoria-tests tutoria-api:test
docker logs tutoria-tests
docker rm tutoria-tests              # eliminar el contenedor
```

Guardar la salida en un archivo:

```bash
docker run --rm tutoria-api:test pytest -v 2>&1 | tee resultados.txt
```

Generar un reporte JUnit en la máquina local:

```bash
mkdir -p reports
docker run --rm -v "$(pwd)/reports:/app/reports" tutoria-api:test \
  pytest -v --junitxml=reports/junit.xml
```

> [!NOTE]
> El `CMD` del stage `test` es `pytest --disable-warnings -q`. Si se pasa otro comando
> al `docker run`, reemplaza a ese `CMD` por completo.

## Notas

- `POST /conversations` y `POST .../messages` no declaran 404/403 en el contrato.
- El 415 de `POST /courses/{courseId}/documents` está declarado pero no implementado:
  falta decidir cómo detectar `text/plain` y `text/markdown`, ya que los navegadores
  suelen enviar `.md` con otro MIME.
- El schema `GenerateMaterial` mantiene el nombre del contrato (probablemente querían
  `GeneratedMaterial`).