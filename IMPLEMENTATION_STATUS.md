# Implementation status

Last verified 2026-10-10 on an Apple M4 with Docker Desktop. The verification runs started
from a **clean clone** (only tracked files) with all Docker volumes deleted. Every "done"
item below was observed working in that run or in the automated suites listed, not
inferred.

Legend: **done** = implemented and verified · **partial** = works with a stated gap ·
**optional** = behind a Compose profile, verified manually · **not done**.

## Acceptance matrix

| ID | Scenario | Status | Evidence |
|---|---|---|---|
| A01 | Fresh clone setup | done | `docker compose up -d --build --wait` → exit 0 in about 15 s with cached images. `migrate` exited 0 after applying migrations and seeding 12 clean batches; api, worker, scheduler, frontend, postgres and redis healthy. |
| A02 | Seed clean data | done | Seed log: 12 batches COMPLETE (6 × 1,000 payments_core, 6 × 400 partner_ledger), no incidents. `test_clean_batch_has_no_incident`. |
| A03 | Inject invalid amount | done | `test_scenario_outcome_matches_labels[invalid_amount]`: quarantined set == labelled set. The HTTP example in docs/api.md was captured from a live run. |
| A04 | Inject duplicate IDs | done | `test_duplicate_incident_full_lifecycle`: 100 identical skipped, 0 quarantined. `[conflicting_duplicates]` quarantined. Live demo: 900 accepted, 100 skipped, FP 0. |
| A05 | Schema incompatibility | done | `test_schema_break_is_not_silently_coerced`: v2 rows quarantined with `SCHEMA_VERSION_UNSUPPORTED`, original payload kept, schema versions recorded. |
| A06 | Trace impact | done | `GET /incidents/{id}/impact` returns paths to `dashboard_revenue` and `dashboard_settlement`. Lineage Explorer highlights them, with a table fallback. Unit tests cover BFS, cycle protection and shortest paths. |
| A07 | Diagnose without API key | done | LLM disabled by default. `diagnosis.ai_enrichment.status == "disabled"`, deterministic causes with cited evidence IDs. 18 diagnosis unit tests. |
| A08 | Unapproved replay denied | done | `403 approval_required` (`test_unapproved_proposal_cannot_run`, `test_demo_walkthrough_over_http`, live capture in docs/api.md). |
| A09 | Approved bounded replay | done | Recovery jobs SUCCEEDED with verification checks for SKIP, NORMALIZE, REPLAY, MANUAL and RETRY_MISSING_BATCH (`tests/integration/test_recovery.py`). |
| A10 | Same key twice | done | Second submit → `200 completed`, same job, accepted count unchanged (integration + HTTP tests, live demo step 6, UI resubmit). |
| A11 | Crash during replay | done | `test_crash_mid_replay_then_retry_with_same_key`: job left RUNNING, retry → attempts 2, rows written == dry run, 0 duplicate IDs. Live `make demo-chaos` on the RQ worker: 1,431 predicted, 1,431 written. Benchmark repeats this at 1k, 10k and 100k. |
| A12 | Audit trail | done | Actor, role, action, entity, before/after hashes, correlation ID. Clean-run audit contained login, batch, incident, proposal, dry-run, approval, recovery (including chaos and retry) and simulation events. The append-only trigger is tested. |
| A13 | UI workflow | done | Playwright `demo.spec.ts` (engineer injects → incident → propose → dry run → steward approves → execute → verification → same-key resubmit → incident resolved) passed against the clean stack. |
| A14 | CI / tests | done (local) | All suites below pass locally. `.github/workflows/ci.yml` mirrors them and passes `actionlint` 1.7.4; it has not run on GitHub yet because the repository has not been pushed. |
| A15 | Documentation | done | README, docs/architecture.md, data_contracts.md, api.md, runbook.md, security.md, 5 ADRs. |

## Verification commands and results (clean-clone run)

| Command | Result |
|---|---|
| `make test-backend` | `172 passed in 13.58s` (re-run 2026-10-11 after adding the Kafka adapter tests) |
| `alembic upgrade head && downgrade base && upgrade head` | ends at `0002 (head)` |
| `make lint` | ruff: `All checks passed!`, `91 files already formatted`; mypy: `Success: no issues found in 65 source files`; ESLint and `tsc --noEmit` clean; exit 0 |
| `make test-frontend` | lint, typecheck, `vite build` ✓ |
| `make demo-chaos` | all steps completed; every verification check PASS |
| `make e2e` | `3 passed` (2026-10-11, adds the phone-width overflow check) |
| `make dbt` | `Done. PASS=23 WARN=0 ERROR=0 SKIP=0 TOTAL=23` |
| `echo nope \| make reset` | `aborted`, volumes kept |
| `make benchmark` (earlier the same day, same code paths) | 1k/10k/100k: counts match ground truth, FP/FN 0/0, 6,057 rows/s at 100k, peak RSS 148 MB; see README |

Test inventory (2026-10-11): 124 unit tests, counting parametrized cases (validator boundaries for every rule, Decimal handling,
hashing, thresholds, volume baseline, lineage, state machine, RBAC, generator determinism
and labels, readers, redaction, diagnosis ranking, LLM fallbacks) and 48 integration
tests against PostgreSQL (Kafka micro-batch replays, 14 scenarios against ground truth, resume after crash, cross-batch redelivery,
watermark regression, lifecycle, all five repair actions, chaos retry, concurrent
executors, approval rules, append-only audit, and HTTP: auth, RBAC, pagination,
validation, uploads, 503 when the queue is down, rule editing, CSV export, freshness).

## Capabilities

### P0

| Capability | Status | Notes |
|---|---|---|
| Synthetic source with deterministic seeds, 1k/10k/100k, labelled anomalies | done | 17 fault scenarios; fixtures via `make fixtures` |
| CSV/JSONL upload and generated-batch ingestion, streamed in chunks | done | size, type, encoding and header checks; idempotent by file hash |
| Raw evidence before validation; malformed rows don't crash the batch | done | |
| Schema and business validation, Decimal money, UTC timestamps | done | |
| DB-enforced dedupe; identical skip vs conflict quarantine | done | |
| Per-rule results, batch status, checksum | done | |
| Incidents with server-side transitions, justification, optimistic locking, audit | done | |
| Quarantine with reason codes and per-field details | done | |
| Proposal → dry run → approval → idempotent recovery → verification | done | |
| FastAPI with OpenAPI, standard errors, pagination, 202 + polling | done | |
| Frontend with all nine pages, dark/light, keyboard focus, empty/error/loading states | done | no horizontal scroll at 375 px on all 13 views (Playwright); three overflow bugs fixed on 2026-10-11 |
| Docker Compose, health checks, ordering, Makefile commands | done | |
| Tests and CI configuration | done | CI not yet run on GitHub |

### P1

| Capability | Status | Notes |
|---|---|---|
| Lineage graph and blast radius | done | dataset-level only (stated in UI and API) |
| Deterministic RCA | done | heuristic confidence labels |
| Optional LLM summaries | partial | implemented with schema validation, citation checks and fallbacks; unit-tested with stubs; **not run against a live API key** |
| Failure simulator with ground-truth evaluation | done | |
| Metric dashboards | done | MTTD, quarantine rate, recovery success rate, incident duration, false positives, throughput and pass rate |
| Batch scheduling | done | built-in scheduler (freshness, stale-job reaper); Airflow optional |
| dbt models and tests | done | staging view, two marts, 20 tests including reconciliation |

### P2

| Capability | Status | Notes |
|---|---|---|
| Kafka/Redpanda adapter | optional | verified manually against Redpanda (2,000 events → 2 micro-batches; a full replay from offset 0 mapped onto the existing batches). `test_kafka_consumer.py` covers ingest, same-range replay, re-chunked replay and the dead-letter path without a broker; the polling and commit loop is only manually verified. |
| Airflow DAG | optional | `airflow dags test datarescue_pipeline` ran all 6 tasks against the live API, including `dbt build`. Airflow is not part of CI. |
| Great Expectations adapter | not done | |
| Alert delivery | not done | |
| OpenTelemetry | not done | structured JSON logs with correlation IDs only |
| Cloud deployment | not done | |
| Richer RBAC | not done | four fixed roles |

## Known gaps and deviations from the spec

- **Docker image for dbt:** the upstream `dbt-labs/dbt-postgres` image has no arm64
  build, so `dbt/Dockerfile` pins `dbt-core~=1.8.0` and `dbt-postgres==1.8.2` on
  `python:3.12-slim`.
- **Detection timing:** rules are evaluated at the end of each batch, so detection latency
  equals ingestion time (16.5 s for a 100k-row batch in the benchmark).
- **Kafka replays** that are chunked differently from the original produce a new batch of
  skipped duplicates. Accepted data stays correct because of `event_id` uniqueness.
- **`RETRY_MISSING_BATCH`** only works for the synthetic source.
- **Audit immutability** is trigger-based; a database superuser can bypass it.
- **Bugs found during live verification and fixed:**
  - Duplicates advanced the event-time watermark (regression test).
  - The simulator re-sent repaired rows as "identical" redeliveries (regression test).
  - A pooled connection kept a session advisory lock (caught by the chaos test; a
    concurrent-executor test added).
  - `numeric` averages were serialized as JSON strings (assertion added to an API test).
  - In the Kafka consumer, a log field name collided with `LogRecord.created` (fixed and
    re-verified manually; the adapter tests now exercise the same `ingest()` return value).
  - The benchmark shared a database across sizes with the same seed (the script now
    resets per size).
