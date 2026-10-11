import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";

import { api } from "@/api/endpoints";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Notice,
  PageHeader,
  Select,
  Skeleton,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";
import { duration, label, num, utc } from "@/lib/format";
import type { Simulation } from "@/types/api";

const DESCRIPTIONS: Record<string, string> = {
  clean: "No faults. Useful as a baseline.",
  duplicate_event_ids: "Re-sends rows from the previous batch and repeats rows in-batch (identical payloads).",
  conflicting_duplicates: "Reuses event IDs with a changed amount.",
  null_customer_id: "Drops customer_id on some rows.",
  invalid_amount: "Negative and zero amounts.",
  malformed_currency: "Lower-case codes (repairable) and invalid codes.",
  unknown_status: "Statuses outside the contract.",
  schema_break: "The tail of the batch switches to schema v2 (amount renamed).",
  unexpected_field: "Adds a field the contract doesn't know.",
  missing_field: "Removes merchant_id.",
  impossible_timestamp: "event_time in 2099.",
  late_events: "Events far behind the watermark (warning severity).",
  out_of_order: "Events slightly beyond the grace window.",
  corrupted_payload: "Truncated, unparseable lines.",
  mixed_faults: "A mixture of the row-level faults above.",
  missing_batch: "Skips one sequence number; completeness detects the gap.",
  stale_pipeline: "Advances the clock past the freshness window for this source.",
  volume_drop: "Delivers fewer rows than the manifest and baseline (uses corruption % as the drop).",
};

export function FailureLabPage() {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: api.scenarios });
  const sources = useQuery({ queryKey: ["sources"], queryFn: api.sources });
  const history = useQuery({
    queryKey: ["simulations"],
    queryFn: () => api.simulations({ limit: 20, offset: 0 }),
    refetchInterval: 3_000,
  });
  const [form, setForm] = useState({
    scenario: "duplicate_event_ids",
    source: "payments_core",
    records: 1000,
    corruption_pct: 10,
    seed: "",
    run_label: "",
  });
  const run = useMutation({
    mutationFn: () =>
      api.simulate({
        scenario: form.scenario,
        source: form.source,
        records: form.records,
        corruption_pct: form.corruption_pct,
        seed: form.seed === "" ? undefined : Number(form.seed),
        run_label: form.run_label || undefined,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["simulations"] }),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    run.mutate();
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Failure Lab"
        subtitle="Inject a deterministic, labeled fault and compare what the platform detected with the ground truth."
      />
      <div className="grid gap-6 xl:grid-cols-3">
        <Card title="Inject a scenario">
          {!can("simulate") ? (
            <Notice>Your role is read-only here. Sign in as engineer@example.test to inject failures.</Notice>
          ) : (
            <form onSubmit={submit} className="space-y-3">
              <Field label="Scenario">
                {(id) => (
                  <Select id={id} value={form.scenario} onChange={(e) => setForm({ ...form, scenario: e.target.value })}>
                    {(scenarios.data?.scenarios ?? []).map((s) => (
                      <option key={s} value={s}>
                        {label(s)}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <p className="text-xs text-slate-600 dark:text-slate-400">{DESCRIPTIONS[form.scenario]}</p>
              <Field label="Source">
                {(id) => (
                  <Select id={id} value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })}>
                    {sources.data?.map((s) => (
                      <option key={s.id}>{s.name}</option>
                    ))}
                  </Select>
                )}
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Batch size">
                  {(id) => (
                    <Input id={id} type="number" min={10} max={100000} value={form.records} onChange={(e) => setForm({ ...form, records: Number(e.target.value) })} />
                  )}
                </Field>
                <Field label="Anomaly %">
                  {(id) => (
                    <Input id={id} type="number" min={0} max={100} step={0.5} value={form.corruption_pct} onChange={(e) => setForm({ ...form, corruption_pct: Number(e.target.value) })} />
                  )}
                </Field>
                <Field label="Seed" hint="Blank = derived from source + sequence">
                  {(id) => <Input id={id} type="number" min={0} value={form.seed} onChange={(e) => setForm({ ...form, seed: e.target.value })} />}
                </Field>
                <Field label="Run label">
                  {(id) => <Input id={id} maxLength={80} value={form.run_label} onChange={(e) => setForm({ ...form, run_label: e.target.value })} />}
                </Field>
              </div>
              {run.isError && <ErrorState error={run.error} />}
              <Button type="submit" variant="primary" busy={run.isPending} className="w-full">
                Inject
              </Button>
            </form>
          )}
        </Card>
        <div className="min-w-0 space-y-6 xl:col-span-2">
          {run.data && <LiveResult id={run.data.simulation.id} initial={run.data.simulation} />}
          <Card title="Recent runs" padded={false}>
            {history.isError ? (
              <div className="p-4">
                <ErrorState error={history.error} />
              </div>
            ) : !history.data ? (
              <div className="p-4">
                <Skeleton />
              </div>
            ) : history.data.items.length === 0 ? (
              <div className="p-4">
                <EmptyState title="No simulations yet" />
              </div>
            ) : (
              <Table>
                <thead>
                  <tr>
                    <Th>Scenario</Th>
                    <Th className="text-right">Rows</Th>
                    <Th className="text-right">Anomaly %</Th>
                    <Th>Counts vs labels</Th>
                    <Th className="text-right">FP / FN</Th>
                    <Th>Incident</Th>
                    <Th>When (UTC)</Th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {history.data.items.map((s) => {
                    const ev = s.evaluation;
                    const incidentId = ev?.incident_id ?? ev?.incident_ids?.[0];
                    return (
                      <tr key={s.id}>
                        <Td>
                          {label(s.scenario)}
                          {s.run_label && <div className="text-xs text-slate-500">{s.run_label}</div>}
                        </Td>
                        <Td className="text-right tabular-nums">{num(s.record_count)}</Td>
                        <Td className="text-right tabular-nums">{s.corruption_pct}</Td>
                        <Td>
                          {!ev ? <Badge tone="blue">ingesting</Badge> : ev.counts_match === undefined ? "—" : ev.counts_match ? <Badge tone="green">match</Badge> : <Badge tone="red">differ</Badge>}
                        </Td>
                        <Td className="text-right tabular-nums">
                          {ev?.quarantine_false_positives ?? "—"} / {ev?.quarantine_false_negatives ?? "—"}
                        </Td>
                        <Td>
                          {incidentId ? (
                            <Link to={`/incidents/${incidentId}`} className="text-teal-700 hover:underline dark:text-teal-400">
                              open
                            </Link>
                          ) : ev ? (
                            "none"
                          ) : (
                            "—"
                          )}
                        </Td>
                        <Td className="text-xs">{utc(s.created_at)}</Td>
                      </tr>
                    );
                  })}
                </tbody>
              </Table>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

function LiveResult({ id, initial }: { id: string; initial: Simulation }) {
  const sim = useQuery({
    queryKey: ["simulation", id],
    queryFn: () => api.simulation(id),
    initialData: initial,
    refetchInterval: (q) => (q.state.data?.evaluation ? false : 1_000),
  });
  return <Result sim={sim.data} />;
}

function Result({ sim }: { sim: Simulation }) {
  const ev = sim.evaluation;
  const incidentId = ev?.incident_id ?? ev?.incident_ids?.[0];
  return (
    <Card title={`Result · ${label(sim.scenario)}`}>
      {!ev ? (
        <div className="space-y-2">
          <p className="text-sm">Batch queued; the worker is ingesting it…</p>
          <Skeleton rows={2} />
        </div>
      ) : (
        <div className="space-y-3 text-sm">
          {ev.actual && ev.expected && (
            <Table>
              <thead>
                <tr>
                  <Th>Outcome</Th>
                  <Th className="text-right">Ground truth</Th>
                  <Th className="text-right">Observed</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {Object.entries(ev.actual).map(([k, v]) => (
                  <tr key={k}>
                    <Td>{label(k)}</Td>
                    <Td className="text-right tabular-nums">{num(ev.expected?.[k] ?? null)}</Td>
                    <Td className="text-right tabular-nums">{num(v)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
          <p>
            Quarantine precision: {ev.quarantine_true_positives ?? 0} true positives, {ev.quarantine_false_positives ?? 0} false
            positives, {ev.quarantine_false_negatives ?? 0} misses. Detection latency{" "}
            {duration(ev.detection_latency_seconds ?? null)}.
          </p>
          {incidentId ? (
            <Link to={`/incidents/${incidentId}`}>
              <Button variant="primary">Inspect incident</Button>
            </Link>
          ) : (
            <Notice>No incident opened (thresholds not breached for this scenario).</Notice>
          )}
        </div>
      )}
    </Card>
  );
}
