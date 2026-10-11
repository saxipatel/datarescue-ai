import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "@/api/endpoints";
import {
  Badge,
  Button,
  Card,
  ErrorState,
  Json,
  KeyValue,
  Notice,
  PageHeader,
  SeverityBadge,
  Skeleton,
  StatusBadge,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { cx } from "@/lib/cx";
import { useAuth } from "@/hooks/useAuth";
import { num, shortId, utc } from "@/lib/format";

export function BatchDetailPage() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const batch = useQuery({
    queryKey: ["batch", id],
    queryFn: () => api.batch(id),
    refetchInterval: (q) => (q.state.data && ["PENDING", "PROCESSING"].includes(q.state.data.status) ? 2_000 : false),
  });
  const resume = useMutation({
    mutationFn: () => api.resumeBatch(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["batch", id] }),
  });

  if (batch.isError) return <ErrorState error={batch.error} />;
  if (!batch.data) return <Skeleton rows={10} />;
  const b = batch.data;
  const reconciles = b.accepted_count + b.rejected_count + b.skipped_count === b.processed_count;

  return (
    <div className="space-y-6">
      <PageHeader
        title={`Batch ${shortId(b.id)}`}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            {b.source_name} · seq {b.sequence_number ?? "—"} · <StatusBadge status={b.status} />
            {b.incident_id && (
              <Link to={`/incidents/${b.incident_id}`} className="text-red-700 hover:underline dark:text-red-400">
                View incident
              </Link>
            )}
          </span>
        }
        actions={
          <>
            <Link to={`/quarantine?batch_id=${b.id}`}>
              <Button size="sm">Quarantined rows ({num(b.rejected_count)})</Button>
            </Link>
            {can("ingest") && ["FAILED", "PENDING", "PROCESSING"].includes(b.status) && (
              <Button size="sm" variant="primary" busy={resume.isPending} onClick={() => resume.mutate()}>
                Resume from checkpoint
              </Button>
            )}
          </>
        }
      />
      {b.error_message && <Notice tone="red">{b.error_message}</Notice>}
      {resume.isError && <ErrorState error={resume.error} />}

      <Card title="Row accounting">
        <KeyValue
          items={[
            ["Expected (manifest)", num(b.expected_count)],
            ["Received", num(b.received_count)],
            ["Processed (checkpoint)", num(b.processed_count)],
            ["Accepted", num(b.accepted_count)],
            ["Quarantined", num(b.rejected_count)],
            ["Skipped (identical duplicates)", num(b.skipped_count)],
            ["Accepted with late warning", num(b.warning_count)],
            ["accepted + quarantined + skipped = processed", reconciles ? "yes" : "NO"],
            ["Started", utc(b.started_at)],
            ["Finished", utc(b.finished_at)],
            ["Format", b.input_format],
            ["Correlation id", <code key="c" className="font-mono text-xs">{b.correlation_id}</code>],
          ]}
        />
        {b.checksum && (
          <p className="mt-3 break-all font-mono text-xs text-slate-500">accepted checksum {b.checksum}</p>
        )}
      </Card>

      <Card title="Quality rules" padded={false}>
        <Table>
          <thead>
            <tr>
              <Th>Rule</Th>
              <Th>Result</Th>
              <Th>Severity</Th>
              <Th className="text-right">Failed</Th>
              <Th className="text-right">Checked</Th>
              <Th>Version</Th>
              <Th />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {b.quality_results.map((r) => (
              <Fragment key={r.id}>
                <tr className={cx(!r.passed && "bg-red-50/50 dark:bg-red-950/30")}>
                  <Td className="font-mono text-xs">{r.rule_key}</Td>
                  <Td>{r.passed ? <Badge tone="green">passed</Badge> : <Badge tone="red">failed</Badge>}</Td>
                  <Td>
                    <SeverityBadge severity={r.severity} />
                  </Td>
                  <Td className="text-right tabular-nums">{num(r.failed_count)}</Td>
                  <Td className="text-right tabular-nums">{num(r.checked_count)}</Td>
                  <Td>v{r.rule_version}</Td>
                  <Td className="text-right">
                    <Button size="sm" variant="ghost" aria-expanded={open === r.id} onClick={() => setOpen(open === r.id ? null : r.id)}>
                      Evidence
                    </Button>
                  </Td>
                </tr>
                {open === r.id && (
                  <tr>
                    <td colSpan={7} className="px-3 pb-3">
                      <Json value={r.evidence} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </Table>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Pipeline runs">
          {b.runs.length === 0 ? (
            <p className="text-sm text-slate-500">Ingested inline (seed or redelivery); no queued run.</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {b.runs.map((r) => (
                <li key={r.id} className="flex flex-wrap items-center gap-2">
                  <StatusBadge status={r.state} />
                  <span>{r.pipeline_name}</span>
                  <span className="text-slate-500">attempt {r.attempts}</span>
                  <span className="text-xs text-slate-500">{utc(r.started_at)}</span>
                  {r.error_message && <span className="text-xs text-red-700">{r.error_message}</span>}
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Run log">
          {b.run_events.length === 0 ? (
            <p className="text-sm text-slate-500">No run events recorded.</p>
          ) : (
            <ol className="space-y-1 break-all font-mono text-xs">
              {b.run_events.map((e, i) => (
                <li key={i}>
                  <span className="text-slate-500">{utc(e.created_at).slice(11, 19)}</span>{" "}
                  <span className={e.level === "ERROR" ? "text-red-700" : e.level === "WARN" ? "text-amber-700" : ""}>{e.level}</span>{" "}
                  {e.message} <span className="text-slate-500">{JSON.stringify(e.context)}</span>
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>
    </div>
  );
}
