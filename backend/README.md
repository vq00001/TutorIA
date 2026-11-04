# Backend de RAG local con Ollama y Qdrant

Pipeline de **Retrieval-Augmented Generation (RAG)** para procesar documentos,
guardarlos como vectores en Qdrant y consultarlos usando modelos locales de
Ollama.

## Requisitos

- [Docker](https://docs.docker.com/get-docker/) y Docker Compose
- [uv](https://docs.astral.sh/uv/)
- `make`

## Puesta en marcha

1. Instala `uv` si todavía no lo tienes:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. Crea el archivo de configuración local:

   ```bash
   cp .env.example .env
   ```

3. Levanta Qdrant y Ollama:

   ```bash
   docker compose up --build
   ```

   La primera ejecución puede tardar porque Ollama descarga los modelos
   `qwen3:1.7b` y `all-minilm`.

4. En otra terminal, instala las dependencias del proyecto:

   ```bash
   make install
   ```

## Uso

### Previsualizar documentos

Muestra los chunks que se generarían sin conectarse a los servicios:

```bash
make preview DOCS=docs
```

### Ingestar documentos

Procesa los documentos, genera embeddings y los guarda en Qdrant:

```bash
make ingest DOCS=docs
```

Para generar contexto adicional con el modelo LLM, activa la flag opcional:

```bash
make ingest DOCS=docs CONTEXT=1
```

### Buscar información

Recupera los documentos más relevantes:

```bash
make search QUERY="tu pregunta"
```

Genera también una respuesta usando el contexto recuperado:

```bash
make search QUERY="tu pregunta" ANSWER=1
```

## Comandos disponibles

Ejecuta `make help` para ver todos los comandos:

| Comando | Descripción |
| --- | --- |
| `make install` | Instala dependencias de desarrollo y producción |
| `make prod` | Instala solo dependencias de producción |
| `make sync` | Sincroniza el entorno usando `uv.lock` |
| `make lint` | Ejecuta Ruff |
| `make format` | Formatea el código |
| `make test` | Ejecuta los tests |
| `make evaluate` | Evalúa los thresholds de recuperación |
| `make evaluate-limit` | Compara valores de `LIMIT_QUERY` |
| `make calibrate` | Calibra `TOKEN_ESTIMATE_RATIO` |

## Configuración

La configuración se define en `.env`. Consulta
[`.env.example`](.env.example) para ver todas las opciones disponibles,
incluyendo:

- URLs y puertos de Ollama y Qdrant.
- Modelos de lenguaje y embeddings.
- Tamaño de los chunks y solapamiento.
- Límite de resultados y umbral de similitud.

## Detener los servicios

Para detener los contenedores sin eliminar los datos:

```bash
docker compose down
```

Para eliminar también los volúmenes persistentes de Ollama y Qdrant:

```bash
docker compose down -v
```
