import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "@/api/endpoints";
import { PassRateChart, ThroughputChart } from "@/components/charts";
import {
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  Select,
  SeverityBadge,
  Skeleton,
  Stat,
  StatusBadge,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { ago, duration, label, num, pct, utc } from "@/lib/format";

const RANGES = [
  { value: "12", label: "Last 12 hours", bucket: "hour" },
  { value: "48", label: "Last 48 hours", bucket: "hour" },
  { value: "336", label: "Last 14 days", bucket: "day" },
] as const;

export function OverviewPage() {
  const [range, setRange] = useState<(typeof RANGES)[number]["value"]>("48");
  const selected = RANGES.find((r) => r.value === range)!;
  const overview = useQuery({ queryKey: ["overview"], queryFn: api.overview, refetchInterval: 10_000 });
  const series = useQuery({
    queryKey: ["timeseries", range],
    queryFn: () => api.timeseries(selected.bucket, Number(range)),
    refetchInterval: 15_000,
  });
  const revenue = useQuery({ queryKey: ["revenue"], queryFn: api.revenue });
  const incidents = useQuery({
    queryKey: ["incidents", "open-overview"],
    queryFn: () => api.incidents({ limit: 5, offset: 0, open_only: true }),
    refetchInterval: 10_000,
  });

  if (overview.isError) return <ErrorState error={overview.error} />;
  const o = overview.data;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Overview"
        subtitle={o ? `Computed from operational tables at ${utc(o.generated_at)}` : "Loading…"}
      />

      {!o ? (
        <Skeleton rows={6} />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Stat
              label="Open incidents"
              value={num(o.incidents.open)}
              tone={o.incidents.open ? "bad" : "good"}
              hint={`${num(o.incidents.open_critical)} critical · ${num(o.incidents.open_high)} high`}
            />
            <Stat label="Rows accepted" value={num(o.records.accepted)} hint={`${num(o.records.received)} received`} />
            <Stat
              label="Quarantine rate"
              value={pct(o.records.quarantine_rate, 2)}
              tone={(o.records.quarantine_rate ?? 0) > 0.01 ? "warn" : "default"}
              hint={`${num(o.records.open_quarantine)} records awaiting review`}
            />
            <Stat
              label="Mean time to detect"
              value={duration(o.mean_time_to_detect_seconds)}
              hint={`batch start → incident, n=${o.mttd_samples}`}
            />
            <Stat
              label="Recovery success"
              value={pct(o.recoveries.success_rate, 0)}
              hint={`${o.recoveries.succeeded} succeeded · ${o.recoveries.failed} failed`}
            />
            <Stat
              label="Mean incident duration"
              value={duration(o.incidents.mean_duration_seconds)}
              hint={`${num(o.incidents.resolved)} resolved · ${num(o.incidents.false_positives)} false positives`}
            />
          </div>

          <Card title="Pipeline health">
            <Table>
              <thead>
                <tr>
                  <Th>Source</Th>
                  <Th>Freshness</Th>
                  <Th>Last successful batch</Th>
                  <Th>Latest batch</Th>
                  <Th className="text-right">Open incidents</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {o.sources.map((s) => (
                  <tr key={s.id}>
                    <Td>
                      <Link className="font-medium hover:underline" to={`/batches?source=${s.name}`}>
                        {s.name}
                      </Link>
                    </Td>
                    <Td>
                      <StatusBadge status={s.freshness} />
                      <span className="ml-2 text-xs text-slate-500">limit {duration(s.freshness_max_age_minutes * 60)}</span>
                    </Td>
                    <Td title={utc(s.last_success_at)}>{ago(s.last_success_at)}</Td>
                    <Td>{s.last_status ? <StatusBadge status={s.last_status} /> : "—"}</Td>
                    <Td className="text-right tabular-nums">{s.open_incidents}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>
        </>
      )}

      <div className="grid gap-6 xl:grid-cols-3">
        <Card
          title="Throughput by outcome"
          className="xl:col-span-2"
          actions={
            <Select
              aria-label="Time range"
              value={range}
              onChange={(e) => setRange(e.target.value as typeof range)}
              className="w-40 py-1 text-xs"
            >
              {RANGES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </Select>
          }
        >
          {series.isError ? (
            <ErrorState error={series.error} />
          ) : !series.data ? (
            <Skeleton rows={6} />
          ) : series.data.points.length === 0 ? (
            <EmptyState title="No batches in this range" />
          ) : (
            <ThroughputChart points={series.data.points} />
          )}
        </Card>
        <Card title="Accepted share of received rows">
          {series.data && series.data.points.length > 0 ? (
            <PassRateChart points={series.data.points} />
          ) : (
            <EmptyState title="No data in this range" />
          )}
          <p className="mt-2 text-xs text-slate-500">
            Accepted ÷ received per bucket. Skipped duplicates and quarantined rows lower it.
          </p>
        </Card>
      </div>

      <Card title="Settled revenue by day (dbt mart_daily_revenue)">
        {revenue.isError ? (
          <ErrorState error={revenue.error} />
        ) : !revenue.data ? (
          <Skeleton />
        ) : !revenue.data.available ? (
          <EmptyState title="Analytics marts not built">
            Run <code className="font-mono">make dbt</code> to materialize them from accepted transactions.
          </EmptyState>
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>Date (UTC)</Th>
                  <Th>Currency</Th>
                  <Th className="text-right">Settled</Th>
                  <Th className="text-right">Settled events</Th>
                  <Th className="text-right">Refunded (not netted)</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {revenue.data.rows.slice(0, 9).map((r) => (
                  <tr key={`${r.revenue_date}-${r.currency}`}>
                    <Td>{r.revenue_date}</Td>
                    <Td>{r.currency}</Td>
                    <Td className="text-right tabular-nums">{Number(r.settled_amount).toLocaleString("en-US", { minimumFractionDigits: 2 })}</Td>
                    <Td className="text-right tabular-nums">{num(r.settled_count)}</Td>
                    <Td className="text-right tabular-nums">{Number(r.refunded_amount).toLocaleString("en-US", { minimumFractionDigits: 2 })}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <p className="mt-2 text-xs text-slate-500">
              Mart refreshed {ago(revenue.data.rows[0]?._refreshed_at)}. Currencies are never converted or summed together.
            </p>
          </>
        )}
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Open incidents" actions={<Link className="text-sm text-teal-700 hover:underline dark:text-teal-400" to="/incidents">All incidents</Link>}>
          {incidents.isError ? (
            <ErrorState error={incidents.error} />
          ) : !incidents.data ? (
            <Skeleton />
          ) : incidents.data.items.length === 0 ? (
            <EmptyState title="No open incidents">Use the Failure Lab to inject a fault.</EmptyState>
          ) : (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {incidents.data.items.map((i) => (
                <li key={i.id} className="flex items-center justify-between gap-3 py-2">
                  <div className="min-w-0">
                    <Link to={`/incidents/${i.id}`} className="block truncate text-sm font-medium hover:underline">
                      {i.title}
                    </Link>
                    <span className="text-xs text-slate-500">{ago(i.detected_at)}</span>
                  </div>
                  <div className="flex shrink-0 gap-1.5">
                    <SeverityBadge severity={i.severity} />
                    <StatusBadge status={i.state} />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Recent recovery activity" actions={<Link className="text-sm text-teal-700 hover:underline dark:text-teal-400" to="/recovery">Recovery center</Link>}>
          {!o ? (
            <Skeleton />
          ) : o.recoveries.recent.length === 0 ? (
            <EmptyState title="No recoveries yet" />
          ) : (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {o.recoveries.recent.map((r) => (
                <li key={r.id} className="flex items-center justify-between gap-3 py-2">
                  <div className="min-w-0">
                    <Link to={`/incidents/${r.incident_id}`} className="block truncate text-sm font-medium hover:underline">
                      {label(r.action)}
                    </Link>
                    <span className="block truncate text-xs text-slate-500">
                      {r.incident_title} · attempt {r.attempts} · {ago(r.completed_at ?? r.created_at)}
                    </span>
                  </div>
                  <StatusBadge status={r.state} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
