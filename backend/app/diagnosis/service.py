"""Run a diagnosis for an incident and store it."""

import json
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.diagnosis.deterministic import DeterministicProvider
from app.diagnosis.llm import Completion, enrich
from app.diagnosis.schema import Diagnosis, DiagnosisContext, EvidenceItem
from app.errors import ConflictError, NotFoundError
from app.lineage.service import incident_impact
from app.models import Incident, IncidentEvidence
from app.models.enums import IncidentState
from app.recovery.targets import repairability
from app.services.audit import Actor, record_audit
from app.services.incidents import TERMINAL, add_evidence, transition

# Evidence kinds recomputed on every diagnosis (derived, not observed).
DERIVED_KINDS = ("repairability", "impact")


def build_context(session: Session, incident: Incident) -> DiagnosisContext:
    evidence = session.scalars(
        select(IncidentEvidence)
        .where(IncidentEvidence.incident_id == incident.id)
        .order_by(IncidentEvidence.created_at)
    ).all()
    return DiagnosisContext(
        incident_id=str(incident.id),
        category=incident.category,
        severity=incident.severity,
        failed_rules=list(incident.failed_rules),
        title=incident.title,
        summary=incident.summary,
        evidence=[EvidenceItem(id=str(e.id), kind=e.kind, payload=e.payload) for e in evidence],
    )


def diagnose_incident(
    session: Session,
    incident_id: uuid.UUID,
    actor: Actor,
    *,
    completion: Completion | None = None,
) -> Diagnosis:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise NotFoundError("Incident not found")
    if incident.state in TERMINAL or incident.state == IncidentState.RECOVERING:
        raise ConflictError(f"Cannot diagnose an incident in state {incident.state}")
    if incident.state != IncidentState.INVESTIGATING:
        transition(session, incident, IncidentState.INVESTIGATING, actor, note="diagnosis started")

    session.execute(
        delete(IncidentEvidence).where(
            IncidentEvidence.incident_id == incident.id, IncidentEvidence.kind.in_(DERIVED_KINDS)
        )
    )
    add_evidence(session, incident, "repairability", repairability(session, incident))
    impact = json.loads(json.dumps(incident_impact(session, incident), default=str))
    add_evidence(session, incident, "impact", impact)
    session.flush()

    ctx = build_context(session, incident)
    diagnosis = DeterministicProvider().diagnose(ctx)
    diagnosis.ai_enrichment = enrich(ctx, diagnosis, get_settings(), completion)

    incident.diagnosis = diagnosis.model_dump(mode="json")
    transition(session, incident, IncidentState.ACTION_REQUIRED, actor, note="diagnosis complete")
    record_audit(
        session,
        actor,
        "incident.diagnosed",
        "incident",
        incident.id,
        after={"causes": [c.cause_key for c in diagnosis.suspected_causes]},
        details={
            "provider": diagnosis.provider,
            "ai_status": diagnosis.ai_enrichment.status,
            "top_cause": diagnosis.suspected_causes[0].cause_key
            if diagnosis.suspected_causes
            else None,
        },
    )
    return diagnosis
