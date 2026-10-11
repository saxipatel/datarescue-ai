import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "@/api/endpoints";
import { Timeline } from "@/components/Timeline";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  Input,
  KeyValue,
  Notice,
  PageHeader,
  Select,
  SeverityBadge,
  Skeleton,
  StatusBadge,
  Table,
  Td,
  TextArea,
  Th,
} from "@/components/ui";
import { cx } from "@/lib/cx";
import { useAuth } from "@/hooks/useAuth";
import { label, newIdempotencyKey, num, shortId, utc } from "@/lib/format";
import type { Proposal } from "@/types/api";

export function RecoveryPage() {
  const [params, setParams] = useSearchParams();
  const selectedProposal = params.get("proposal");
  const selectedJob = params.get("job");
  const [status, setStatus] = useState("");

  const proposals = useQuery({
    queryKey: ["proposals", status],
    queryFn: () => api.proposals({ limit: 50, offset: 0, status: status || undefined }),
    refetchInterval: 5_000,
  });
  const jobs = useQuery({
    queryKey: ["recoveries"],
    queryFn: () => api.recoveries({ limit: 20, offset: 0 }),
    refetchInterval: 3_000,
  });

  function select(key: "proposal" | "job", id: string) {
    setParams(new URLSearchParams({ [key]: id }));
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Recovery Center"
        subtitle="Proposals need a dry run and a steward's approval. Execution is idempotent per key and verified before an incident resolves."
      />
      <div className="grid gap-6 xl:grid-cols-5">
        <Card
          title="Repair proposals"
          className="xl:col-span-2"
          padded={false}
          actions={
            <Select aria-label="Proposal status" value={status} onChange={(e) => setStatus(e.target.value)} className="w-36 py-1 text-xs">
              <option value="">All</option>
              <option value="PENDING">Pending</option>
              <option value="APPROVED">Approved</option>
              <option value="EXECUTED">Executed</option>
              <option value="REJECTED">Rejected</option>
            </Select>
          }
        >
          {proposals.isError ? (
            <div className="p-4">
              <ErrorState error={proposals.error} />
            </div>
          ) : !proposals.data ? (
            <div className="p-4">
              <Skeleton rows={5} />
            </div>
          ) : proposals.data.items.length === 0 ? (
            <div className="p-4">
              <EmptyState title="No proposals">Create one from an incident's recommended actions.</EmptyState>
            </div>
          ) : (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {proposals.data.items.map((p) => (
                <li key={p.id}>
                  <button
                    onClick={() => select("proposal", p.id)}
                    aria-current={selectedProposal === p.id}
                    className={cx(
                      "flex w-full flex-wrap items-center justify-between gap-2 px-4 py-3 text-left hover:bg-slate-50 dark:hover:bg-slate-800/50",
                      selectedProposal === p.id && "bg-teal-50 dark:bg-teal-950/50",
                    )}
                  >
                    <span className="min-w-0">
                      <span className="block text-sm font-medium">{label(p.action)}</span>
                      <span className="text-xs text-slate-500">
                        {num(p.target_count)} rows · {utc(p.created_at)}
                      </span>
                    </span>
                    <span className="flex gap-1.5">
                      <SeverityBadge severity={p.risk_level} />
                      <StatusBadge status={p.status} />
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div className="min-w-0 xl:col-span-3">
          {selectedProposal ? (
            <ProposalPanel id={selectedProposal} onJob={(id) => select("job", id)} />
          ) : selectedJob ? (
            <JobPanel id={selectedJob} />
          ) : (
            <Card>
              <EmptyState title="Select a proposal or a recovery job" />
            </Card>
          )}
        </div>
      </div>

      <Card title="Recovery jobs" padded={false}>
        {!jobs.data ? (
          <div className="p-4">
            <Skeleton />
          </div>
        ) : jobs.data.items.length === 0 ? (
          <div className="p-4">
            <EmptyState title="No recovery jobs yet" />
          </div>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Job</Th>
                <Th>State</Th>
                <Th>Attempts</Th>
                <Th>Idempotency key</Th>
                <Th>Verification</Th>
                <Th>Created (UTC)</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {jobs.data.items.map((j) => (
                <tr key={j.id} className={cx(selectedJob === j.id && "bg-teal-50 dark:bg-teal-950/50")}>
                  <Td>
                    <button className="font-mono text-xs text-teal-700 hover:underline dark:text-teal-400" onClick={() => select("job", j.id)}>
                      {shortId(j.id)}
                    </button>
                  </Td>
                  <Td>
                    <StatusBadge status={j.state} />
                  </Td>
                  <Td>{j.attempts}</Td>
                  <Td className="font-mono text-xs">{j.idempotency_key}</Td>
                  <Td>{j.verification ? <Badge tone={j.verification.passed ? "green" : "red"}>{j.verification.passed ? "passed" : "failed"}</Badge> : "—"}</Td>
                  <Td className="text-xs">{utc(j.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}

function PreviewTable({ proposal }: { proposal: Proposal }) {
  const patch = proposal.proposed_patch;
  if (patch.deliveries)
    return (
      <Table>
        <thead>
          <tr>
            <Th>Sequence</Th>
            <Th>Records</Th>
            <Th>Seed</Th>
            <Th>Window ends (UTC)</Th>
          </tr>
        </thead>
        <tbody>
          {patch.deliveries.map((d) => (
            <tr key={String(d.sequence_number)}>
              <Td>{String(d.sequence_number)}</Td>
              <Td>{String(d.records)}</Td>
              <Td>{String(d.seed)}</Td>
              <Td className="text-xs">{utc(String(d.anchor))}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
    );
  const rows = patch.rows ?? [];
  if (!rows.length) return <p className="text-sm text-slate-500">No preview rows.</p>;
  const columns = Object.keys(rows[0]).filter((c) => !c.endsWith("_id") || c === "event_id");
  return (
    <>
      <Table>
        <thead>
          <tr>
            {columns.map((c) => (
              <Th key={c}>{label(c)}</Th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <Td key={c} className={cx("text-xs", c === "event_id" && "font-mono")}>
                  {c === "before" ? (
                    <span className="rounded bg-red-100 px-1 line-through dark:bg-red-900">{String(row[c])}</span>
                  ) : c === "after" ? (
                    <span className="rounded bg-emerald-100 px-1 dark:bg-emerald-900">{String(row[c])}</span>
                  ) : Array.isArray(row[c]) ? (
                    (row[c] as string[]).join(", ")
                  ) : (
                    String(row[c] ?? "—").slice(0, 36)
                  )}
                </Td>
              ))}
            </tr>
          ))}
        </tbody>
      </Table>
      {proposal.target_count > rows.length && (
        <p className="mt-1 text-xs text-slate-500">
          Showing {rows.length} of {num(proposal.target_count)} targeted rows.
        </p>
      )}
    </>
  );
}

function ProposalPanel({ id, onJob }: { id: string; onJob: (id: string) => void }) {
  const { user, can } = useAuth();
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const [key, setKey] = useState(() => newIdempotencyKey("recovery"));
  const [crashAfter, setCrashAfter] = useState("");
  const proposal = useQuery({ queryKey: ["proposal", id], queryFn: () => api.proposal(id) });
  const info = useQuery({ queryKey: ["system-info"], queryFn: api.systemInfo });
  const jobs = useQuery({ queryKey: ["recoveries"], queryFn: () => api.recoveries({ limit: 20, offset: 0 }) });
  const existingJob = jobs.data?.items.find((j) => j.proposal_id === id);

  // Reuse the key of an existing job for this proposal so a resubmission is idempotent.
  useEffect(() => {
    setKey(existingJob?.idempotency_key ?? newIdempotencyKey("recovery"));
  }, [id, existingJob?.idempotency_key]);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["proposal", id] });
    queryClient.invalidateQueries({ queryKey: ["proposals"] });
  };
  const dryRun = useMutation({ mutationFn: () => api.dryRun(id), onSuccess: refresh });
  const approve = useMutation({
    mutationFn: (p: Proposal) => api.approve(id, note || null, p.version),
    onSuccess: refresh,
  });
  const reject = useMutation({ mutationFn: (p: Proposal) => api.reject(id, note, p.version), onSuccess: refresh });
  const execute = useMutation({
    mutationFn: () =>
      api.startRecovery({ proposal_id: id, idempotency_key: key, chaos_crash_after: crashAfter ? Number(crashAfter) : undefined }),
    onSuccess: (res) => {
      refresh();
      queryClient.invalidateQueries({ queryKey: ["recoveries"] });
      onJob(res.job.id);
    },
  });

  if (proposal.isError) return <ErrorState error={proposal.error} />;
  if (!proposal.data) return <Card><Skeleton rows={8} /></Card>;
  const p = proposal.data;
  const selfReviewBlocked = (p.risk_level === "high" || p.risk_level === "critical") && p.created_by === user?.id;

  return (
    <Card
      title={`${label(p.action)} · ${shortId(p.id)}`}
      actions={
        <Link to={`/incidents/${p.incident_id}`} className="text-sm text-teal-700 hover:underline dark:text-teal-400">
          Incident
        </Link>
      }
    >
      <div className="space-y-5">
        <KeyValue
          items={[
            ["Status", <StatusBadge key="s" status={p.status} />],
            ["Risk", <SeverityBadge key="r" severity={p.risk_level} />],
            ["Target rows", num(p.target_count)],
            ["Created", utc(p.created_at)],
            ["Reviewed", utc(p.reviewed_at)],
            ["Review note", p.review_note ?? "—"],
          ]}
        />
        <div>
          <h3 className="mb-1 text-xs font-semibold uppercase text-slate-500">Justification</h3>
          <p className="text-sm">{p.justification}</p>
        </div>
        <div>
          <h3 className="mb-1 text-xs font-semibold uppercase text-slate-500">Preview ({p.proposed_patch.kind ?? "targets"})</h3>
          <PreviewTable proposal={p} />
        </div>

        <section aria-labelledby="dry-run">
          <div className="mb-2 flex items-center justify-between">
            <h3 id="dry-run" className="text-xs font-semibold uppercase text-slate-500">
              Dry run {p.dry_run_at && `· ${utc(p.dry_run_at)}`}
            </h3>
            {can("propose_repair") && ["PENDING", "APPROVED"].includes(p.status) && (
              <Button size="sm" busy={dryRun.isPending} onClick={() => dryRun.mutate()}>
                {p.dry_run_summary ? "Re-run dry run" : "Run dry run"}
              </Button>
            )}
          </div>
          {dryRun.isError && <ErrorState error={dryRun.error} />}
          {p.dry_run_summary ? (
            <>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
                {Object.entries(p.dry_run_summary.counts).map(([k, v]) => (
                  <div key={k} className="rounded-md bg-slate-50 p-2 text-center dark:bg-slate-950">
                    <div className="text-lg font-semibold tabular-nums">{num(v)}</div>
                    <div className="text-xs text-slate-500">{label(k)}</div>
                  </div>
                ))}
              </div>
              {p.dry_run_summary.notes.map((n) => (
                <p key={n} className="mt-1 text-xs text-slate-500">
                  {n}
                </p>
              ))}
            </>
          ) : (
            <p className="text-sm text-slate-500">Not run yet. Approval requires a dry run.</p>
          )}
        </section>

        {p.status === "PENDING" && can("approve_repair") && (
          <section className="space-y-2 rounded-md border border-slate-200 p-3 dark:border-slate-800">
            <h3 className="text-xs font-semibold uppercase text-slate-500">Review</h3>
            {selfReviewBlocked && <Notice tone="amber">High-risk change: a reviewer other than the proposer must approve.</Notice>}
            <Field label="Review note (required to reject)">
              {(fid) => <TextArea id={fid} rows={2} value={note} onChange={(e) => setNote(e.target.value)} />}
            </Field>
            {(approve.isError || reject.isError) && <ErrorState error={approve.error ?? reject.error} />}
            <div className="flex gap-2">
              <Button variant="primary" busy={approve.isPending} disabled={!p.dry_run_at || selfReviewBlocked} onClick={() => approve.mutate(p)}>
                Approve
              </Button>
              <Button variant="danger" busy={reject.isPending} disabled={!note.trim()} onClick={() => reject.mutate(p)}>
                Reject
              </Button>
            </div>
          </section>
        )}
        {p.status === "PENDING" && !can("approve_repair") && (
          <Notice>Waiting for a steward or admin to review. Sign in as steward@example.test to approve.</Notice>
        )}

        {["APPROVED", "EXECUTED"].includes(p.status) && can("execute_recovery") && (
          <section className="space-y-2 rounded-md border border-slate-200 p-3 dark:border-slate-800">
            <h3 className="text-xs font-semibold uppercase text-slate-500">Execute</h3>
            <Field label="Idempotency key" hint="Re-submitting the same key returns the existing job or resumes a crashed one; it never applies the change twice.">
              {(fid) => <Input id={fid} value={key} onChange={(e) => setKey(e.target.value)} className="font-mono" />}
            </Field>
            {info.data?.chaos_enabled && p.status === "APPROVED" && (
              <Field label="Failure injection: crash worker after N records (optional)" hint="Demo only (CHAOS_ENABLED). Retry with the same key afterwards.">
                {(fid) => <Input id={fid} type="number" min={1} value={crashAfter} onChange={(e) => setCrashAfter(e.target.value)} />}
              </Field>
            )}
            {execute.isError && <ErrorState error={execute.error} />}
            {execute.data && <Notice tone="blue">Outcome: {execute.data.outcome}</Notice>}
            <Button variant="primary" busy={execute.isPending} onClick={() => execute.mutate()}>
              {existingJob && key === existingJob.idempotency_key ? "Submit again with the same key" : "Execute recovery"}
            </Button>
          </section>
        )}
      </div>
    </Card>
  );
}

function JobPanel({ id }: { id: string }) {
  const queryClient = useQueryClient();
  const job = useQuery({
    queryKey: ["recovery", id],
    queryFn: () => api.recovery(id),
    refetchInterval: (q) => (q.state.data && ["SUCCEEDED", "FAILED"].includes(q.state.data.state) ? false : 1_500),
  });
  const state = job.data?.state;
  useEffect(() => {
    // Refresh the lists as soon as the job settles instead of waiting for their poll.
    queryClient.invalidateQueries({ queryKey: ["recoveries"] });
    queryClient.invalidateQueries({ queryKey: ["proposals"] });
  }, [state, queryClient]);
  if (job.isError) return <ErrorState error={job.error} />;
  if (!job.data) return <Card><Skeleton rows={8} /></Card>;
  const j = job.data;
  const progress = j.metrics.progress;
  return (
    <Card
      title={`Recovery job ${shortId(j.id)}`}
      actions={
        <span className="flex items-center gap-2">
          <StatusBadge status={j.state} />
          {j.proposal_id && (
            <Link to={`/recovery?proposal=${j.proposal_id}`} className="text-sm text-teal-700 hover:underline dark:text-teal-400">
              Proposal
            </Link>
          )}
        </span>
      }
    >
      <div className="space-y-5">
        <KeyValue
          items={[
            ["Idempotency key", <code key="k" className="font-mono text-xs">{j.idempotency_key}</code>],
            ["Attempts", j.attempts],
            ["Started", utc(j.started_at)],
            ["Last heartbeat", utc(j.heartbeat_at)],
            ["Completed", utc(j.completed_at)],
            ["Correlation id", <code key="c" className="font-mono text-xs">{j.correlation_id}</code>],
          ]}
        />
        {j.error_message && <Notice tone="red">{j.error_message}</Notice>}
        {progress && j.state !== "SUCCEEDED" && (
          <Notice>Progress: {Object.entries(progress).map(([k, v]) => `${label(k)} ${v}`).join(" · ")}</Notice>
        )}
        {j.state === "RUNNING" && j.attempts > 0 && (
          <p className="text-xs text-slate-500">
            If the worker crashed, re-submit the same idempotency key from the proposal to resume.
          </p>
        )}
        {j.verification && (
          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase text-slate-500">
              Verification <Badge tone={j.verification.passed ? "green" : "red"}>{j.verification.passed ? "passed" : "failed"}</Badge>
            </h3>
            <ul className="space-y-1 text-sm">
              {j.verification.checks.map((c) => (
                <li key={c.name} className="flex gap-2">
                  <span aria-hidden className={c.passed ? "text-emerald-600" : "text-red-600"}>
                    {c.passed ? "✓" : "✗"}
                  </span>
                  <span>
                    <span className="font-mono text-xs">{c.name}</span> — {c.detail}
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-slate-500">
              Outstanding issues after recovery:{" "}
              {Object.entries(j.verification.outstanding).map(([k, v]) => `${k}=${v}`).join(", ") || "none"}
            </p>
            <p className="mt-1 break-all font-mono text-xs text-slate-500">
              checksum {j.verification.checksum_before?.slice(0, 16)}… → {j.verification.checksum_after?.slice(0, 16)}…
            </p>
          </section>
        )}
        {j.metrics.effects && (
          <section>
            <h3 className="mb-1 text-xs font-semibold uppercase text-slate-500">Effects (last attempt)</h3>
            <p className="text-sm">
              {Object.entries(j.metrics.effects)
                .filter(([, v]) => typeof v === "number")
                .map(([k, v]) => `${label(k)} ${v}`)
                .join(" · ")}
            </p>
          </section>
        )}
        <section>
          <h3 className="mb-2 text-xs font-semibold uppercase text-slate-500">Audit trail</h3>
          <Timeline entries={j.audit} />
        </section>
      </div>
    </Card>
  );
}
