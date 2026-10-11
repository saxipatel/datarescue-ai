import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "@/api/endpoints";
import { LineageGraph } from "@/components/LineageGraph";
import { Card, ErrorState, Notice, PageHeader, Select, Skeleton, Table, Td, Th } from "@/components/ui";
import { ago, utc } from "@/lib/format";

export function LineagePage() {
  const [params, setParams] = useSearchParams();
  const incidentId = params.get("incident") ?? "";
  const [selected, setSelected] = useState<string | null>(null);
  const graph = useQuery({ queryKey: ["lineage"], queryFn: api.lineage });
  const incidents = useQuery({
    queryKey: ["incidents", "lineage-picker"],
    queryFn: () => api.incidents({ limit: 50, offset: 0 }),
  });
  const impact = useQuery({
    queryKey: ["impact", incidentId],
    queryFn: () => api.impact(incidentId),
    enabled: Boolean(incidentId),
  });

  const impacted = useMemo(() => new Set(impact.data?.impacted.map((n) => n.node_key) ?? []), [impact.data]);
  const pathTo = selected ? impact.data?.impacted.find((n) => n.node_key === selected)?.path : undefined;

  if (graph.isError) return <ErrorState error={graph.error} />;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Lineage Explorer"
        subtitle="Declared dataset dependencies from source to dashboards. Impact is dataset-level, not row-level."
        actions={
          <Select
            aria-label="Highlight impact of incident"
            value={incidentId}
            onChange={(e) => {
              setSelected(null);
              setParams(e.target.value ? { incident: e.target.value } : {});
            }}
            className="w-80"
          >
            <option value="">Highlight an incident's impact…</option>
            {incidents.data?.items.map((i) => (
              <option key={i.id} value={i.id}>
                {i.title.slice(0, 60)} ({i.state.toLowerCase()})
              </option>
            ))}
          </Select>
        }
      />
      <Card title="Dependency graph">
        {!graph.data ? (
          <Skeleton rows={6} />
        ) : (
          <>
            <LineageGraph
              graph={graph.data}
              origin={impact.data?.origin}
              impacted={impacted}
              selected={selected}
              onSelect={setSelected}
            />
            <div className="mt-3 flex flex-wrap gap-4 text-xs text-slate-500">
              <span className="flex items-center gap-1"><span className="h-3 w-3 rounded border-2 border-red-500" /> incident origin</span>
              <span className="flex items-center gap-1"><span className="h-3 w-3 rounded border-2 border-amber-500" /> downstream impact</span>
              <span>Select a node to see its path.</span>
            </div>
            {incidentId && impact.data?.status === "unknown" && <Notice tone="amber">{impact.data.note}</Notice>}
            {pathTo && (
              <Notice>
                Path: <span className="font-mono">{pathTo.join(" → ")}</span>
              </Notice>
            )}
            {incidentId && (
              <p className="mt-2 text-sm">
                <Link to={`/incidents/${incidentId}`} className="text-teal-700 hover:underline dark:text-teal-400">
                  Open incident
                </Link>
              </p>
            )}
          </>
        )}
      </Card>
      <Card title="Nodes (accessible table view)" padded={false}>
        {graph.data && (
          <Table>
            <thead>
              <tr>
                <Th>Node</Th>
                <Th>Type</Th>
                <Th>Feeds</Th>
                <Th>Last refresh (observed)</Th>
                <Th>Impact</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {graph.data.nodes.map((n) => {
                const hit = impact.data?.impacted.find((i) => i.node_key === n.node_key);
                return (
                  <tr key={n.node_key}>
                    <Td className="whitespace-normal">
                      <div className="font-mono text-xs">{n.node_key}</div>
                      <div className="text-xs text-slate-500">{String(n.metadata.description ?? "")}</div>
                    </Td>
                    <Td>{n.node_type}</Td>
                    <Td className="font-mono text-xs">
                      {graph.data.edges.filter((e) => e.upstream === n.node_key).map((e) => e.downstream).join(", ") || "—"}
                    </Td>
                    <Td className="text-xs" title={utc(n.last_refreshed_at)}>
                      {n.last_refreshed_at ? ago(n.last_refreshed_at) : "unknown"}
                    </Td>
                    <Td className="text-xs">
                      {n.node_key === impact.data?.origin ? "origin" : hit ? `depth ${hit.depth}, ${hit.confidence} confidence` : "—"}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
