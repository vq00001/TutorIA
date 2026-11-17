FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS db-init
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
RUN python -c 'import subprocess, sys, tomllib; deps = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]; subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *deps])'

COPY __init__.py ./backend/
COPY db/ ./backend/db/
CMD ["python", "-m", "backend.db.initialize"]

FROM db-init AS test
RUN python -c 'import subprocess, sys, tomllib; deps = tomllib.load(open("pyproject.toml", "rb"))["dependency-groups"]["dev"]; subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *deps])'

WORKDIR /app/backend
COPY pyproject.toml *.py ./
COPY routes/ ./routes/
COPY experiments/ ./experiments/
COPY knowledge_graph/ ./knowledge_graph/
COPY utilities/ ./utilities/
COPY tests/ ./tests/
CMD ["python", "-m", "pytest"]
