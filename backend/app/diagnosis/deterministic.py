"""Rules-based root-cause ranking.

Each check inspects failure signatures in the evidence and may return a hypothesis with
the evidence IDs that support or contradict it. Confidence labels are heuristic
(low/medium/high), not calibrated probabilities. Nothing here modifies data; actions are
recommendations that still need a proposal, a dry run and approval.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from app.diagnosis.schema import (
    Diagnosis,
    DiagnosisContext,
    RecommendedAction,
    SuspectedCause,
)
from app.models.enums import RepairAction

_RANK = {"high": 3, "medium": 2, "low": 1}

Check = Callable[[DiagnosisContext], tuple[list[SuspectedCause], list[RecommendedAction]]]


def _ids(*items: object) -> list[str]:
    return [i.id for i in items if i is not None]  # type: ignore[attr-defined]


def _repairability(ctx: DiagnosisContext) -> dict:
    item = ctx.first("repairability")
    return item.payload if item else {}


def _breakdown(ctx: DiagnosisContext) -> dict[str, int]:
    meta = ctx.first("batch_metadata")
    return dict(meta.payload.get("error_code_breakdown", {})) if meta else {}


def check_schema(ctx: DiagnosisContext) -> tuple[list[SuspectedCause], list[RecommendedAction]]:
    failure = ctx.rule_failure("schema_contract")
    if failure is None:
        return [], []
    meta = ctx.first("batch_metadata")
    codes = _breakdown(ctx)
    causes: list[SuspectedCause] = []
    actions: list[RecommendedAction] = []
    contract = meta.payload.get("source_contract_version") if meta else None
    versions = meta.payload.get("schema_versions_observed", {}) if meta else {}
    foreign = {v: n for v, n in versions.items() if str(v) != str(contract)}
    failed = failure.payload.get("failed_count", 0)

    if foreign:
        foreign_rows = sum(foreign.values())
        # Strong signal when the rows on the new version account for most failures.
        aligned = failed > 0 and foreign_rows >= 0.8 * failed
        causes.append(
            SuspectedCause(
                cause_key="upstream_schema_change",
                cause="Upstream schema change incompatible with the contract",
                explanation=(
                    f"{foreign_rows} rows carry schema_version {sorted(foreign)} while the source "
                    f"contract is v{contract}; {failed} rows failed schema checks. Missing/unexpected "
                    "field codes are consistent with renamed or moved fields."
                ),
                confidence_label="high" if aligned else "medium",
                supporting_evidence_ids=_ids(failure, meta),
            )
        )
    elif codes.get("SCHEMA_UNEXPECTED_FIELD") or codes.get("SCHEMA_MISSING_FIELD"):
        causes.append(
            SuspectedCause(
                cause_key="unversioned_field_change",
                cause="Producer added or dropped fields without bumping schema_version",
                explanation=(
                    f"Unexpected-field={codes.get('SCHEMA_UNEXPECTED_FIELD', 0)}, "
                    f"missing-field={codes.get('SCHEMA_MISSING_FIELD', 0)} while every row reports "
                    "the contracted version."
                ),
                confidence_label="medium",
                supporting_evidence_ids=_ids(failure, meta),
            )
        )
    if codes.get("CORRUPTED_PAYLOAD"):
        causes.append(
            SuspectedCause(
                cause_key="payload_corruption",
                cause="Rows corrupted in transport or serialization",
                explanation=f"{codes['CORRUPTED_PAYLOAD']} rows could not be parsed as JSON/CSV.",
                confidence_label="medium",
                supporting_evidence_ids=_ids(failure, meta),
            )
        )
    if codes.get("TIMESTAMP_IMPOSSIBLE") or codes.get("INVALID_FIELD_TYPE"):
        causes.append(
            SuspectedCause(
                cause_key="type_or_clock_error",
                cause="Invalid field types or impossible timestamps from the producer",
                explanation=(
                    f"impossible timestamps={codes.get('TIMESTAMP_IMPOSSIBLE', 0)}, "
                    f"type errors={codes.get('INVALID_FIELD_TYPE', 0)}; often a clock or "
                    "serialization bug upstream."
                ),
                confidence_label="low",
                supporting_evidence_ids=_ids(failure),
            )
        )
    actions.append(
        RecommendedAction(
            action=RepairAction.MANUAL_REVIEW_REQUIRED,
            executable=True,
            description=(
                "Keep affected rows quarantined and flag them for manual review. Schema changes are "
                "ambiguous; values are never guessed. Once the contract or producer is fixed, "
                "replay the quarantined rows with REPLAY_QUARANTINED."
            ),
            risk="low",
        )
    )
    return causes, actions


def check_duplicates(ctx: DiagnosisContext) -> tuple[list[SuspectedCause], list[RecommendedAction]]:
    failure = ctx.rule_failure("event_uniqueness")
    if failure is None:
        return [], []
    analysis = ctx.first("duplicate_analysis")
    logs = ctx.first("pipeline_log")
    details = failure.payload.get("details", {})
    identical = int(details.get("identical_duplicates_skipped", 0))
    conflicts = int(details.get("conflicting_duplicates_quarantined", 0))
    cross = int(analysis.payload.get("cross_batch_duplicates", 0)) if analysis else 0
    in_batch = int(analysis.payload.get("in_batch_repeats", 0)) if analysis else 0
    origins = analysis.payload.get("originals_in_previous_batches", []) if analysis else []
    causes: list[SuspectedCause] = []
    actions: list[RecommendedAction] = []

    if identical and cross:
        top = origins[0] if origins else {}
        concentrated = bool(top) and top.get("count", 0) >= 0.7 * cross
        causes.append(
            SuspectedCause(
                cause_key="non_idempotent_retry",
                cause="Previously delivered events were re-sent (non-idempotent upstream retry)",
                explanation=(
                    f"{cross} skipped rows match events already accepted from earlier batches"
                    + (
                        f", {top.get('count')} of them from batch seq {top.get('sequence_number')}"
                        if top
                        else ""
                    )
                    + ". Payloads are identical, which fits a replay of the same data rather than new "
                    "activity."
                ),
                confidence_label="high" if concentrated and cross >= 0.5 * identical else "medium",
                supporting_evidence_ids=_ids(failure, analysis),
                contradicting_evidence_ids=_ids(logs) if in_batch > cross else [],
            )
        )
    if in_batch:
        causes.append(
            SuspectedCause(
                cause_key="at_least_once_duplicates",
                cause="Producer emitted repeated events within the batch (at-least-once delivery)",
                explanation=f"{in_batch} identical rows repeated inside this batch.",
                confidence_label="medium",
                supporting_evidence_ids=_ids(failure, analysis),
            )
        )
    if identical:
        actions.append(
            RecommendedAction(
                action=RepairAction.SKIP_IDENTICAL_DUPLICATE,
                executable=True,
                description=(
                    f"Confirm the {identical} identical duplicates as safely skipped after re-checking "
                    "each payload hash against the accepted row. No accepted rows are added."
                ),
                risk="low",
            )
        )
    if conflicts:
        causes.append(
            SuspectedCause(
                cause_key="event_id_reuse",
                cause="Event IDs reused with different payloads",
                explanation=(
                    f"{conflicts} rows share an event_id with an accepted row but differ in content; "
                    "either an upstream mutation or an ID generation bug."
                ),
                confidence_label="medium",
                supporting_evidence_ids=_ids(failure),
            )
        )
        actions.append(
            RecommendedAction(
                action=RepairAction.MANUAL_REVIEW_REQUIRED,
                executable=True,
                description="Conflicting duplicates need a human decision; never pick a winner "
                "automatically.",
                risk="low",
            )
        )
    return causes, actions


def check_values(ctx: DiagnosisContext) -> tuple[list[SuspectedCause], list[RecommendedAction]]:
    codes = _breakdown(ctx)
    repair = _repairability(ctx)
    causes: list[SuspectedCause] = []
    actions: list[RecommendedAction] = []

    amount = ctx.rule_failure("amount_range")
    if amount:
        non_pos = codes.get("AMOUNT_NON_POSITIVE", 0)
        too_big = codes.get("AMOUNT_EXCEEDS_MAX", 0)
        if non_pos:
            causes.append(
                SuspectedCause(
                    cause_key="amount_sign_or_encoding",
                    cause="Non-positive amounts from the producer (sign or refund encoding change)",
                    explanation=f"{non_pos} rows have amount <= 0. Refunds are modelled as a REFUNDED "
                    "status, not negative amounts.",
                    confidence_label="medium",
                    supporting_evidence_ids=_ids(amount, ctx.first("sample_records")),
                )
            )
        if too_big:
            causes.append(
                SuspectedCause(
                    cause_key="amount_unit_change",
                    cause="Amounts above the configured maximum (possible minor-unit change)",
                    explanation=f"{too_big} rows exceed max_amount.",
                    confidence_label="low",
                    supporting_evidence_ids=_ids(amount),
                )
            )
        actions.append(
            RecommendedAction(
                action=RepairAction.MANUAL_REVIEW_REQUIRED,
                executable=True,
                risk="low",
                description="Invalid amounts are never corrected automatically; flag for review.",
            )
        )

    currency = ctx.rule_failure("currency_allowlist")
    if currency:
        repairable = int(repair.get("normalize_currency_candidates", 0))
        failed = int(currency.payload.get("failed_count", 0))
        if repairable:
            causes.append(
                SuspectedCause(
                    cause_key="currency_case_regression",
                    cause="Upstream stopped upper-casing currency codes",
                    explanation=f"{repairable} of {failed} currency failures are valid codes in the "
                    "wrong case.",
                    confidence_label="high" if repairable == failed else "medium",
                    supporting_evidence_ids=_ids(currency, ctx.first("repairability")),
                )
            )
            actions.append(
                RecommendedAction(
                    action=RepairAction.NORMALIZE_CURRENCY_CASE,
                    executable=True,
                    risk="medium",
                    description=f"Upper-case the currency on {repairable} rows whose code is otherwise "
                    "allowed; preview shows before/after for each row.",
                )
            )
        if failed > repairable:
            causes.append(
                SuspectedCause(
                    cause_key="unsupported_currency",
                    cause="Unsupported or malformed currency codes",
                    explanation=f"{failed - repairable} rows use codes outside the allowlist.",
                    confidence_label="medium",
                    supporting_evidence_ids=_ids(currency),
                )
            )
            actions.append(
                RecommendedAction(
                    action=RepairAction.REPLAY_QUARANTINED,
                    executable=True,
                    risk="medium",
                    description="If the allowlist is intentionally extended by an admin, replay the "
                    "quarantined rows; otherwise flag them for manual review.",
                )
            )

    required = ctx.rule_failure("required_fields")
    if required:
        fields = repair.get("field_breakdown", {})
        top_field, top_count = max(fields.items(), key=lambda kv: kv[1]) if fields else (None, 0)
        failed = int(required.payload.get("failed_count", 0))
        causes.append(
            SuspectedCause(
                cause_key="upstream_enrichment_gap",
                cause=f"Upstream join or enrichment left {top_field or 'required fields'} empty",
                explanation=f"{failed} rows miss required fields"
                + (f"; {top_count} of them are {top_field}" if top_field else "")
                + ".",
                confidence_label="medium" if top_field and top_count >= 0.8 * failed else "low",
                supporting_evidence_ids=_ids(required, ctx.first("repairability")),
            )
        )
        actions.append(
            RecommendedAction(
                action=RepairAction.MANUAL_REVIEW_REQUIRED,
                executable=True,
                risk="low",
                description="Missing identifiers cannot be inferred; request a corrected redelivery.",
            )
        )

    status = ctx.rule_failure("enum_status")
    if status:
        causes.append(
            SuspectedCause(
                cause_key="new_status_values",
                cause="Producer sends status values outside the contract",
                explanation=f"{status.payload.get('failed_count')} rows have unknown statuses "
                "(new lifecycle state or misspelling).",
                confidence_label="medium",
                supporting_evidence_ids=_ids(status, ctx.first("sample_records")),
            )
        )
        actions.append(
            RecommendedAction(
                action=RepairAction.MANUAL_REVIEW_REQUIRED,
                executable=True,
                risk="low",
                description="Agree the status mapping with the producer before replaying.",
            )
        )
    return causes, actions


def check_delivery(ctx: DiagnosisContext) -> tuple[list[SuspectedCause], list[RecommendedAction]]:
    causes: list[SuspectedCause] = []
    actions: list[RecommendedAction] = []
    complete = ctx.rule_failure("completeness")
    if complete:
        details = complete.payload.get("details", {})
        gaps = details.get("missing_sequences", [])
        if gaps:
            causes.append(
                SuspectedCause(
                    cause_key="missing_batch",
                    cause=f"Batch sequence {gaps} was never delivered",
                    explanation="A later sequence number arrived while earlier ones are absent.",
                    confidence_label="high",
                    supporting_evidence_ids=_ids(complete, ctx.first("batch_metadata")),
                )
            )
            actions.append(
                RecommendedAction(
                    action=RepairAction.RETRY_MISSING_BATCH,
                    executable=True,
                    risk="medium",
                    description=f"Request redelivery of sequence(s) {gaps} with stable event IDs.",
                    target_hint={"missing_sequences": gaps},
                )
            )
        expected, received = details.get("expected_count"), details.get("received")
        if expected is not None and received is not None and received < expected:
            causes.append(
                SuspectedCause(
                    cause_key="truncated_delivery",
                    cause="Batch delivered fewer rows than its manifest declared",
                    explanation=f"Received {received} of {expected} expected rows.",
                    confidence_label="medium",
                    supporting_evidence_ids=_ids(complete),
                )
            )
    volume = ctx.rule_failure("volume_anomaly")
    if volume:
        details = volume.payload.get("details", {})
        causes.append(
            SuspectedCause(
                cause_key="volume_shift",
                cause="Batch volume outside the recent baseline band",
                explanation=f"Received {details.get('received')} vs median "
                f"{details.get('baseline_median')}. Heuristic band, not a significance "
                "test.",
                confidence_label="low",
                supporting_evidence_ids=_ids(volume),
            )
        )
    fresh = ctx.first("freshness_check")
    if fresh:
        causes.append(
            SuspectedCause(
                cause_key="stalled_source",
                cause="Upstream pipeline stalled or its scheduler stopped",
                explanation=f"No successful batch for {fresh.payload.get('age_minutes')} minutes.",
                confidence_label="medium",
                supporting_evidence_ids=_ids(fresh),
            )
        )
        actions.append(
            RecommendedAction(
                action="INVESTIGATE_UPSTREAM",
                executable=False,
                risk="low",
                description="Check the producer and its scheduler; nothing to replay until data "
                "arrives.",
            )
        )
    late = ctx.rule_failure("late_event")
    if late:
        causes.append(
            SuspectedCause(
                cause_key="upstream_delay",
                cause="Upstream delay or backfill delivering old events",
                explanation=f"{late.payload.get('failed_count')} events arrived behind the watermark "
                "by more than the grace window.",
                confidence_label="low",
                supporting_evidence_ids=_ids(late),
            )
        )
    return causes, actions


CHECKS: list[Check] = [check_schema, check_duplicates, check_values, check_delivery]

LIMITATIONS = [
    "Heuristic ranking from failure signatures; confidence labels are not probabilities.",
    "Lineage is dataset-level; row-level downstream effects are not traced.",
    "Diagnosis never modifies data. Every action requires a proposal, dry run and approval.",
]


class DeterministicProvider:
    name = "deterministic"

    def diagnose(self, ctx: DiagnosisContext) -> Diagnosis:
        causes: list[SuspectedCause] = []
        actions: list[RecommendedAction] = []
        for check in CHECKS:
            c, a = check(ctx)
            causes.extend(c)
            actions.extend(a)
        causes.sort(
            key=lambda c: (_RANK[c.confidence_label], len(c.supporting_evidence_ids)),
            reverse=True,
        )
        limitations = list(LIMITATIONS)
        if not causes:
            limitations.append("No known failure signature matched; investigate manually.")
        return Diagnosis(
            provider=self.name,
            generated_at=datetime.now(UTC),
            suspected_causes=causes,
            recommended_actions=_dedupe(actions),
            limitations=limitations,
        )


def _dedupe(actions: list[RecommendedAction]) -> list[RecommendedAction]:
    seen: dict[str, RecommendedAction] = {}
    for action in actions:
        if action.action in seen:
            # Keep the first description; merge hints.
            seen[action.action].target_hint.update(action.target_hint)
        else:
            seen[action.action] = action
    return list(seen.values())
