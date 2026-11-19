# Backend image for Render free (512 MB RAM, 0.1 CPU). Build context = repo root.
# No ML models in the image: embeddings and generation are remote API calls.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first so code edits don't bust the layer cache. psycopg[binary] ships its own libpq.
COPY backend/requirements.txt .
RUN pip install -r requirements.txt

# Runtime code + data only (tests, frontend, env files excluded via .dockerignore)
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/data ./data

# Non-root user
RUN useradd --system --uid 10001 --no-create-home appuser && chown -R appuser /app
USER appuser

# Render injects $PORT. One worker: 0.1 CPU, and the in-process rate/quota windows assume one process.
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*' --no-access-log"]
