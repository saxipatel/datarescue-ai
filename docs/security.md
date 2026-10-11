# Security notes

This is a local, single-tenant portfolio system. The controls below are real and tested,
but the deployment model (Compose on a laptop, demo accounts) is not production-grade.
The hardening backlog at the end lists what would have to change.

## Data

- **Synthetic only.** The generator produces IDs like `C00042`, `M0001` and
  `T00042-0000017`, random UUIDs, and amounts from a log-normal distribution. A unit test
  asserts that no generated value contains a 13–19 digit run (the shape of a card number).
  There is no PCI DSS scope and no compliance claim.
- **Redaction.** API responses and LLM prompts mask `customer_id` (`C***42`), drop
  unparseable raw text, and truncate long strings. Raw payloads remain unredacted in the
  database as evidence.
- **Money** is `NUMERIC(18,2)` / `Decimal` end to end. Float inputs are rejected.

## Authentication and authorization

- Passwords are hashed with Argon2id (`argon2-cffi` defaults). Login returns the same
  error for an unknown user and a wrong password.
- Bearer JWTs are HS256 and expire after `JWT_TTL_MINUTES`. Startup refuses the
  placeholder secret when `ENVIRONMENT=production`, and the secret must be at least 32
  bytes. The frontend keeps the token in `sessionStorage`, so it doesn't survive closing
  the tab.
- Four roles map to explicit permissions in
  [`security/rbac.py`](../backend/app/security/rbac.py), checked server-side on every
  mutating route. Tests cover 401 and 403 paths.
- **Approval controls:** only stewards and admins approve. Approval requires a dry run.
  High-risk proposals (more than 1,000 rows written to business data) need a reviewer
  other than the proposer, which is enforced in the service and by a CHECK constraint.
  Recovery requires an approved proposal (`403 approval_required`).

## Demo credentials

`engineer@`, `steward@`, `admin@` and `analyst@example.test`, all with password
`datarescue-demo`, are created by `scripts/seed.py` for local use only. Never run the seed
against a shared environment. The `.test` TLD is reserved and cannot receive mail.

## Input handling

- Uploads are checked for extension (`.csv`, `.jsonl`, `.ndjson`), a streaming size limit
  (`MAX_UPLOAD_BYTES`; partial files are deleted), UTF-8 encoding, and CSV header columns,
  before anything is queued. Files are stored under a generated UUID name in the data
  volume. `data_path()` rejects paths that escape the data directory, and no endpoint
  accepts or returns filesystem paths.
- Lines over 64 KB and unparseable rows are quarantined as `CORRUPTED_PAYLOAD`, never
  executed or evaluated.
- Request bodies are Pydantic models with bounds. Repair targets accept only three
  narrowing filters; arbitrary keys, including anything SQL-like, are rejected with 422.
  No endpoint accepts SQL.
- SQL is either built with SQLAlchemy expressions or written as `text()` with bound
  parameters. The two interpolated identifiers (table names in lineage refresh and test
  truncation) come from code constants.
- **CSV export:** cells starting with `=`, `+`, `-`, `@`, tab or CR are prefixed with `'`
  to prevent formula injection in spreadsheet apps (test:
  `test_quarantine_export_neutralizes_formulas`).
- Rule configuration edits are validated per key and type. `locked` is not editable.

## Rate limiting

`/simulations`, `/datasets/generate` and `/incidents/{id}/diagnose` use a per-user
fixed-window counter in Redis (`RATE_LIMIT_PER_MINUTE`, default 20). If Redis is down, the
check fails open and the endpoint itself then returns 503, because it can't enqueue.

## LLM boundary (optional feature)

- Off unless `LLM_ENABLED=true` and `LLM_API_KEY` is set. The key comes from the
  environment only and is never logged or returned. `/system/info` reports only whether
  the feature is active.
- The model receives a redacted, size-bounded JSON summary wrapped in an
  `<incident_data>` block. The system prompt states that evidence is untrusted data, and
  `<`, `>` and code fences inside evidence strings are neutralized.
- Output must parse into a fixed schema. Citations must be evidence IDs that were sent,
  and suggested actions are filtered to the allowlist. Anything else is discarded, and
  the deterministic diagnosis stands alone.
- The model's output is stored as `ai_enrichment` with `unverified: true`. It cannot
  create proposals, approve, or execute anything.

## Audit

Every state change writes an `audit_events` row in the same transaction: actor, role,
action, entity, SHA-256 of canonical before and after snapshots, details and
correlation ID. Covered actions include login, ingestion requests, batch completion,
incident transitions, proposals, dry runs, reviews, recovery start, success and failure,
rule changes, simulations and chaos injection.

A trigger (`audit_events_no_update_delete`) rejects UPDATE and DELETE on the table.
**Caveat:** a database superuser can disable or drop the trigger, or `TRUNCATE` the table
(the test suite does this between tests). The log is append-only against application
bugs, not tamper-evident against an administrator. A production version would ship events
to write-once storage or hash-chain them.

## Trust boundaries

```
browser ──(JWT)──> nginx ──> API ──> Postgres
                              │──> Redis ──> worker ──> Postgres
                              └──(optional, redacted)──> LLM provider
producers ──(files / Kafka)──> ingestion (untrusted content, quarantined on failure)
```

Untrusted inputs: uploaded files, Kafka messages, every field of every payload, and LLM
output. Trusted: the code, the database, and the environment configuration.

## Network exposure

Compose publishes Postgres (5432), Redis (6379), the API (8000) and the frontend (5173) on
the host for local development. Postgres uses a fixed development password, and Redis has
no authentication. Don't expose these ports beyond localhost.

## Production hardening backlog

- Managed secrets; random JWT key; remove seed users; enforce `ENVIRONMENT=production`
- OIDC/SSO, token revocation, short-lived access with refresh, MFA for stewards
- TLS everywhere; Postgres and Redis on a private network with authentication; no
  published DB ports
- A least-privilege database role for the app, with no DDL and no TRUNCATE on
  `audit_events`
- Tamper-evident audit (hash chain, or export to WORM storage)
- Malware scanning and content-type sniffing for uploads; per-tenant quotas
- Rate limiting at the edge; request size limits in nginx tuned per route
- Dependency and image scanning in CI (pip-audit, npm audit, Trivy)
- Content Security Policy for the frontend
