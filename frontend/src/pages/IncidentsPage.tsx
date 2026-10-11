import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "@/api/endpoints";
import {
  Card,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
  Pagination,
  Select,
  SeverityBadge,
  Skeleton,
  StatusBadge,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { ago, label, utc } from "@/lib/format";

const LIMIT = 25;
const STATES = ["OPEN", "INVESTIGATING", "ACTION_REQUIRED", "ACKNOWLEDGED", "RECOVERING", "RESOLVED", "CLOSED_AS_FALSE_POSITIVE"];
const SEVERITIES = ["critical", "high", "medium", "low"];

export function IncidentsPage() {
  const [params, setParams] = useSearchParams();
  const view = params.get("view") ?? "open";
  const state = params.get("state") ?? "";
  const severity = params.get("severity") ?? "";
  const offset = Number(params.get("offset") ?? 0);
  const [search, setSearch] = useState(params.get("q") ?? "");

  const incidents = useQuery({
    queryKey: ["incidents", view, state, severity, offset, params.get("q")],
    queryFn: () =>
      api.incidents({
        limit: LIMIT,
        offset,
        open_only: view === "open",
        state: state ? [state] : undefined,
        severity: severity ? [severity] : undefined,
        q: params.get("q") ?? undefined,
      }),
    refetchInterval: 5_000,
  });

  function set(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("offset");
    setParams(next);
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Incidents" subtitle="Opened automatically when quality rules breach their thresholds." />
      <Card padded={false}>
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-200 p-3 dark:border-slate-800">
          <Select aria-label="View" value={view} onChange={(e) => set("view", e.target.value)} className="w-40">
            <option value="open">Open incidents</option>
            <option value="all">All (history)</option>
          </Select>
          <Select aria-label="State" value={state} onChange={(e) => set("state", e.target.value)} className="w-48">
            <option value="">Any state</option>
            {STATES.map((s) => (
              <option key={s} value={s}>
                {label(s)}
              </option>
            ))}
          </Select>
          <Select aria-label="Severity" value={severity} onChange={(e) => set("severity", e.target.value)} className="w-36">
            <option value="">Any severity</option>
            {SEVERITIES.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </Select>
          <form
            className="flex-1"
            onSubmit={(e) => {
              e.preventDefault();
              set("q", search.trim());
            }}
          >
            <Input aria-label="Search title or summary" placeholder="Search title or summary, press Enter" value={search} onChange={(e) => setSearch(e.target.value)} />
          </form>
        </div>
        {incidents.isError ? (
          <div className="p-4">
            <ErrorState error={incidents.error} />
          </div>
        ) : !incidents.data ? (
          <div className="p-4">
            <Skeleton rows={6} />
          </div>
        ) : incidents.data.items.length === 0 ? (
          <div className="p-4">
            <EmptyState title={view === "open" ? "No open incidents" : "No incidents match"}>
              Inject a failure from the <Link className="underline" to="/failure-lab">Failure Lab</Link>.
            </EmptyState>
          </div>
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>Incident</Th>
                  <Th>Severity</Th>
                  <Th>State</Th>
                  <Th>Failed rules</Th>
                  <Th>Detected (UTC)</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {incidents.data.items.map((i) => (
                  <tr key={i.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/50">
                    <Td className="max-w-md whitespace-normal">
                      <Link to={`/incidents/${i.id}`} className="font-medium text-teal-700 hover:underline dark:text-teal-400">
                        {i.title}
                      </Link>
                      <div className="text-xs text-slate-500">{i.category}</div>
                    </Td>
                    <Td>
                      <SeverityBadge severity={i.severity} />
                    </Td>
                    <Td>
                      <StatusBadge status={i.state} />
                    </Td>
                    <Td className="font-mono text-xs">{i.failed_rules.join(", ")}</Td>
                    <Td className="text-xs">
                      {utc(i.detected_at)}
                      <div className="text-slate-500">{ago(i.detected_at)}</div>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <Pagination total={incidents.data.total} limit={LIMIT} offset={offset} onChange={(o) => set("offset", String(o))} />
          </>
        )}
      </Card>
    </div>
  );
}
