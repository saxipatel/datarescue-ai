# Data contracts

## Transaction event (schema version 1)

| Field | Type | Rule |
|---|---|---|
| `event_id` | UUID string | Unique per event. If missing, ingestion derives a stable UUIDv5 from the source name and payload hash. |
| `transaction_id` | string | Required. Stable business ID shared by an event's lifecycle (PENDING → SETTLED/FAILED → REFUNDED). |
| `customer_id` | string | Required. Synthetic only (`C00042`). |
| `merchant_id` | string | Required by the contract (missing → `SCHEMA_MISSING_FIELD`). |
| `amount` | decimal string or exact JSON number | Required; > 0, at most 2 decimals, ≤ `max_amount` (rule config, default 50,000.00). Floats from Python are rejected; JSON is parsed with `Decimal`. |
| `currency` | string | Required; uppercase three-letter code in the allowlist (default USD, EUR, GBP). |
| `status` | enum | `PENDING`, `SETTLED`, `FAILED` or `REFUNDED`, case-sensitive. |
| `event_time` | ISO-8601 with offset | Required. Normalized to UTC; timestamps without an offset are rejected as ambiguous. Not more than 5 minutes in the future, not before 2000-01-01. |
| `source_system` | string | Required by the contract. |
| `schema_version` | integer | Must be in the rule's `supported_versions` (default: the source's `contract_version`). |

Assigned by ingestion, not by the producer: `ingested_at` (`raw_events.received_at`),
`batch_id`, and `payload_hash`, which is a SHA-256 of a canonical serialization where
every scalar is stringified and keys are sorted. That makes the hash independent of
CSV-versus-JSON formatting. Any other field is unexpected (`SCHEMA_UNEXPECTED_FIELD`)
unless the schema rule's `reject_unexpected_fields` is turned off.

Accepted CSV uploads need at least the required columns in the header. Empty cells mean
"absent", and a row with the wrong number of columns is treated as corrupted.

### Lateness

Each batch keeps an event-time watermark: the maximum `event_time` of rows accepted so far,
in delivery order. A valid row more than the source's `lateness_grace_minutes` (default 15)
behind the watermark is accepted with a `LATE_EVENT` warning. Late rows, duplicates and
rejected rows never advance the watermark.

## Error codes

| Code | Rule | Disposition |
|---|---|---|
| `MISSING_REQUIRED_FIELD` | required_fields | quarantined |
| `SCHEMA_MISSING_FIELD` | schema_contract | quarantined |
| `SCHEMA_UNEXPECTED_FIELD` | schema_contract | quarantined (configurable) |
| `SCHEMA_VERSION_UNSUPPORTED` | schema_contract | quarantined |
| `INVALID_FIELD_TYPE` | schema_contract | quarantined |
| `INVALID_EVENT_ID` | schema_contract | quarantined |
| `TIMESTAMP_IMPOSSIBLE` | schema_contract | quarantined |
| `CORRUPTED_PAYLOAD` | schema_contract | quarantined (row could not be parsed) |
| `AMOUNT_NON_POSITIVE` | amount_range | quarantined |
| `AMOUNT_INVALID_PRECISION` | amount_range | quarantined |
| `AMOUNT_EXCEEDS_MAX` | amount_range | quarantined |
| `CURRENCY_INVALID_FORMAT` | currency_allowlist | quarantined (lowercase valid codes are repairable) |
| `CURRENCY_NOT_ALLOWED` | currency_allowlist | quarantined |
| `STATUS_UNKNOWN` | enum_status | quarantined |
| `DUPLICATE_CONFLICT` | event_uniqueness | quarantined |
| `DUPLICATE_IDENTICAL` | event_uniqueness | skipped (counted, not quarantined) |
| `LATE_EVENT` | late_event | accepted with warning |

## Quality rules and thresholds

Defaults from [`quality/catalog.py`](../backend/app/quality/catalog.py). Admins can change
severity, thresholds and, for unlocked rules, the enabled flag. Every change increments
`version` and is audited with before and after values. Locked rules protect database
constraints, so they can't be disabled.

| Rule | Severity | Threshold | Locked | Notes |
|---|---|---|---|---|
| required_fields | high | 0% | yes | |
| amount_range | high | 0% | yes | `max_amount` editable |
| currency_allowlist | medium | 0% | yes | `allowed_currencies` editable |
| enum_status | medium | 0% | yes | |
| event_uniqueness | high | 0% | yes | identical skips + conflicts both count as failures |
| schema_contract | critical | 0% | yes | `supported_versions`, `reject_unexpected_fields` |
| completeness | high | received ≥ expected × `min_ratio`, no sequence gaps | no | evaluated when a manifest count or sequence number exists |
| volume_anomaly | medium | within ±`tolerance_pct` (50) of the median of the last `window` (10) successful batches | no | needs `min_history` (3) batches; a heuristic band, not a significance test |
| late_event | low | ≤ 5% late | no | low severity: recorded, no incident |
| freshness | medium | last successful batch within the source's `freshness_max_age_minutes` (24 h) | no | evaluated by the scheduler, per source |

A failed rule with severity medium or above opens an incident. A batch has at most one
incident (`incident_key = batch:<id>`), and a source has at most one open freshness
incident.

Batch status: `COMPLETE` if nothing was quarantined, `PARTIAL` if some rows were, and
`FAILED` if every processed row was quarantined or the run errored (resumable).

## Row accounting

For every batch: `processed = accepted + rejected (quarantined) + skipped`. `skipped`
counts identical repeats inside the batch and identical redeliveries of already-accepted
events. `received` equals `processed` once the batch finishes. A recovery that moves a
quarantined row into accepted shifts one count from rejected to accepted (or to skipped if
the row turned out to be present already). `processed` never changes after ingestion.

## Revenue semantics (dbt)

`mart_daily_revenue` sums SETTLED amounts per UTC day and currency. FAILED and PENDING are
excluded. REFUNDED amounts are reported in separate columns and are **not** subtracted,
because the event stream carries no separate refund amount to net against. Currencies are
never converted or summed together. `revenue_reconciles_with_staging` checks that mart
totals equal the settled sum in staging.

## Synthetic data and fault scenarios

[`datagen/generator.py`](../backend/app/datagen/generator.py) is seeded and streams rows.
Transactions move through PENDING → SETTLED/FAILED and sometimes REFUNDED. There are 40
merchants and 2,000 repeat customers, offsets are mixed (UTC, −05:00, +05:30), and about
5% of rows arrive a few minutes late but within grace. Faulty rows are labelled with the
outcome the engine should produce.

| Scenario | Fault(s) | Expected |
|---|---|---|
| duplicate_event_ids | identical copies from the previous batch (≈70%) or earlier in the batch | skipped |
| conflicting_duplicates | reused event_id, amount + 1.00 | quarantined |
| null_customer_id | customer_id null | quarantined |
| invalid_amount | negative (80%), zero (20%) | quarantined |
| malformed_currency | lowercase (70%, repairable), invalid code (30%) | quarantined |
| unknown_status | `SETLED`, `CHARGEBACK`, `settled` | quarantined |
| schema_break | trailing rows switch to v2 with `amount_minor` | quarantined |
| unexpected_field / missing_field | extra `promo_code` / no `merchant_id` | quarantined |
| impossible_timestamp | year 2099 | quarantined |
| late_events / out_of_order | behind the watermark by grace + 30–240 / + 5–30 min | accepted with warning |
| corrupted_payload | truncated JSON line | quarantined |
| mixed_faults | mixture of the above | per fault |
| missing_batch | sequence N skipped, N+1 delivered | completeness incident |
| stale_pipeline | clock advanced past the freshness window | freshness incident |
| volume_drop | fewer rows than the manifest declares | completeness incident; volume_anomaly too once the drop exceeds its band |

Fixture files (`make fixtures`): `clean_1000`, `duplicate_1000`, `invalid_amount_1000`,
`schema_break_1000`, `late_events`, `mixed_faults_10000` and `missing_batch` (seq1 + seq3).
Each has a `.manifest.json` with parameters and expected counts, plus a `.labels.jsonl` with
one line per faulty row. `data/sample/upload_sample_100.csv` is a small mixed-fault file
for trying the upload path.

## Database tables

| Table | Purpose |
|---|---|
| `users` | email, argon2 hash, role, active |
| `source_systems` | name, contract version, grace and freshness windows, synthetic generator settings |
| `datasets` | generated files and their ground-truth manifests |
| `ingestion_batches` | one per delivery: status, counters, checkpoint, checksum, request key, sequence |
| `raw_events` | every distinct received row with its payload, hash, occurrences, disposition |
| `accepted_transactions` | validated rows; `NUMERIC(18,2)` amounts; unique `event_id`; `recovery_job_id` when written by a repair |
| `quarantine_records` | rejected rows: codes, details, original payload, state, repair candidate |
| `quality_rules`, `quality_results` | versioned rule config; per-batch (or per-source for freshness) outcomes with evidence |
| `pipeline_runs`, `pipeline_run_events` | queued work with attempts, plus structured log lines used as diagnosis evidence |
| `incidents`, `incident_evidence` | lifecycle state with optimistic version, diagnosis JSON, evidence items |
| `lineage_nodes`, `lineage_edges` | declared dependencies |
| `repair_proposals` | bounded target, preview, risk, dry-run summary, review |
| `recovery_jobs` | idempotency key, state, attempts, heartbeat, metrics, verification |
| `audit_events` | append-only: actor, action, entity, before and after hashes, details, correlation ID |
| `simulation_runs` | fault injections with labels and evaluation |
