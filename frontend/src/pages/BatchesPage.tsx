import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { api } from "@/api/endpoints";
import {
  Button,
  Card,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Notice,
  PageHeader,
  Pagination,
  Select,
  Skeleton,
  StatusBadge,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";
import { duration, num, shortId, utc } from "@/lib/format";

const LIMIT = 25;
const STATUSES = ["", "PENDING", "PROCESSING", "COMPLETE", "PARTIAL", "FAILED"];

export function BatchesPage() {
  const [params, setParams] = useSearchParams();
  const source = params.get("source") ?? "";
  const status = params.get("status") ?? "";
  const offset = Number(params.get("offset") ?? 0);
  const { can } = useAuth();
  const [ingestOpen, setIngestOpen] = useState(false);

  const sources = useQuery({ queryKey: ["sources"], queryFn: api.sources });
  const batches = useQuery({
    queryKey: ["batches", source, status, offset],
    queryFn: () => api.batches({ limit: LIMIT, offset, source: source || undefined, status: status || undefined }),
    refetchInterval: 5_000,
  });

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("offset");
    setParams(next);
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Pipelines & Batches"
        subtitle="Every ingestion batch with its row outcomes and quality rule results."
        actions={
          can("ingest") && (
            <Button variant="primary" onClick={() => setIngestOpen(true)}>
              Ingest batch
            </Button>
          )
        }
      />
      <Card padded={false}>
        <div className="flex flex-wrap gap-3 border-b border-slate-200 p-3 dark:border-slate-800">
          <Select aria-label="Source" value={source} onChange={(e) => setFilter("source", e.target.value)} className="w-48">
            <option value="">All sources</option>
            {sources.data?.map((s) => (
              <option key={s.id} value={s.name}>
                {s.name}
              </option>
            ))}
          </Select>
          <Select aria-label="Status" value={status} onChange={(e) => setFilter("status", e.target.value)} className="w-40">
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s ? s.toLowerCase() : "Any status"}
              </option>
            ))}
          </Select>
        </div>
        {batches.isError ? (
          <div className="p-4">
            <ErrorState error={batches.error} />
          </div>
        ) : !batches.data ? (
          <div className="p-4">
            <Skeleton rows={8} />
          </div>
        ) : batches.data.items.length === 0 ? (
          <div className="p-4">
            <EmptyState title="No batches match these filters" />
          </div>
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>Batch</Th>
                  <Th>Source</Th>
                  <Th className="text-right">Seq</Th>
                  <Th>Status</Th>
                  <Th className="text-right">Received</Th>
                  <Th className="text-right">Accepted</Th>
                  <Th className="text-right">Quarantined</Th>
                  <Th className="text-right">Skipped</Th>
                  <Th className="text-right">Failed rules</Th>
                  <Th>Finished (UTC)</Th>
                  <Th className="text-right">Duration</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {batches.data.items.map((b) => (
                  <tr key={b.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/50">
                    <Td>
                      <Link to={`/batches/${b.id}`} className="font-mono text-xs font-medium text-teal-700 hover:underline dark:text-teal-400">
                        {shortId(b.id)}
                      </Link>
                    </Td>
                    <Td>{b.source_name}</Td>
                    <Td className="text-right tabular-nums">{b.sequence_number ?? "—"}</Td>
                    <Td>
                      <StatusBadge status={b.status} />
                    </Td>
                    <Td className="text-right tabular-nums">{num(b.received_count)}</Td>
                    <Td className="text-right tabular-nums">{num(b.accepted_count)}</Td>
                    <Td className="text-right tabular-nums">{num(b.rejected_count)}</Td>
                    <Td className="text-right tabular-nums">{num(b.skipped_count)}</Td>
                    <Td className="text-right tabular-nums">
                      {b.incident_id ? (
                        <Link to={`/incidents/${b.incident_id}`} className="text-red-700 hover:underline dark:text-red-400">
                          {b.failed_rules}
                        </Link>
                      ) : (
                        b.failed_rules
                      )}
                    </Td>
                    <Td className="text-xs">{utc(b.finished_at)}</Td>
                    <Td className="text-right tabular-nums">
                      {b.started_at && b.finished_at
                        ? duration((new Date(b.finished_at).getTime() - new Date(b.started_at).getTime()) / 1000)
                        : "—"}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <Pagination
              total={batches.data.total}
              limit={LIMIT}
              offset={offset}
              onChange={(o) => {
                const next = new URLSearchParams(params);
                next.set("offset", String(o));
                setParams(next);
              }}
            />
          </>
        )}
      </Card>
      <IngestDialog open={ingestOpen} onClose={() => setIngestOpen(false)} sources={sources.data?.map((s) => s.name) ?? []} />
    </div>
  );
}

function IngestDialog({ open, onClose, sources }: { open: boolean; onClose: () => void; sources: string[] }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"generate" | "upload">("generate");
  const [source, setSource] = useState("payments_core");
  const [records, setRecords] = useState(1000);
  const [seed, setSeed] = useState(1);
  const [file, setFile] = useState<File | null>(null);

  const ingest = useMutation({
    mutationFn: async () => {
      if (mode === "upload") {
        if (!file) throw new Error("Choose a .csv or .jsonl file");
        return api.uploadFile(source, file);
      }
      const dataset = await api.generateDataset({ source, scenario: "clean", records, seed, corruption_pct: 0 });
      return api.ingestDataset({ source, dataset_id: dataset.id });
    },
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["batches"] });
      onClose();
      navigate(`/batches/${res.batch_id}`);
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    ingest.mutate();
  }

  return (
    <Dialog open={open} title="Ingest a batch" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <div className="flex gap-2" role="radiogroup" aria-label="Input">
          {(["generate", "upload"] as const).map((m) => (
            <Button key={m} type="button" size="sm" variant={mode === m ? "primary" : "secondary"} aria-pressed={mode === m} onClick={() => setMode(m)}>
              {m === "generate" ? "Generate clean synthetic batch" : "Upload file"}
            </Button>
          ))}
        </div>
        <Field label="Source">
          {(id) => (
            <Select id={id} value={source} onChange={(e) => setSource(e.target.value)}>
              {sources.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </Select>
          )}
        </Field>
        {mode === "generate" ? (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Records">
              {(id) => <Input id={id} type="number" min={1} max={200000} value={records} onChange={(e) => setRecords(Number(e.target.value))} />}
            </Field>
            <Field label="Seed" hint="Same seed, same rows and event IDs">
              {(id) => <Input id={id} type="number" min={0} value={seed} onChange={(e) => setSeed(Number(e.target.value))} />}
            </Field>
          </div>
        ) : (
          <Field label="File" hint="UTF-8 .csv (with header) or .jsonl, up to 50 MB. Re-uploading the same file returns the original batch.">
            {(id) => <Input id={id} type="file" accept=".csv,.jsonl,.ndjson" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />}
          </Field>
        )}
        {mode === "generate" && (
          <Notice>To inject faults with ground-truth labels, use the Failure Lab instead.</Notice>
        )}
        {ingest.isError && <ErrorState error={ingest.error} />}
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" busy={ingest.isPending}>
            Start ingestion
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
