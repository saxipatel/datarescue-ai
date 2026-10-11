# ADR 0001: PostgreSQL as the system of record

**Status:** accepted

## Context

The platform must keep raw evidence, validated rows, quarantine, quality results,
incidents, approvals, recovery state and an audit trail consistent with each other. A
recovery that inserts a row must also move a counter, close a quarantine record and write
an audit event. If any of those happen separately, the result is double counting or
missing evidence.

## Decision

Use one PostgreSQL 16 database for everything the platform owns. Raw payloads and
evidence go in JSONB columns. Invariants are enforced in the database where possible:
unique `event_id`, CHECK constraints on amounts, currency, status and the batch counter
reconciliation, an append-only trigger on audit, and terminal-state triggers. Schema
changes go through Alembic migrations only; the app never calls `create_all`.

## Consequences

- One transaction covers a chunk's raw rows, accepted rows, quarantine rows, counters and
  checkpoint. Crash recovery reduces to "resume after the checkpoint".
- JSONB and GIN indexes make evidence queries (rows with error code X) cheap enough at
  this scale.
- Postgres is not a columnar warehouse. The analytics marts are small aggregates built by
  dbt in a separate `analytics` schema. At larger volumes, raw events would move to object
  storage and accepted rows to a warehouse, with Postgres keeping the control-plane
  tables.
- Write throughput is bounded by one database. The measured single-process ingest rate
  is about 6k rows/s on a laptop (see the README benchmarks).
