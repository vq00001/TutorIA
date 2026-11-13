FROM python:3.12-alpine AS dev

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app app

EXPOSE 8000

# Alpine no incluye curl; se usa Python, que ya está en la imagen.
HEALTHCHECK \
    --start-period=5s \
    --interval=5s \
    --timeout=3s \
    --retries=3 \
    CMD ["python", "-c","import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


FROM dev AS test

COPY requirements.test.txt .
RUN pip install --no-cache-dir -r requirements.test.txt

COPY contract contract
COPY pytest.ini .
COPY tests tests

HEALTHCHECK NONE
CMD ["pytest", "--disable-warnings", "-q"]