# TutorIA

TutorIA es una aplicación web pensada para potenciar el estudio individual de alumnos de educación superior mediante el uso de modelos de lenguaje.

Las principales funcionalidades son la respuestas de preguntas en lenguaje natural a partir del material provisto por el estudiante, la generación de un plan de estudio, personalización de las respuestas del modelo según el nivel de conocimiento del usuario, etc.

## Ejecutar el proyecto

Desde el directorio raíz para ejecutar el proyecto en  <http://localhost:8080>.

```bash
cp .env.example .env       # crear .env
make up                    # ejecutar en producción
```

El comando `make up` es para ejecutar el programa en producción e inicia los contenedores para el frontend, backend, servicio LLM local, y base de datos.

El puerto del frontend es configurable en el `.env` en la variable `WEBAPP_PORT`.  El archivo `.env.example` también contiene la configuración del servicio de RAG usado al ejecutarlo directamente en el equipo anfitrión.

Comando de docker original: `docker compose --profile prod up --build`.

## Ejecutar en modo desarrollador

Desde el directorio raíz para ejecutar el proyecto en  <http://localhost:5173>.

```bash
cp .env.example .env       # crear .env
make dev                   # ejecutar como desarrollador
```

El comando `make dev` es para ejecutar los contenedores para ejecutar el frontend con vite para visualizar las modificaciones de la interfaz rápidamente. Además expone una interfaz para la API a través de SwaggerUI en <http://localhost:8081>.

Comando de docker original: `docker compose --profile dev up --build`.

### Entrar a la aplicación

Para ingresar en modo desarrollo, usar el correo de usuario <usuario@example.com> y cualquier contraseña.

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
