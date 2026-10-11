# ADR 0002: Redis + RQ for background work

**Status:** accepted

## Context

Ingestion, diagnosis and recovery must not run inside HTTP requests. We need retries with
backoff, a way to tell the client the work was accepted, and a clear failure when it was
not. Candidates: Celery, RQ, Kafka as the only transport, or Airflow as the executor.

## Decision

Use Redis 7 with RQ. The API commits the database rows (batch, run, job) and then
enqueues. If enqueueing fails, the API returns `503 queue_unavailable` and marks what it
created, so work is never silently lost. Ingestion tasks retry 3 times with backoff
(2, 8 and 30 seconds); RQ's scheduler runs inside the worker. Recovery tasks do not
auto-retry: a retry is an explicit, audited resubmission of the same idempotency key.

## Consequences

- RQ gives at-least-once execution and no exactly-once guarantee. That is acceptable
  because every task is idempotent against the database: checkpoints, unique constraints,
  conditional updates and advisory locks (see ADR 0004).
- Tests and the seed script use `TASK_MODE=sync`, which runs the same task bodies inline.
- RQ forks a work horse per job. A hard crash in a job (including the chaos hook) is
  contained, and the advisory lock is released when the connection drops.
- Airflow stays optional (ADR 0005). The built-in scheduler process covers freshness
  checks and stale-job reaping.
