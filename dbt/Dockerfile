# Multi-arch alternative to the upstream dbt image (which has no arm64 build).
FROM python:3.12-slim
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install "dbt-core~=1.8.0" "dbt-postgres==1.8.2"
WORKDIR /usr/app
ENTRYPOINT ["dbt"]
