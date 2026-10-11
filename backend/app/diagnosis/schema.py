from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Confidence = Literal["low", "medium", "high"]


class EvidenceItem(BaseModel):
    id: str
    kind: str
    payload: dict[str, Any]


class DiagnosisContext(BaseModel):
    """Everything a provider may look at. Built from the DB, already redacted."""

    incident_id: str
    category: str
    severity: str
    failed_rules: list[str]
    title: str
    summary: str
    evidence: list[EvidenceItem]

    def by_kind(self, kind: str) -> list[EvidenceItem]:
        return [e for e in self.evidence if e.kind == kind]

    def first(self, kind: str) -> EvidenceItem | None:
        items = self.by_kind(kind)
        return items[0] if items else None

    def rule_failure(self, rule_key: str) -> EvidenceItem | None:
        for item in self.by_kind("rule_failure"):
            if item.payload.get("rule_key") == rule_key:
                return item
        return None


class SuspectedCause(BaseModel):
    cause_key: str
    cause: str
    explanation: str
    confidence_label: Confidence
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)


class RecommendedAction(BaseModel):
    action: str
    executable: bool
    description: str
    risk: str
    target_hint: dict[str, Any] = Field(default_factory=dict)


class AIEnrichment(BaseModel):
    status: Literal["ok", "disabled", "error", "invalid_output", "refused"]
    provider: str | None = None
    model: str | None = None
    summary: str | None = None
    considerations: list[str] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    cited_evidence_ids: list[str] = Field(default_factory=list)
    unverified: bool = True
    error: str | None = None


class Diagnosis(BaseModel):
    provider: str
    generated_at: datetime
    suspected_causes: list[SuspectedCause]
    recommended_actions: list[RecommendedAction]
    limitations: list[str]
    ai_enrichment: AIEnrichment | None = None
