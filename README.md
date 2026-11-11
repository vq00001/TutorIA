# TutorIA

TutorIA es una aplicación web pensada para potenciar el estudio individual de alumnos de educación superior mediante el uso de modelos de lenguaje. 

Las principales funcionalidades son la respuestas de preguntas en lenguaje natural a partir del material provisto por el estudiante, la generación de un plan de estudio, personalización de las respuestas del modelo según el nivel de conocimiento del usuario, etc. 
## Ejecutar el proyecto

Desde el directorio raíz para ejecutar el proyecto en  <http://localhost:8080>.   

```bash
cp .env.example .env       # crear .env
make up                    # ejecutar en producción
```

El comando `make up` inicia el frontend, Ollama y PostgreSQL con pgvector. Compose habilita pgvector automáticamente.

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

## Estructura del proyecto

- [backend/db](backend/db/) contiene la conexión a PostgreSQL y el instalador de pgvector.
- [backend](backend/README.md) contiene el procesamiento de documentos, el contrato API y los experimentos.
- [webapp](webapp/) contiene el frontend.

La primera instalación de la base y los comandos locales están en el [README del backend](backend/README.md).
