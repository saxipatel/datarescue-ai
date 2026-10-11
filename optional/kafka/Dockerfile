# Optional streaming adapter (compose profile `optional-streaming`). Build context: repo root.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt "confluent-kafka==2.6.1"
COPY backend/app ./app
COPY optional ./optional
RUN useradd --uid 1000 app && mkdir -p /data && chown app:app /data
USER app
