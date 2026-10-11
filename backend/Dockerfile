# syntax=docker/dockerfile:1
# Build context is the repository root (see docker-compose.yml).
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app
WORKDIR /app
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data && chown app:app /data
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

# Test/lint tooling; used by `make test` and CI. Source is bind-mounted in development.
FROM base AS dev
COPY backend/requirements-dev.txt ./
RUN pip install -r requirements-dev.txt
COPY --chown=app:app backend/ ./
COPY --chown=app:app scripts ./scripts
USER app

FROM base AS runtime
COPY --chown=app:app backend/alembic.ini ./
COPY --chown=app:app backend/alembic ./alembic
COPY --chown=app:app backend/app ./app
COPY --chown=app:app scripts ./scripts
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
