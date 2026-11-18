FROM python:3.12.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
RUN python -c 'import subprocess, sys, tomllib; deps = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]; subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *deps])'

COPY __init__.py /app/backend/
COPY healthcheck.py /app/backend/healthcheck.py
COPY api/ /app/backend/api/
COPY core/ /app/backend/core/
COPY db/ /app/backend/db/
COPY integrations/ /app/backend/integrations/
COPY knowledge_graph/ /app/backend/knowledge_graph/
COPY rag/ /app/backend/rag/
COPY storage/ /app/backend/storage/
COPY utilities/ /app/backend/utilities/
COPY workers/ /app/backend/workers/

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2)"]
CMD ["uvicorn", "backend.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
