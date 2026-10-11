// Shapes mirror backend/app/schemas/resources.py.

export type Role = "analyst" | "engineer" | "steward" | "admin";
export type Severity = "low" | "medium" | "high" | "critical";
export type IncidentState =
  | "OPEN"
  | "INVESTIGATING"
  | "ACTION_REQUIRED"
  | "ACKNOWLEDGED"
  | "RECOVERING"
  | "RESOLVED"
  | "CLOSED_AS_FALSE_POSITIVE";
export type BatchStatus = "PENDING" | "PROCESSING" | "COMPLETE" | "PARTIAL" | "FAILED";
export type RepairAction =
  | "SKIP_IDENTICAL_DUPLICATE"
  | "REPLAY_QUARANTINED"
  | "NORMALIZE_CURRENCY_CASE"
  | "RETRY_MISSING_BATCH"
  | "MANUAL_REVIEW_REQUIRED";

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  details: unknown;
  correlation_id: string | null;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  permissions?: string[];
}

export interface TokenResponse {
  access_token: string;
  expires_at: string;
  user: User;
}

export interface Source {
  id: string;
  name: string;
  description: string;
  contract_version: number;
  enabled: boolean;
  lateness_grace_minutes: number;
  freshness_max_age_minutes: number;
}

export interface Batch {
  id: string;
  source_id: string;
  source_name: string | null;
  sequence_number: number | null;
  status: BatchStatus;
  input_format: string;
  expected_count: number | null;
  received_count: number;
  processed_count: number;
  accepted_count: number;
  rejected_count: number;
  skipped_count: number;
  warning_count: number;
  started_at: string | null;
  finished_at: string | null;
  checksum: string | null;
  correlation_id: string;
  error_message: string | null;
  created_at: string;
  failed_rules: number;
  incident_id: string | null;
}

export interface QualityResult {
  id: string;
  batch_id: string | null;
  rule_key: string | null;
  rule_version: number;
  severity: Severity;
  passed: boolean;
  failed_count: number;
  checked_count: number;
  evidence: Record<string, unknown>;
  checked_at: string;
}

export interface RunOut {
  id: string;
  pipeline_name: string;
  state: string;
  attempts: number;
  started_at: string | null;
  finished_at: string | null;
  error_message: string | null;
  correlation_id: string;
}

export interface RunEvent {
  level: string;
  message: string;
  context: Record<string, unknown>;
  created_at: string;
}

export interface BatchDetail extends Batch {
  details: Record<string, unknown>;
  quality_results: QualityResult[];
  runs: RunOut[];
  run_events: RunEvent[];
}

export interface Rule {
  id: string;
  rule_key: string;
  description: string;
  severity: Severity;
  enabled: boolean;
  configuration: Record<string, unknown>;
  version: number;
  updated_at: string;
}

export interface QuarantineRecord {
  id: string;
  batch_id: string;
  event_id: string;
  error_codes: string[];
  state: string;
  created_at: string;
  resolved_at: string | null;
}

export interface QuarantineDetail extends QuarantineRecord {
  error_details: { code: string; field: string | null; message: string }[];
  raw_payload: Record<string, unknown>;
  repair_candidate: Record<string, unknown> | null;
  resolution_note: string | null;
  raw_event_id: string;
}

export interface Incident {
  id: string;
  incident_key: string;
  category: string;
  batch_id: string | null;
  source_id: string | null;
  origin_node_key: string;
  severity: Severity;
  state: IncidentState;
  title: string;
  summary: string;
  failed_rules: string[];
  detected_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  version: number;
}

export interface Evidence {
  id: string;
  kind: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface TimelineEntry {
  at: string;
  actor_id: string;
  actor_role: string | null;
  action: string;
  details: Record<string, unknown>;
}

export interface SuspectedCause {
  cause_key: string;
  cause: string;
  explanation: string;
  confidence_label: "low" | "medium" | "high";
  supporting_evidence_ids: string[];
  contradicting_evidence_ids: string[];
}

export interface RecommendedAction {
  action: string;
  executable: boolean;
  description: string;
  risk: string;
  target_hint: Record<string, unknown>;
}

export interface AIEnrichment {
  status: "ok" | "disabled" | "error" | "invalid_output" | "refused";
  provider: string | null;
  model: string | null;
  summary: string | null;
  considerations: string[];
  suggested_actions: string[];
  cited_evidence_ids: string[];
  unverified: boolean;
  error: string | null;
}

export interface Diagnosis {
  provider: string;
  generated_at: string;
  suspected_causes: SuspectedCause[];
  recommended_actions: RecommendedAction[];
  limitations: string[];
  ai_enrichment: AIEnrichment | null;
}

export interface Proposal {
  id: string;
  incident_id: string;
  action: RepairAction;
  target_count: number;
  proposed_patch: { kind?: string; field?: string; rows?: Record<string, unknown>[]; deliveries?: Record<string, unknown>[] };
  justification: string;
  risk_level: Severity;
  status: "PENDING" | "APPROVED" | "REJECTED" | "EXECUTED";
  created_by: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  dry_run_summary: DryRun | null;
  dry_run_at: string | null;
  created_at: string;
  version: number;
}

export interface DryRun {
  action: string;
  counts: {
    would_insert: number;
    would_update: number;
    would_skip: number;
    would_remain_quarantined: number;
    blocked: number;
  };
  notes: string[];
  target_count: number;
}

export interface VerificationCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface Recovery {
  id: string;
  incident_id: string;
  proposal_id: string | null;
  idempotency_key: string;
  state: "QUEUED" | "RUNNING" | "VERIFYING" | "SUCCEEDED" | "FAILED";
  target_batch_id: string | null;
  dry_run_summary: DryRun | null;
  metrics: {
    before?: Record<string, unknown>;
    after?: Record<string, unknown>;
    effects?: Record<string, unknown>;
    progress?: Record<string, number>;
  };
  verification: {
    passed: boolean;
    checks: VerificationCheck[];
    outstanding: Record<string, number>;
    outstanding_total: number;
    checksum_before: string | null;
    checksum_after: string | null;
  } | null;
  attempts: number;
  error_message: string | null;
  correlation_id: string;
  started_at: string | null;
  heartbeat_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface RecoveryDetail extends Recovery {
  audit: TimelineEntry[];
}

export interface IncidentDetail extends Incident {
  diagnosis: Diagnosis | null;
  resolution_note: string | null;
  evidence: Evidence[];
  timeline: TimelineEntry[];
  proposals: Proposal[];
  recoveries: Recovery[];
}

export interface ImpactNode {
  node_key: string;
  depth: number;
  path: string[];
  confidence: string;
  last_refreshed_at: string | null;
}

export interface Impact {
  origin: string;
  status: "computed" | "unknown";
  granularity?: string;
  impacted: ImpactNode[];
  note: string;
}

export interface LineageGraph {
  nodes: {
    node_key: string;
    node_type: string;
    display_name: string;
    metadata: Record<string, unknown>;
    last_refreshed_at: string | null;
  }[];
  edges: { upstream: string; downstream: string }[];
}

export interface Simulation {
  id: string;
  scenario: string;
  seed: number;
  record_count: number;
  corruption_pct: number;
  run_label: string | null;
  batch_id: string | null;
  labels: Record<string, unknown>;
  evaluation: {
    expected?: Record<string, number | null>;
    actual?: Record<string, number>;
    counts_match?: boolean;
    quarantine_true_positives?: number;
    quarantine_false_positives?: number;
    quarantine_false_negatives?: number;
    incident_opened?: boolean;
    incident_id?: string | null;
    incident_ids?: string[];
    detection_latency_seconds?: number | null;
    missing_sequence_detected?: boolean;
  } | null;
  created_at: string;
}

export interface Overview {
  generated_at: string;
  batches: Record<string, number>;
  records: {
    received: number;
    accepted: number;
    quarantined: number;
    skipped: number;
    open_quarantine: number;
    quarantine_rate: number | null;
  };
  processing_success_rate: number | null;
  incidents: Record<string, number | null>;
  mean_time_to_detect_seconds: number | null;
  mttd_samples: number;
  recoveries: {
    succeeded: number;
    failed: number;
    active: number;
    mean_duration_seconds: number | null;
    success_rate: number | null;
    recent: {
      id: string;
      state: string;
      attempts: number;
      action: string;
      incident_title: string;
      incident_id: string;
      created_at: string;
      completed_at: string | null;
    }[];
  };
  sources: {
    id: string;
    name: string;
    enabled: boolean;
    last_success_at: string | null;
    last_status: string | null;
    open_incidents: number;
    freshness: "fresh" | "stale" | "no_history";
    freshness_max_age_minutes: number;
  }[];
  simulations: Record<string, number>;
}

export interface TimeseriesPoint {
  bucket: string;
  received: number;
  accepted: number;
  quarantined: number;
  skipped: number;
  batches: number;
  incidents_opened: number;
  pass_rate: number | null;
}

export interface AuditEvent {
  id: string;
  actor_id: string;
  actor_role: string | null;
  action: string;
  entity_type: string;
  entity_id: string;
  before_hash: string | null;
  after_hash: string | null;
  details: Record<string, unknown>;
  correlation_id: string | null;
  occurred_at: string;
}

export interface SystemInfo {
  environment: string;
  task_mode: string;
  llm: { enabled: boolean; active: boolean; provider: string | null; model: string | null };
  chaos_enabled: boolean;
  integrations: Record<string, string>;
}
