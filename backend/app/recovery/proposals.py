"""Repair proposals: creation, dry run, and the approval workflow."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.errors import ConflictError, NotFoundError, PermissionDenied, ValidationFailed
from app.models import Incident, RepairProposal, User
from app.models.enums import IncidentState, ProposalStatus, RepairAction, Severity
from app.recovery import targets
from app.security.rbac import Permission, has_permission
from app.services.audit import Actor, record_audit
from app.services.incidents import TERMINAL

# Only these keys may narrow a target set; anything else is rejected outright.
ALLOWED_TARGET_FILTERS = {"quarantine_ids", "error_codes", "sequence_numbers"}


def _snapshot(p: RepairProposal) -> dict[str, Any]:
    return {
        "status": p.status,
        "target_count": p.target_count,
        "risk": p.risk_level,
        "target": p.target,
    }


def create_proposal(
    session: Session,
    user: User,
    incident_id: uuid.UUID,
    action: RepairAction,
    justification: str,
    requested_target: dict[str, Any] | None = None,
) -> RepairProposal:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise NotFoundError("Incident not found")
    if incident.state in TERMINAL:
        raise ConflictError(f"Incident is {incident.state}; no further repairs")
    requested = requested_target or {}
    unknown = set(requested) - ALLOWED_TARGET_FILTERS
    if unknown:
        raise ValidationFailed(
            "Unsupported target filters",
            details={"unsupported": sorted(unknown), "allowed": sorted(ALLOWED_TARGET_FILTERS)},
        )
    if len(justification.strip()) < 10:
        raise ValidationFailed("Justification must explain the change (min 10 characters)")

    target, count, patch, risk = targets.resolve(session, incident, action, requested)
    if count == 0:
        raise ConflictError(
            "No records are eligible for this action",
            code="no_eligible_targets",
            details={"action": action},
        )
    proposal = RepairProposal(
        incident_id=incident.id,
        action=action,
        target=target,
        target_count=count,
        proposed_patch=patch,
        justification=justification.strip(),
        risk_level=risk,
        status=ProposalStatus.PENDING,
        created_by=user.id,
    )
    session.add(proposal)
    session.flush()
    record_audit(
        session,
        Actor.from_user(user),
        "proposal.created",
        "repair_proposal",
        proposal.id,
        after=_snapshot(proposal),
        details={"incident_id": incident.id, "action": action},
    )
    return proposal


def run_dry_run(session: Session, user: User, proposal: RepairProposal) -> dict[str, Any]:
    if proposal.status not in (ProposalStatus.PENDING, ProposalStatus.APPROVED):
        raise ConflictError(f"Proposal is {proposal.status}")
    summary = targets.dry_run(session, RepairAction(proposal.action), proposal.target)
    proposal.dry_run_summary = summary
    proposal.dry_run_at = datetime.now(UTC)
    session.flush()
    record_audit(
        session,
        Actor.from_user(user),
        "proposal.dry_run",
        "repair_proposal",
        proposal.id,
        details=summary,
    )
    return summary


def review(
    session: Session,
    user: User,
    proposal: RepairProposal,
    *,
    approve: bool,
    note: str | None,
    expected_version: int | None = None,
) -> RepairProposal:
    if not has_permission(user.role, Permission.APPROVE_REPAIR):
        raise PermissionDenied("Only stewards and admins can review repair proposals")
    if expected_version is not None and expected_version != proposal.version:
        raise ConflictError(
            "Proposal was modified concurrently",
            details={"expected_version": expected_version, "actual_version": proposal.version},
        )
    if proposal.status != ProposalStatus.PENDING:
        raise ConflictError(f"Proposal is already {proposal.status}")
    if approve:
        if proposal.dry_run_at is None:
            raise ConflictError("Run a dry run before approving", code="dry_run_required")
        if Severity(proposal.risk_level).rank >= Severity.HIGH.rank and (
            proposal.created_by == user.id
        ):
            raise PermissionDenied(
                "High-risk proposals need a reviewer other than the proposer",
                code="separation_of_duties",
            )
    elif not (note and note.strip()):
        raise ValidationFailed("A reason is required to reject a proposal")

    before = _snapshot(proposal)
    proposal.status = ProposalStatus.APPROVED if approve else ProposalStatus.REJECTED
    proposal.reviewed_by = user.id
    proposal.reviewed_at = datetime.now(UTC)
    proposal.review_note = note
    session.flush()
    record_audit(
        session,
        Actor.from_user(user),
        "proposal.approved" if approve else "proposal.rejected",
        "repair_proposal",
        proposal.id,
        before=before,
        after=_snapshot(proposal),
        details={"note": note, "incident_id": proposal.incident_id},
    )
    return proposal


def ensure_executable(proposal: RepairProposal, incident: Incident) -> None:
    if proposal.status == ProposalStatus.EXECUTED:
        raise ConflictError("Proposal was already executed", code="already_executed")
    if proposal.status != ProposalStatus.APPROVED:
        raise PermissionDenied(
            "Recovery requires an approved proposal",
            code="approval_required",
            details={"proposal_status": proposal.status},
        )
    # RECOVERING is allowed so a crashed job can be retried with the same key.
    allowed = {IncidentState.ACTION_REQUIRED, IncidentState.ACKNOWLEDGED, IncidentState.RECOVERING}
    if IncidentState(incident.state) not in allowed:
        raise ConflictError(
            f"Incident is {incident.state}; recovery needs ACTION_REQUIRED or ACKNOWLEDGED "
            "(run a diagnosis first)",
            code="invalid_incident_state",
        )
