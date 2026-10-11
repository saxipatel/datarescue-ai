import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { download } from "@/api/client";
import { api } from "@/api/endpoints";
import { ProposalDialog } from "@/components/ProposalDialog";
import {
  Badge,
  Button,
  Card,
  Dialog,
  EmptyState,
  ErrorState,
  Input,
  Json,
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
import { shortId, utc } from "@/lib/format";
import type { QuarantineDetail } from "@/types/api";

const LIMIT = 50;
const STATES = ["OPEN", "MANUAL_REVIEW", "REPLAYED", "REPAIRED"];

export function QuarantinePage() {
  const [params, setParams] = useSearchParams();
  const batchId = params.get("batch_id") ?? "";
  const state = params.get("state") ?? "OPEN";
  const code = params.get("code") ?? "";
  const offset = Number(params.get("offset") ?? 0);
  const { can } = useAuth();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [detailId, setDetailId] = useState<string | null>(null);
  const [proposeFor, setProposeFor] = useState<string | null>(null);
  const [codeInput, setCodeInput] = useState(code);

  const query = {
    batch_id: batchId || undefined,
    state: state || undefined,
    code: code ? [code] : undefined,
  };
  const records = useQuery({
    queryKey: ["quarantine", batchId, state, code, offset],
    queryFn: () => api.quarantine({ limit: LIMIT, offset, ...query }),
  });
  const incidentForBatch = useMutation({
    mutationFn: async (batch: string) => {
      const page = await api.incidents({ limit: 1, offset: 0, batch_id: batch });
      if (!page.items.length) throw new Error("This batch has no incident; repairs are attached to incidents.");
      return page.items[0].id;
    },
    onSuccess: setProposeFor,
  });
  const exporter = useMutation({
    mutationFn: () => download("/quarantine/export.csv", "quarantine.csv", query),
  });

  function set(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("offset");
    setParams(next);
    setSelected(new Set());
  }

  const items = records.data?.items ?? [];
  const selectedBatches = new Set(items.filter((r) => selected.has(r.id)).map((r) => r.batch_id));

  return (
    <div className="space-y-4">
      <PageHeader
        title="Quarantine"
        subtitle="Rows that failed validation. Original payloads are kept as received; nothing here is deleted."
        actions={
          <>
            <Button size="sm" busy={exporter.isPending} onClick={() => exporter.mutate()}>
              Export CSV
            </Button>
            {can("propose_repair") && (
              <Button
                size="sm"
                variant="primary"
                disabled={selected.size === 0 || selectedBatches.size !== 1}
                title={selectedBatches.size > 1 ? "Select rows from a single batch" : undefined}
                busy={incidentForBatch.isPending}
                onClick={() => incidentForBatch.mutate([...selectedBatches][0])}
              >
                Propose repair for {selected.size || ""} selected
              </Button>
            )}
          </>
        }
      />
      {incidentForBatch.isError && <ErrorState error={incidentForBatch.error} />}
      {exporter.isError && <ErrorState error={exporter.error} />}
      <Card padded={false}>
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-200 p-3 dark:border-slate-800">
          <Select aria-label="State" value={state} onChange={(e) => set("state", e.target.value)} className="w-44">
            <option value="">Any state</option>
            {STATES.map((s) => (
              <option key={s} value={s}>
                {s.toLowerCase()}
              </option>
            ))}
          </Select>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              set("code", codeInput.trim().toUpperCase());
            }}
          >
            <Input aria-label="Error code" placeholder="Error code, e.g. AMOUNT_NON_POSITIVE" value={codeInput} onChange={(e) => setCodeInput(e.target.value)} className="w-72" />
          </form>
          {batchId && (
            <span className="flex items-center gap-2 text-sm">
              batch <Link className="font-mono text-teal-700 hover:underline dark:text-teal-400" to={`/batches/${batchId}`}>{shortId(batchId)}</Link>
              <Button size="sm" variant="ghost" onClick={() => set("batch_id", "")}>
                Clear
              </Button>
            </span>
          )}
        </div>
        {records.isError ? (
          <div className="p-4">
            <ErrorState error={records.error} />
          </div>
        ) : !records.data ? (
          <div className="p-4">
            <Skeleton rows={8} />
          </div>
        ) : items.length === 0 ? (
          <div className="p-4">
            <EmptyState title="No quarantined records match" />
          </div>
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>
                    <input
                      type="checkbox"
                      aria-label="Select all on this page"
                      checked={items.every((r) => selected.has(r.id))}
                      onChange={(e) => setSelected(e.target.checked ? new Set(items.map((r) => r.id)) : new Set())}
                    />
                  </Th>
                  <Th>Event</Th>
                  <Th>Batch</Th>
                  <Th>Reason codes</Th>
                  <Th>State</Th>
                  <Th>Quarantined (UTC)</Th>
                  <Th />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {items.map((r) => (
                  <tr key={r.id}>
                    <Td>
                      <input
                        type="checkbox"
                        aria-label={`Select ${r.event_id}`}
                        checked={selected.has(r.id)}
                        onChange={(e) => {
                          const next = new Set(selected);
                          if (e.target.checked) next.add(r.id);
                          else next.delete(r.id);
                          setSelected(next);
                        }}
                      />
                    </Td>
                    <Td className="font-mono text-xs">{r.event_id.slice(0, 18)}</Td>
                    <Td>
                      <button className="font-mono text-xs text-teal-700 hover:underline dark:text-teal-400" onClick={() => set("batch_id", r.batch_id)}>
                        {shortId(r.batch_id)}
                      </button>
                    </Td>
                    <Td className="whitespace-normal">
                      <div className="flex flex-wrap gap-1">
                        {r.error_codes.map((c) => (
                          <Badge key={c} tone="red">
                            {c}
                          </Badge>
                        ))}
                      </div>
                    </Td>
                    <Td>
                      <StatusBadge status={r.state} />
                    </Td>
                    <Td className="text-xs">{utc(r.created_at)}</Td>
                    <Td>
                      <Button size="sm" variant="ghost" onClick={() => setDetailId(r.id)}>
                        Inspect
                      </Button>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <Pagination total={records.data.total} limit={LIMIT} offset={offset} onChange={(o) => set("offset", String(o))} />
          </>
        )}
      </Card>
      {detailId && <RecordDialog id={detailId} onClose={() => setDetailId(null)} />}
      {proposeFor && (
        <ProposalDialog
          open
          incidentId={proposeFor}
          initialIds={[...selected]}
          initialAction="MANUAL_REVIEW_REQUIRED"
          onClose={() => setProposeFor(null)}
        />
      )}
    </div>
  );
}

function RecordDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const record = useQuery({ queryKey: ["quarantine-record", id], queryFn: () => api.quarantineRecord(id) });
  return (
    <Dialog open title="Quarantined record" onClose={onClose} wide>
      {record.isError ? <ErrorState error={record.error} /> : !record.data ? <Skeleton rows={6} /> : <RecordBody r={record.data} />}
    </Dialog>
  );
}

function RecordBody({ r }: { r: QuarantineDetail }) {
  const currency = r.raw_payload.currency;
  const previewable = r.error_codes.length === 1 && r.error_codes[0] === "CURRENCY_INVALID_FORMAT" && typeof currency === "string";
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap gap-2">
        <StatusBadge status={r.state} />
        <span className="font-mono text-xs">{r.event_id}</span>
      </div>
      <Table>
        <thead>
          <tr>
            <Th>Code</Th>
            <Th>Field</Th>
            <Th>Message</Th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
          {r.error_details.map((d, i) => (
            <tr key={i}>
              <Td className="font-mono text-xs">{d.code}</Td>
              <Td>{d.field ?? "—"}</Td>
              <Td className="whitespace-normal">{d.message}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {previewable && (
        <Notice>
          Diff preview for NORMALIZE_CURRENCY_CASE: <code className="font-mono">currency</code>{" "}
          <span className="rounded bg-red-100 px-1 line-through dark:bg-red-900">{currency}</span> →{" "}
          <span className="rounded bg-emerald-100 px-1 dark:bg-emerald-900">{currency.toUpperCase()}</span>. The server re-validates the
          whole row before any change.
        </Notice>
      )}
      <div>
        <h3 className="mb-1 text-xs font-semibold uppercase text-slate-500">Raw payload (customer id masked)</h3>
        <Json value={r.raw_payload} />
      </div>
      {r.repair_candidate && (
        <div>
          <h3 className="mb-1 text-xs font-semibold uppercase text-slate-500">Applied repair candidate</h3>
          <Json value={r.repair_candidate} />
        </div>
      )}
      {r.resolution_note && <p className="text-xs text-slate-500">{r.resolution_note}</p>}
    </div>
  );
}
