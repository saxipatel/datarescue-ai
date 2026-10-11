import { get, patch, post, request } from "@/api/client";
import type {
  AuditEvent,
  Batch,
  BatchDetail,
  DryRun,
  Impact,
  Incident,
  IncidentDetail,
  LineageGraph,
  Overview,
  Page,
  Proposal,
  QualityResult,
  QuarantineDetail,
  QuarantineRecord,
  Recovery,
  RecoveryDetail,
  RepairAction,
  Rule,
  Simulation,
  Source,
  SystemInfo,
  TimeseriesPoint,
  TokenResponse,
  User,
} from "@/types/api";

export const api = {
  login: (email: string, password: string) =>
    post<TokenResponse>("/auth/login", { email, password }),
  me: () => get<User>("/auth/me"),
  systemInfo: () => get<SystemInfo>("/system/info"),
  ready: () => get<{ status: string; checks: Record<string, string> }>("/ready"),

  sources: () => get<Source[]>("/sources"),

  overview: () => get<Overview>("/metrics/overview"),
  timeseries: (bucket: "hour" | "day", hours: number) =>
    get<{ points: TimeseriesPoint[] }>("/metrics/timeseries", { bucket, hours }),

  revenue: () =>
    get<{
      available: boolean;
      reason?: string;
      rows: {
        revenue_date: string;
        currency: string;
        settled_amount: string;
        settled_count: number;
        refunded_amount: string;
        _refreshed_at: string;
      }[];
    }>("/metrics/revenue"),

  batches: (q: { limit: number; offset: number; source?: string; status?: string }) =>
    get<Page<Batch>>("/batches", q),
  batch: (id: string) => get<BatchDetail>(`/batches/${id}`),
  resumeBatch: (id: string) => post<{ batch_id: string }>(`/batches/${id}/resume`),
  generateDataset: (body: {
    source: string;
    scenario: string;
    records: number;
    seed: number;
    corruption_pct: number;
  }) => post<{ id: string; expected: Record<string, number> }>("/datasets/generate", body),
  ingestDataset: (body: { source: string; dataset_id: string; sequence_number?: number }) =>
    post<{ batch_id: string; duplicate_request: boolean }>("/ingestions", body),
  uploadFile: (source: string, file: File) => {
    const form = new FormData();
    form.append("source", source);
    form.append("file", file);
    return request<{ batch_id: string; duplicate_request: boolean }>("POST", "/ingestions/upload", {
      form,
    });
  },

  rules: () => get<Rule[]>("/quality/rules"),
  updateRule: (
    id: string,
    body: {
      severity?: string;
      enabled?: boolean;
      configuration?: Record<string, unknown>;
      expected_version: number;
      reason: string;
    },
  ) => patch<Rule>(`/quality/rules/${id}`, body),
  qualityResults: (q: { limit: number; offset: number; rule_key?: string; passed?: boolean }) =>
    get<Page<QualityResult>>("/quality/results", q),

  incidents: (q: {
    limit: number;
    offset: number;
    state?: string[];
    severity?: string[];
    open_only?: boolean;
    q?: string;
    batch_id?: string;
  }) => get<Page<Incident>>("/incidents", q),
  incident: (id: string) => get<IncidentDetail>(`/incidents/${id}`),
  impact: (id: string) => get<Impact>(`/incidents/${id}/impact`),
  acknowledge: (id: string, note: string, expected_version: number) =>
    post<Incident>(`/incidents/${id}/acknowledge`, { note, expected_version }),
  closeFalsePositive: (id: string, note: string, expected_version: number) =>
    post<Incident>(`/incidents/${id}/close`, { note, expected_version }),
  diagnose: (id: string) => post<{ status_url: string }>(`/incidents/${id}/diagnose`),

  lineage: () => get<LineageGraph>("/lineage"),

  quarantine: (q: {
    limit: number;
    offset: number;
    batch_id?: string;
    state?: string;
    code?: string[];
  }) => get<Page<QuarantineRecord>>("/quarantine", q),
  quarantineRecord: (id: string) => get<QuarantineDetail>(`/quarantine/${id}`),

  proposals: (q: { limit: number; offset: number; status?: string; incident_id?: string }) =>
    get<Page<Proposal>>("/repairs/proposals", q),
  proposal: (id: string) => get<Proposal>(`/repairs/proposals/${id}`),
  createProposal: (body: {
    incident_id: string;
    action: RepairAction;
    justification: string;
    target?: Record<string, unknown>;
  }) => post<Proposal>("/repairs/proposals", body),
  dryRun: (proposal_id: string) =>
    post<DryRun & { proposal_id: string; risk_level: string }>("/recoveries/dry-run", {
      proposal_id,
    }),
  approve: (id: string, note: string | null, expected_version: number) =>
    post<Proposal>(`/repairs/proposals/${id}/approve`, { note, expected_version }),
  reject: (id: string, note: string, expected_version: number) =>
    post<Proposal>(`/repairs/proposals/${id}/reject`, { note, expected_version }),

  recoveries: (q: { limit: number; offset: number; state?: string }) =>
    get<Page<Recovery>>("/recoveries", q),
  recovery: (id: string) => get<RecoveryDetail>(`/recoveries/${id}`),
  startRecovery: (body: {
    proposal_id: string;
    idempotency_key: string;
    chaos_crash_after?: number;
  }) =>
    post<{ job: Recovery; outcome: string; queue_job_id: string | null }>("/recoveries", body),

  scenarios: () => get<{ scenarios: string[] }>("/simulations/scenarios"),
  simulations: (q: { limit: number; offset: number }) => get<Page<Simulation>>("/simulations", q),
  simulation: (id: string) => get<Simulation>(`/simulations/${id}`),
  simulate: (body: {
    scenario: string;
    source: string;
    records: number;
    corruption_pct: number;
    seed?: number;
    run_label?: string;
  }) =>
    post<{ simulation: Simulation; freshness_results: Record<string, unknown>[] }>(
      "/simulations",
      body,
    ),

  audit: (q: {
    limit: number;
    offset: number;
    action?: string;
    entity_type?: string;
    entity_id?: string;
  }) => get<Page<AuditEvent>>("/audit", q),
};
