# TutorIA

TutorIA es una aplicación web pensada para potenciar el estudio individual de alumnos de educación superior mediante el uso de modelos de lenguaje.

Las principales funcionalidades son la respuestas de preguntas en lenguaje natural a partir del material provisto por el estudiante, la generación de un plan de estudio, personalización de las respuestas del modelo según el nivel de conocimiento del usuario, etc.

## Ejecutar el proyecto

Desde el directorio raíz para ejecutar el proyecto en  <http://localhost:8080>.

```bash
cp .env.example .env       # crear .env
make up                    # ejecutar en producción
```
El comando `make up` es para ejecutar el programa en producción e inicia los contenedores para el frontend, backend, Ollama, PostgreSQL, servicio LLM local, y base de datos.
Compose habilita pgvector automáticamente.

El contenedor `backend` ejecuta la API persistente de `backend/http_api.py` y
espera a que `db-init` prepare PostgreSQL. Esta primera integración conecta
inicio de sesión, clases y grafo del curso con la base de datos. El chat y el
procesamiento asíncrono de documentos aún requieren sus servicios de trabajo.


El puerto del frontend es configurable en el `.env` en la variable `WEBAPP_PORT`.  El archivo `.env.example` también contiene la configuración del servicio de RAG usado al ejecutarlo directamente en el equipo anfitrión.

Comando de docker original: `docker compose --profile prod up --build`.

## Ejecutar en modo desarrollador

Desde el directorio raíz para ejecutar el proyecto en  <http://localhost:5173>.

```bash
cp .env.example .env       # crear .env
make dev                   # ejecutar como desarrollador
```

El comando `make dev` ejecuta el frontend con Vite. La documentación de la API
activa está en <http://localhost:8000/docs>. El SwaggerUI separado en
<http://localhost:8081> todavía muestra el contrato de la API de demostración.

Comando de docker original: `docker compose --profile dev up --build`.

### Entrar a la aplicación

Se necesita una cuenta activa en PostgreSQL: el acceso de demostración
`usuario@example.com` con cualquier contraseña ya no funciona. El registro crea
una cuenta pendiente que debe verificar su correo antes de crear una clase.

Para probar localmente sin configurar correo, añade `compose.dev-account.yaml` al
comando de arranque. `db-init` crea o restablece una cuenta activa cada vez que
se ejecuta con ese archivo; no afecta al Compose normal:

```bash
WEBAPP_PORT=8082 docker compose -f compose.yaml -f compose.nvidia.yaml \
  -f compose.dev-account.yaml --profile prod up --build --force-recreate
```

La cuenta local es `dev@example.com` con contraseña `TutorIA-dev-123`. Para
cambiarlos, configura `DEV_ACCOUNT_EMAIL` y `DEV_ACCOUNT_PASSWORD` antes de
arrancar. Este archivo adicional es solo para pruebas en el equipo local.

## Estructura del proyecto

```
/TutorIA
├── backend
│   ├── API
│   │   └── openapi.yaml
│   ├── chunking.py
│   ├── config.py
│   ├── context.py
│   ├── documents.py
│   ├── embeddings.py
│   ├── experiments
│   │   ├── __init__.py
│   │   ├── README.md
│   │   ├── retrieval
│   │   │   ├── data
│   │   │   │   ├── cases
│   │   │   │   │   ├── test_cases.json
│   │   │   │   │   └── test_cases_many.json
│   │   │   │   └── documents
│   │   │   │       ├── documents.json
│   │   │   │       └── documents_many.json
│   │   │   ├── evaluate_limit.py
│   │   │   ├── __init__.py
│   │   │   ├── output.py
│   │   │   ├── README.md
│   │   │   ├── repository.py
│   │   │   └── run.py
│   │   └── token_estimation
│   │       ├── calibrate.py
│   │       └── data
│   │           └── samples.md
│   ├── __init__.py
│   ├── llm.py
│   ├── main.py
│   ├── Makefile
│   ├── models.py
│   ├── ollama
│   │   ├── Dockerfile
│   │   └── entrypoint.sh
│   ├── pipeline.py
│   ├── pyproject.toml
│   ├── README.md
│   └── vector_store.py
├── compose.yaml
├── Makefile
├── README.md
└── webapp
    ├── Dockerfile
    ├── index.html
    ├── nginx
    │   └── default.conf
    ├── package.json
    ├── package-lock.json
    ├── README.md
    ├── src
    │   ├── App.tsx
    │   ├── components
    │   │   ├── chat
    │   │   │   ├── ChartCard.tsx
    │   │   │   ├── ChatComposer.tsx
    │   │   │   ├── MarkdownContent.tsx
    │   │   │   ├── MermaidDiagram.tsx
    │   │   │   └── MessageRenderer.tsx
    │   │   ├── exams
    │   │   │   ├── ExamBuilder.tsx
    │   │   │   └── ExamCard.tsx
    │   │   ├── graph
    │   │   │   └── KnowledgeGraph.tsx
    │   │   ├── layout
    │   │   │   └── AppShell.tsx
    │   │   ├── materials
    │   │   │   ├── MaterialDropzone.tsx
    │   │   │   └── MaterialList.tsx
    │   │   └── ui
    │   │       └── PageHeading.tsx
    │   ├── contexts
    │   │   ├── AppDataContext.tsx
    │   │   └── ThemeContext.tsx
    │   ├── data
    │   │   ├── demoDatabase.ts
    │   │   └── exams.ts
    │   ├── main.tsx
    │   ├── pages
    │   │   ├── ChatPage.tsx
    │   │   ├── CreateCoursePage.tsx
    │   │   ├── ExamPage.tsx
    │   │   ├── GraphPage.tsx
    │   │   ├── HomePage.tsx
    │   │   ├── MaterialsPage.tsx
    │   │   └── TakeExamPage.tsx
    │   ├── services
    │   │   └── databaseApi.ts
    │   ├── styles
    │   │   └── exams.css
    │   ├── styles.css
    │   └── types
    │       ├── charts.ts
    │       ├── exams.ts
    │       └── index.ts
    ├── tsconfig.app.json
    ├── tsconfig.json
    └── vite.config.ts
```

- [backend/db](backend/db/) contiene la conexión a PostgreSQL y el instalador de pgvector.
- [backend](backend/README.md) contiene el procesamiento de documentos, el contrato API y los experimentos.
- [webapp](webapp/) contiene el frontend.

La primera instalación de la base y los comandos locales están en el [README del backend](backend/README.md).

