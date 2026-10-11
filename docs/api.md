# API reference

Base URL `http://localhost:8000/api/v1`. The OpenAPI schema and interactive docs are at
`/docs` and `/openapi.json`. The examples below are real responses from a local run, with
tokens shortened.

## Conventions

- **Auth:** `Authorization: Bearer <token>` from `POST /auth/login`. Tokens are HS256 JWTs
  valid for `JWT_TTL_MINUTES` (60).
- **Errors** always have this shape:
  ```json
  {"code": "forbidden", "message": "…", "details": {…} | null, "correlation_id": "…"}
  ```
- **Correlation:** send `X-Correlation-ID` (8–64 chars of `[A-Za-z0-9._-]`) or one is
  generated. It is echoed in the response header, written to every log line, and stored on
  batches, runs, jobs and audit events.
- **Pagination:** `limit` (1–200, default 50) and `offset`. Responses are
  `{"items": [...], "total": n, "limit": l, "offset": o}`.
- **Time:** ISO-8601 in UTC. **Money:** decimal strings (`"119706.59"`), never floats.
- **Long-running work** returns `202` and a status URL or job ID to poll.

| Status | When |
|---|---|
| 400 | domain rule violated |
| 401 | missing, invalid or expired token; bad credentials |
| 403 | role lacks the permission; recovery without approval; separation of duties; chaos disabled |
| 404 | unknown resource |
| 409 | invalid state transition; concurrent modification; dry run required; idempotency key reused for another proposal; locked rule |
| 422 | request validation (types, bounds, unknown filters, bad uploads) |
| 429 | rate limit on simulations, dataset generation, diagnosis (`RATE_LIMIT_PER_MINUTE`) |
| 503 | database or task queue unavailable; work was not accepted |

## Roles

| Permission | analyst | engineer | steward | admin |
|---|:-:|:-:|:-:|:-:|
| read (all GET except audit) | ✓ | ✓ | ✓ | ✓ |
| ingest, generate datasets, resume batches | | ✓ | | ✓ |
| simulate | | ✓ | | ✓ |
| triage (acknowledge, close, diagnose) | | ✓ | ✓ | ✓ |
| propose repair, dry run | | ✓ | ✓ | ✓ |
| approve / reject repair | | | ✓ | ✓ |
| execute approved recovery | | ✓ | ✓ | ✓ |
| manage rules, manage sources, read audit | | | | ✓ |

A proposal with risk `high` or `critical` can't be approved by the person who created it.

## Auth

```http
POST /auth/login
{"email": "engineer@example.test", "password": "datarescue-demo"}
```
```json
200
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5…",
  "token_type": "bearer",
  "expires_at": "2026-10-10T22:56:27.392363Z",
  "user": {"id": "787e0333-…", "email": "engineer@example.test", "full_name": "Demo Engineer", "role": "engineer"}
}
```
```json
401
{"code":"unauthorized","message":"Invalid email or password","details":null,"correlation_id":"3483166ab75946ea80e5d0a75663e765"}
```

`GET /auth/me` returns the user plus `permissions`.

## Health

- `GET /health` → `200 {"status":"ok"}` (process only)
- `GET /ready` → `200 {"status":"ready","checks":{"database":"ok","queue":"ok"}}`, or `503`
  with the failing check

## Ingestion

### Generate a dataset, then ingest it

```http
POST /datasets/generate
{"source": "payments_core", "scenario": "clean", "records": 1000, "seed": 7}
```
Returns `201` with `id` and `expected` (ground-truth counts).

```http
POST /ingestions
{"source": "payments_core", "dataset_id": "<id>", "sequence_number": 7}
```
```json
202
{"batch_id": "…", "run_id": "…", "job_id": "…", "duplicate_request": false, "status_url": "/api/v1/batches/…"}
```
Repeating the request (same `request_key`, which defaults to `dataset:<id>`) returns `200`
with the original `batch_id` and `"duplicate_request": true`. A `FAILED` batch is
re-queued instead and resumes from its checkpoint.

### Upload

```http
POST /ingestions/upload   (multipart/form-data)
source=payments_core, file=@batch.csv, expected_count=100 (optional), sequence_number (optional)
```
- `.csv` (header required) or `.jsonl`/`.ndjson`, UTF-8, up to `MAX_UPLOAD_BYTES`.
- The request key is the file's SHA-256, so uploading the same bytes again returns the
  original batch.
- `422` examples: unsupported extension, non-UTF-8 content,
  `{"code":"validation_failed","message":"CSV header is missing required columns","details":{"missing_columns":["transaction_id",…],"header":[…]}}`,
  and `file_too_large`.

### Batches

`GET /batches?source=&status=&limit=&offset=` · `GET /batches/{id}` (row accounting,
rule results with evidence, runs, run log) · `POST /batches/{id}/resume` (`409` if already
finished).

## Quality

`GET /quality/rules` · `GET /quality/results?batch_id=&rule_key=&passed=&since=&until=`

```http
PATCH /quality/rules/{id}
{"configuration": {"max_failure_pct": 2}, "severity": "medium", "expected_version": 1, "reason": "tighten late-event budget"}
```
Returns the rule with `version` incremented. Possible errors: `403` for non-admins, `409`
`rule_locked` when disabling a locked rule, `409` when `expected_version` is stale, and
`422` for unknown keys or out-of-range values.

## Incidents

`GET /incidents?state=&severity=&open_only=&batch_id=&q=` · `GET /incidents/{id}` returns
the incident plus `diagnosis`, `evidence[]`, `timeline[]` (audit events for the incident,
its proposals and jobs), `proposals[]` and `recoveries[]`.

Transitions, each requiring a `note` and optionally `expected_version`:

- `POST /incidents/{id}/acknowledge`
- `POST /incidents/{id}/close` (closes as `CLOSED_AS_FALSE_POSITIVE`)
- `POST /incidents/{id}/diagnose` → `202` (queued; rate-limited)

```json
409
{"code":"invalid_transition","message":"Invalid transition RESOLVED -> ACKNOWLEDGED","details":{"from":"RESOLVED","to":"ACKNOWLEDGED","allowed":[]},"correlation_id":"aa55c0a9679e4b05a5e0612a026cdd54"}
```
```json
404
{"code":"not_found","message":"Incident not found","details":null,"correlation_id":"91460f19e5644ad1aad4facbb11087ac"}
```

Diagnosis shape:
```json
{
  "provider": "deterministic",
  "suspected_causes": [
    {"cause_key": "non_idempotent_retry", "cause": "Previously delivered events were re-sent (non-idempotent upstream retry)",
     "explanation": "69 skipped rows match events already accepted from earlier batches, 69 of them from batch seq 13. …",
     "confidence_label": "high", "supporting_evidence_ids": ["13e99acf-…", "c792048d-…"], "contradicting_evidence_ids": []}
  ],
  "recommended_actions": [{"action": "SKIP_IDENTICAL_DUPLICATE", "executable": true, "risk": "low", "description": "…", "target_hint": {}}],
  "limitations": ["Heuristic ranking from failure signatures; confidence labels are not probabilities.", "…"],
  "ai_enrichment": {"status": "disabled", "unverified": true}
}
```

## Lineage

- `GET /lineage` returns `{nodes: [{node_key, node_type, display_name, metadata, last_refreshed_at}], edges: [{upstream, downstream}]}`.
- `GET /incidents/{id}/impact` returns `{origin, status: "computed"|"unknown", granularity: "dataset", impacted: [{node_key, depth, path, confidence, last_refreshed_at}], note}`.

## Quarantine

`GET /quarantine?batch_id=&state=&code=` · `GET /quarantine/{id}` (error details, raw
payload with `customer_id` masked, repair candidate) · `GET /quarantine/export.csv` (same
filters; cells beginning with `= + - @` or a tab are prefixed with `'`).

## Repairs and recoveries

```http
POST /repairs/proposals
{"incident_id": "c4520fb6-…", "action": "MANUAL_REVIEW_REQUIRED",
 "justification": "Negative amounts need a corrected redelivery from the producer",
 "target": {"error_codes": ["AMOUNT_NON_POSITIVE"]}}
```
```json
201
{"id": "4e8043be-…", "action": "MANUAL_REVIEW_REQUIRED", "target_count": 20, "risk_level": "low", "status": "PENDING",
 "proposed_patch": {"kind": "state_change", "rows": [{"quarantine_id": "190d3b27-…", "event_id": "6f03177f-…", "error_codes": ["AMOUNT_NON_POSITIVE"]}]},
 "dry_run_summary": null, "version": 1, …}
```
`target` may only narrow the server-resolved set, using `quarantine_ids`, `error_codes` or
`sequence_numbers`. Any other key is a `422`. `409 no_eligible_targets` means nothing
qualifies.

```http
POST /recoveries/dry-run
{"proposal_id": "4e8043be-…"}
```
```json
200
{"proposal_id":"4e8043be-…","risk_level":"low","action":"MANUAL_REVIEW_REQUIRED",
 "counts":{"would_insert":0,"would_update":20,"would_skip":0,"would_remain_quarantined":0,"blocked":0},
 "notes":["Records stay quarantined and are flagged for manual review."],"target_count":20}
```

Approve or reject: `POST /repairs/proposals/{id}/approve` `{"note": "…"}` and
`POST /repairs/proposals/{id}/reject` `{"note": "required"}`.

```json
409
{"code":"dry_run_required","message":"Run a dry run before approving","details":null,"correlation_id":"0d9b23b26fdf4ea68e990a77036bf8e1"}
```

```http
POST /recoveries
{"proposal_id": "4e8043be-…", "idempotency_key": "docs-example-0001"}
```
```json
403 (proposal not approved)
{"code":"approval_required","message":"Recovery requires an approved proposal","details":{"proposal_status":"PENDING"},"correlation_id":"67e592c9f3254d85b2153b92be35219b"}
```
```json
202
{"outcome": "created", "job": {"id": "baa651a4-…", "state": "QUEUED", "idempotency_key": "docs-example-0001", "attempts": 0}, "queue_job_id": "…"}
```
Submitting the same key again returns `200` with `"outcome": "completed"` once the job has
succeeded, `"in_progress"` while it runs, and `202 "retry"` if it failed or its worker died.
The same key with a different proposal returns `409 idempotency_key_reused`.
`chaos_crash_after: N` is accepted only when `CHAOS_ENABLED=true` (otherwise `403
chaos_disabled`).

`GET /recoveries/{id}`:
```json
{
  "state": "SUCCEEDED", "attempts": 1,
  "verification": {
    "passed": true,
    "checks": [
      {"name": "no_duplicate_event_ids", "passed": true, "detail": "0 duplicated event_ids in accepted rows"},
      {"name": "counts_reconcile", "passed": true, "detail": "accepted 480 (rows 480) + rejected 20 + skipped 0 = processed 500"},
      {"name": "business_data_unchanged", "passed": true, "detail": "accepted_transactions checksum before == after"}
    ],
    "outstanding": {"amount_range": 0}, "outstanding_total": 0,
    "checksum_before": "7a673506…", "checksum_after": "7a673506…"
  },
  "audit": [{"action": "recovery.requested", …}, {"action": "recovery.started", …}, {"action": "recovery.succeeded", …}]
}
```

## Simulations

```http
POST /simulations
{"scenario": "invalid_amount", "source": "payments_core", "records": 500, "corruption_pct": 4}
```
```json
202
{"simulation": {"id": "cfc04c27-…", "scenario": "invalid_amount", "seed": 420015, "record_count": 500,
                "corruption_pct": 4.0, "batch_id": "8d3372f9-…", "evaluation": null},
 "run_id": "c15e78f7-…", "job_id": "e8b09290-…"}
```
Poll `GET /simulations/{id}` until `evaluation` is set. It holds `expected`, `actual`,
`counts_match`, `quarantine_true_positives/false_positives/false_negatives`, `incident_id`
and `detection_latency_seconds`. `GET /simulations/scenarios` lists the scenarios.
`stale_pipeline` runs synchronously and returns `freshness_results`.

```json
403
{"code":"forbidden","message":"Role 'analyst' lacks permission 'simulate'","details":{"required":"simulate"},"correlation_id":"a9a5643968834adeaaa18bafd306903e"}
```

When Redis is unreachable:
```json
503
{"code":"queue_unavailable","message":"Task queue unavailable; the request was not accepted","details":null,"correlation_id":"…"}
```

## Metrics and audit

- `GET /metrics/overview`: batch counts by status, row totals, quarantine rate, open
  incidents by severity, MTTD, mean incident duration, recovery success rate and recent
  jobs, per-source freshness, and simulation FP/FN totals.
- `GET /metrics/timeseries?bucket=hour|day&hours=48`: rows by outcome, batches, incidents
  opened, pass rate per bucket.
- `GET /metrics/revenue`: rows from `analytics.mart_daily_revenue`, or
  `{"available": false}` if dbt hasn't run.
- `GET /audit?entity_type=&entity_id=&action=&actor_id=&since=` (admin).

```json
422
{"code":"validation_failed","message":"Request validation failed","details":[{"loc":["query","limit"],"msg":"Input should be less than or equal to 200","type":"less_than_equal"}],"correlation_id":"0f66c79f82584d3383971629e912964c"}
```
