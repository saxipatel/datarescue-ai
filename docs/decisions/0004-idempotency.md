# ADR 0004: Idempotency at every layer that can repeat

**Status:** accepted

## Context

Repeats are normal. Producers redeliver, clients resubmit, queues redeliver, workers crash
mid-task, and people click buttons twice. The rule is that no repeat may create a
duplicate accepted transaction or apply a repair twice.

## Decision

| Repeat | Guard |
|---|---|
| Same event delivered again | `UNIQUE(accepted_transactions.event_id)`. An identical payload is skipped; a different payload is quarantined as `DUPLICATE_CONFLICT`. |
| Same row twice in a batch | `UNIQUE(raw_events.batch_id, event_id, payload_hash)` with an `occurrences` count |
| Same ingestion request | `ingestion_batches.request_key` (dataset ID, file SHA-256, Kafka offset range, simulation ID, or redelivery sequence) |
| Worker crash mid-batch | `processed_count` checkpoint committed with each chunk; per-rule results recomputed from persisted rows |
| Same recovery request | `recovery_jobs.idempotency_key` (unique, bound to one proposal): finished → returned as-is, failed or abandoned → resumed, other proposal → 409 |
| Two executors on one job | Session-level `pg_try_advisory_lock` held for the run; the connection is invalidated afterwards so the lock can't leak back to the pool |
| Recovery retried after partial work | Conditional writes (`state = 'OPEN'`, `NOT duplicate_confirmed`, `ON CONFLICT DO NOTHING`); verification counts rows by `recovery_job_id` across attempts |
| Proposal executed again with a new key | Proposal status `EXECUTED` is terminal (service check + trigger) |

## Consequences

- Chaos tests cover two cases: a recovery crashed after its first chunk, then retried with
  the same key, writes exactly the dry-run count with zero duplicate IDs; a batch crashed
  after two chunks resumes and reconciles.
- Kafka delivery is at-least-once. If a replay is chunked differently from the original,
  it gets a new request key, and the protection falls to `event_id` uniqueness. The data
  stays correct, but an extra batch of skipped duplicates appears. That is documented
  rather than hidden.
- A few early bugs were found exactly here: a pooled connection keeping the advisory
  lock, and duplicates advancing the event-time watermark. Both now have regression
  tests.
