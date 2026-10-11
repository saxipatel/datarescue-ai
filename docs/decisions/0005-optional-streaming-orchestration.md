# ADR 0005: Kafka and Airflow as optional adapters

**Status:** accepted

## Context

Streaming ingestion and a workflow orchestrator are common in production data platforms,
but they add heavy services (a broker, a scheduler, a metadata DB) that make the base
stack slower to start and harder to run on a laptop. The core value here, validation,
incidents and safe recovery, doesn't depend on them.

## Decision

- **Kafka (Redpanda), profile `optional-streaming`.** A producer publishes synthetic
  events keyed by `event_id`. A consumer (group `datarescue-ingest`, manual commits) cuts
  per-partition micro-batches by count or time and ingests each one through the same
  engine with request key `kafka:<topic>:<partition>:<first>-<last>`. Offsets are
  committed only after the batch is durable. Ranges that keep failing after 3 attempts go
  to `transactions.dlq` with the error in the headers.
- **Airflow, profile `optional-orchestration`.** An hourly DAG runs generate → ingest →
  wait → diagnose → `dbt build` → publish metrics. Every step calls the public API or the
  dbt CLI, so no platform logic is duplicated. Retries use exponential backoff.
- The base stack uses the built-in scheduler process and file or API ingestion.

## Consequences

- `docker compose up` starts without either service.
- Verified locally: replaying the whole topic from offset 0 mapped onto the existing
  batches (no new rows), and `airflow dags test datarescue_pipeline` completed all six
  tasks against the running API, including the dbt build.
- Semantics are stated plainly: at-least-once from Kafka, idempotent effects in
  PostgreSQL, no claim of end-to-end exactly-once (no Kafka transactions span the DB
  write).
- `tests/integration/test_kafka_consumer.py` covers the consumer's ingest path with stand-in
  messages: same-range replay, re-chunked replay and dead-lettering. The broker loop
  (polling, offset commits) was verified manually as
  described above.
