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
inicio de sesión, clases y grafo del curso con la base de datos. El servicio
`document-worker` genera embeddings mediante el alias `embeddings` del AI
gateway, que puede enrutar a Ollama. El `chat-worker` usa el mismo gateway para
las respuestas del modelo.
El servicio `chat-worker` recupera los chunks vectoriales y publica las
respuestas del chat con sus referencias documentales. La generación de exámenes
aún requiere un consumidor aparte.


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
`usuario@example.com` con cualquier contraseña ya no funciona. Desde la pantalla
de acceso se puede registrar una cuenta. En la configuración normal queda
pendiente hasta verificar el código recibido por correo.

Para probar localmente sin configurar correo, añade `compose.dev-account.yaml` al
comando de arranque. `db-init` crea o restablece una cuenta activa cada vez que
se ejecuta con ese archivo; no afecta al Compose normal:

```bash
WEBAPP_PORT=8082 docker compose -f compose.yaml -f compose.nvidia.yaml \
  -f compose.dev-account.yaml --profile prod up --build --force-recreate
```

La cuenta local es `dev@example.com` con contraseña `TutorIA-dev-123`. Para
cambiarlos, configura `DEV_ACCOUNT_EMAIL` y `DEV_ACCOUNT_PASSWORD` antes de
arrancar. Este archivo adicional también activa inmediatamente las cuentas
creadas desde el registro para poder probarlas sin servicio de correo. Es solo
para pruebas en el equipo local.

En el mapa de un curso sin conceptos, el coordinador puede subir un `.txt` o
`.md`, revisar la propuesta de nodos y dependencias y guardarla. Subir un
material al curso no genera el grafo automáticamente. En **Materiales**, los
documentos avanzan de «En cola» a «Procesando» y «Listo» cuando el worker
termina la ingestión; la vista consulta el estado cada cinco segundos.
Los archivos se pueden revisar en `archivos/<curso>-<id>/`: se conserva el
original y, para cada PDF válido, el worker guarda también un `.md` convertido
con el mismo nombre base. En Docker, `archivos/` se comparte entre la API y el
worker y queda visible en la raíz del proyecto. Los PDF con tablas o imágenes
siguen admitidos; se rechazan los PDF corruptos, protegidos o sin texto extraíble.

## Estructura del proyecto

```
/TutorIA
├── backend
│   ├── api/               # FastAPI, rutas HTTP y contratos
│   ├── cli/               # Comandos locales de terminal
│   ├── core/              # Configuración y modelos compartidos
│   ├── db/                # Persistencia y cola de trabajos
│   ├── experiments/       # Experimentos y datos de evaluación
│   ├── integrations/      # Clientes de LLM y embeddings
│   ├── knowledge_graph/   # Conceptos y prerequisitos
│   ├── rag/               # Chunking, documentos y pipeline
│   ├── storage/           # Archivos originales y convertidos
│   ├── workers/           # Procesadores en segundo plano
│   ├── openapi.yaml
│   ├── pyproject.toml
│   └── README.md
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

