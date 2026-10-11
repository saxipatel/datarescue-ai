import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "@/api/endpoints";
import { ProposalDialog } from "@/components/ProposalDialog";
import { Timeline } from "@/components/Timeline";
import {
  Badge,
  Button,
  Card,
  Dialog,
  ErrorState,
  Field,
  Json,
  Notice,
  PageHeader,
  SeverityBadge,
  Skeleton,
  StatusBadge,
  Table,
  Td,
  TextArea,
  Th,
} from "@/components/ui";
import type { Tone } from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";
import { label, shortId, utc } from "@/lib/format";
import type { AIEnrichment, Evidence, IncidentDetail, RepairAction } from "@/types/api";

const CONFIDENCE: Record<string, Tone> = { high: "red", medium: "amber", low: "gray" };
const TRIAGE_STATES = ["OPEN", "INVESTIGATING", "ACTION_REQUIRED"];

export function IncidentDetailPage() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const [proposal, setProposal] = useState<{ action?: RepairAction } | null>(null);
  const [transition, setTransition] = useState<"ack" | "close" | null>(null);

  const incident = useQuery({
    queryKey: ["incident", id],
    queryFn: () => api.incident(id),
    refetchInterval: (q) =>
      q.state.data && ["OPEN", "INVESTIGATING", "RECOVERING"].includes(q.state.data.state) ? 2_000 : 10_000,
  });
  const impact = useQuery({ queryKey: ["impact", id], queryFn: () => api.impact(id) });
  const diagnose = useMutation({
    mutationFn: () => api.diagnose(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["incident", id] }),
  });

  if (incident.isError) return <ErrorState error={incident.error} />;
  if (!incident.data) return <Skeleton rows={12} />;
  const inc = incident.data;
  const terminal = inc.state === "RESOLVED" || inc.state === "CLOSED_AS_FALSE_POSITIVE";

  return (
    <div className="space-y-6">
      <PageHeader
        title={inc.title}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={inc.severity} />
            <StatusBadge status={inc.state} />
            <span>detected {utc(inc.detected_at)}</span>
            {inc.batch_id && (
              <Link to={`/batches/${inc.batch_id}`} className="text-teal-700 hover:underline dark:text-teal-400">
                batch {shortId(inc.batch_id)}
              </Link>
            )}
          </span>
        }
        actions={
          can("triage") &&
          !terminal && (
            <>
              {["OPEN", "INVESTIGATING", "ACTION_REQUIRED", "ACKNOWLEDGED"].includes(inc.state) && (
                <Button size="sm" busy={diagnose.isPending} onClick={() => diagnose.mutate()}>
                  Re-run diagnosis
                </Button>
              )}
              {TRIAGE_STATES.includes(inc.state) && (
                <Button size="sm" onClick={() => setTransition("ack")}>
                  Acknowledge
                </Button>
              )}
              {[...TRIAGE_STATES, "ACKNOWLEDGED"].includes(inc.state) && (
                <Button size="sm" variant="ghost" onClick={() => setTransition("close")}>
                  Close as false positive
                </Button>
              )}
            </>
          )
        }
      />
      {diagnose.isError && <ErrorState error={diagnose.error} />}
      {inc.resolution_note && (
        <Notice tone={inc.state === "RESOLVED" ? "green" : "gray"}>
          {inc.state === "RESOLVED" ? "Resolved" : "Closed"}: {inc.resolution_note}
        </Notice>
      )}

      <Card title="Summary">
        <p className="text-sm">{inc.summary}</p>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {inc.failed_rules.map((r) => (
            <Badge key={r} tone="red">
              {r}
            </Badge>
          ))}
        </div>
      </Card>

      <div className="grid gap-6 xl:grid-cols-5">
        <div className="min-w-0 space-y-6 xl:col-span-3">
          <DiagnosisCard
            incident={inc}
            canPropose={can("propose_repair") && !terminal}
            onPropose={(action) => setProposal({ action: action as RepairAction })}
          />
          <EvidenceCard evidence={inc.evidence} />
        </div>
        <div className="min-w-0 space-y-6 xl:col-span-2">
          <Card title="Downstream impact">
            {impact.isError ? (
              <ErrorState error={impact.error} />
            ) : !impact.data ? (
              <Skeleton />
            ) : impact.data.status === "unknown" ? (
              <Notice tone="amber">{impact.data.note}</Notice>
            ) : (
              <>
                <Link
                  to={`/lineage?incident=${inc.id}`}
                  className="mb-2 inline-block text-sm text-teal-700 hover:underline dark:text-teal-400"
                >
                  View on the lineage graph
                </Link>
                <Table>
                  <thead>
                    <tr>
                      <Th>Dataset / KPI</Th>
                      <Th>Depth</Th>
                      <Th>Confidence</Th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {impact.data.impacted.map((n) => (
                      <tr key={n.node_key}>
                        <Td className="whitespace-normal">
                          <div className="font-mono text-xs">{n.node_key}</div>
                          <div className="text-xs text-slate-500">{n.path.join(" → ")}</div>
                        </Td>
                        <Td>{n.depth}</Td>
                        <Td>{n.confidence}</Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
                <p className="mt-2 text-xs text-slate-500">{impact.data.note}</p>
              </>
            )}
          </Card>

          <Card
            title="Repairs"
            actions={
              can("propose_repair") &&
              !terminal && (
                <Button size="sm" variant="primary" onClick={() => setProposal({})}>
                  Propose repair
                </Button>
              )
            }
          >
            {inc.proposals.length === 0 ? (
              <p className="text-sm text-slate-500">No proposals yet.</p>
            ) : (
              <ul className="space-y-2 text-sm">
                {inc.proposals.map((p) => {
                  const job = inc.recoveries.find((r) => r.proposal_id === p.id);
                  return (
                    <li key={p.id} className="flex flex-wrap items-center gap-2">
                      <Link to={`/recovery?proposal=${p.id}`} className="font-medium text-teal-700 hover:underline dark:text-teal-400">
                        {label(p.action)}
                      </Link>
                      <StatusBadge status={p.status} />
                      <span className="text-xs text-slate-500">{p.target_count} rows · {p.risk_level} risk</span>
                      {job && <StatusBadge status={job.state} />}
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>

          <Card title="Timeline">
            <Timeline entries={inc.timeline} />
          </Card>
        </div>
      </div>

      {proposal && (
        <ProposalDialog open incidentId={inc.id} initialAction={proposal.action} onClose={() => setProposal(null)} />
      )}
      <TransitionDialog
        mode={transition}
        incident={inc}
        onClose={() => setTransition(null)}
        onDone={() => queryClient.invalidateQueries({ queryKey: ["incident", id] })}
      />
    </div>
  );
}

function DiagnosisCard({
  incident,
  canPropose,
  onPropose,
}: {
  incident: IncidentDetail;
  canPropose: boolean;
  onPropose: (action: string) => void;
}) {
  const d = incident.diagnosis;
  if (!d)
    return (
      <Card title="Diagnosis">
        <p className="text-sm text-slate-500">
          {incident.state === "OPEN" || incident.state === "INVESTIGATING" ? "Diagnosis is running…" : "No diagnosis recorded."}
        </p>
      </Card>
    );
  return (
    <Card title={`Diagnosis · ${d.provider} rules`} actions={<span className="text-xs text-slate-500">{utc(d.generated_at)}</span>}>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Suspected causes (ranked)</h3>
      {d.suspected_causes.length === 0 ? (
        <p className="text-sm text-slate-500">No known failure signature matched.</p>
      ) : (
        <ol className="space-y-3">
          {d.suspected_causes.map((c, i) => (
            <li key={c.cause_key} className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-semibold text-slate-500">#{i + 1}</span>
                <span className="font-medium">{c.cause}</span>
                <Badge tone={CONFIDENCE[c.confidence_label]}>{c.confidence_label} confidence</Badge>
              </div>
              <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{c.explanation}</p>
              <p className="mt-1 text-xs text-slate-500">
                Supporting evidence:{" "}
                {c.supporting_evidence_ids.map((e) => (
                  <a key={e} href={`#evidence-${e}`} className="mr-1 font-mono underline">
                    {shortId(e)}
                  </a>
                ))}
                {c.contradicting_evidence_ids.length > 0 && (
                  <>
                    {" "}· contradicting:{" "}
                    {c.contradicting_evidence_ids.map((e) => (
                      <a key={e} href={`#evidence-${e}`} className="mr-1 font-mono underline">
                        {shortId(e)}
                      </a>
                    ))}
                  </>
                )}
              </p>
            </li>
          ))}
        </ol>
      )}

      <h3 className="mb-2 mt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">Recommended actions</h3>
      <ul className="space-y-2">
        {d.recommended_actions.map((a) => (
          <li key={a.action} className="flex flex-wrap items-start justify-between gap-2 rounded-md bg-slate-50 p-3 dark:bg-slate-950">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs font-semibold">{a.action}</span>
                <Badge>{a.risk} risk</Badge>
              </div>
              <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{a.description}</p>
            </div>
            {a.executable && canPropose && (
              <Button size="sm" onClick={() => onPropose(a.action)}>
                Propose
              </Button>
            )}
          </li>
        ))}
      </ul>

      <AIPanel ai={d.ai_enrichment} />

      <details className="mt-4 text-xs text-slate-500">
        <summary className="cursor-pointer">Limitations</summary>
        <ul className="mt-1 list-disc pl-5">
          {d.limitations.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      </details>
    </Card>
  );
}

function AIPanel({ ai }: { ai: AIEnrichment | null }) {
  if (!ai || ai.status === "disabled")
    return (
      <p className="mt-4 text-xs text-slate-500">
        LLM enrichment is off. The ranking above comes from deterministic rules only.
      </p>
    );
  if (ai.status !== "ok")
    return (
      <Notice tone="amber">
        LLM enrichment {label(ai.status)}
        {ai.error ? `: ${ai.error}` : ""}. Showing the deterministic diagnosis only.
      </Notice>
    );
  return (
    <div className="mt-4 rounded-md border border-violet-300 bg-violet-50 p-3 text-sm dark:border-violet-800 dark:bg-violet-950">
      <div className="mb-1 flex items-center gap-2">
        <Badge tone="violet">AI suggestion · unverified</Badge>
        <span className="text-xs text-slate-500">{ai.model}</span>
      </div>
      <p>{ai.summary}</p>
      {ai.considerations.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-xs">
          {ai.considerations.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-xs text-slate-500">
        Cites evidence {ai.cited_evidence_ids.map(shortId).join(", ") || "none"}. Suggested actions still need a proposal,
        dry run and approval.
      </p>
    </div>
  );
}

function EvidenceCard({ evidence }: { evidence: Evidence[] }) {
  return (
    <Card title={`Evidence (${evidence.length})`}>
      <ul className="space-y-2">
        {evidence.map((e) => (
          <li key={e.id} id={`evidence-${e.id}`} className="scroll-mt-20">
            <details className="rounded-md border border-slate-200 dark:border-slate-800">
              <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-3 py-2 text-sm">
                <span className="font-medium">{label(e.kind)}</span>
                {e.kind === "rule_failure" && <Badge tone="red">{String(e.payload.rule_key)}</Badge>}
                <span className="font-mono text-xs text-slate-500">{shortId(e.id)}</span>
              </summary>
              <div className="px-3 pb-3">
                <Json value={e.payload} />
              </div>
            </details>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function TransitionDialog({
  mode,
  incident,
  onClose,
  onDone,
}: {
  mode: "ack" | "close" | null;
  incident: IncidentDetail;
  onClose: () => void;
  onDone: () => void;
}) {
  const [note, setNote] = useState("");
  const mutation = useMutation({
    mutationFn: () =>
      mode === "ack"
        ? api.acknowledge(incident.id, note, incident.version)
        : api.closeFalsePositive(incident.id, note, incident.version),
    onSuccess: () => {
      setNote("");
      onDone();
      onClose();
    },
  });
  return (
    <Dialog open={mode !== null} title={mode === "ack" ? "Acknowledge incident" : "Close as false positive"} onClose={onClose}>
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          mutation.mutate();
        }}
      >
        <Field label="Justification" hint="Required; stored in the audit trail.">
          {(id) => <TextArea id={id} rows={3} required minLength={3} value={note} onChange={(e) => setNote(e.target.value)} />}
        </Field>
        {mutation.isError && <ErrorState error={mutation.error} />}
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant={mode === "close" ? "danger" : "primary"} busy={mutation.isPending}>
            Confirm
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
