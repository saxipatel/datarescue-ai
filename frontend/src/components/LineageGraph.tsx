import { useMemo } from "react";

import type { LineageGraph as Graph } from "@/types/api";

const COL_W = 190;
const ROW_H = 84;
const NODE_W = 160;
const NODE_H = 52;
const PAD = 16;

type Layout = Record<string, { x: number; y: number; depth: number }>;

// Longest-path layering: each node sits one column right of its deepest parent.
function layout(graph: Graph): { pos: Layout; width: number; height: number } {
  const parents: Record<string, string[]> = {};
  graph.nodes.forEach((n) => (parents[n.node_key] = []));
  graph.edges.forEach((e) => parents[e.downstream]?.push(e.upstream));
  const depth: Record<string, number> = {};
  const visit = (key: string, stack: Set<string>): number => {
    if (depth[key] !== undefined) return depth[key];
    if (stack.has(key)) return 0; // cycle guard; the backend rejects cycles anyway
    stack.add(key);
    const d = Math.max(-1, ...(parents[key] ?? []).map((p) => visit(p, stack))) + 1;
    stack.delete(key);
    depth[key] = d;
    return d;
  };
  graph.nodes.forEach((n) => visit(n.node_key, new Set()));
  const columns: Record<number, string[]> = {};
  graph.nodes.forEach((n) => (columns[depth[n.node_key]] ??= []).push(n.node_key));
  const maxRows = Math.max(1, ...Object.values(columns).map((c) => c.length));
  const pos: Layout = {};
  Object.entries(columns).forEach(([d, keys]) => {
    const offset = ((maxRows - keys.length) * ROW_H) / 2;
    keys.sort().forEach((key, i) => {
      pos[key] = { x: PAD + Number(d) * COL_W, y: PAD + offset + i * ROW_H, depth: Number(d) };
    });
  });
  const cols = Object.keys(columns).length;
  return { pos, width: PAD * 2 + (cols - 1) * COL_W + NODE_W, height: PAD * 2 + maxRows * ROW_H - (ROW_H - NODE_H) };
}

export function LineageGraph({
  graph,
  origin,
  impacted,
  selected,
  onSelect,
}: {
  graph: Graph;
  origin?: string | null;
  impacted?: Set<string>;
  selected?: string | null;
  onSelect?: (key: string) => void;
}) {
  const { pos, width, height } = useMemo(() => layout(graph), [graph]);
  const hot = (key: string) => key === origin || impacted?.has(key);

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="min-w-[720px]"
        role="img"
        aria-label="Dataset lineage graph; the table below lists the same information"
      >
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0,0 L10,5 L0,10 z" className="fill-slate-400" />
          </marker>
          <marker id="arrow-hot" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0,0 L10,5 L0,10 z" className="fill-amber-500" />
          </marker>
        </defs>
        {graph.edges.map((e) => {
          const a = pos[e.upstream];
          const b = pos[e.downstream];
          if (!a || !b) return null;
          const x1 = a.x + NODE_W;
          const y1 = a.y + NODE_H / 2;
          const x2 = b.x;
          const y2 = b.y + NODE_H / 2;
          const mid = (x1 + x2) / 2;
          const active = hot(e.upstream) && hot(e.downstream);
          return (
            <path
              key={`${e.upstream}-${e.downstream}`}
              d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2 - 2},${y2}`}
              fill="none"
              strokeWidth={active ? 2.5 : 1.5}
              className={active ? "stroke-amber-500" : "stroke-slate-300 dark:stroke-slate-600"}
              markerEnd={active ? "url(#arrow-hot)" : "url(#arrow)"}
            />
          );
        })}
        {graph.nodes.map((n) => {
          const p = pos[n.node_key];
          if (!p) return null;
          const isOrigin = n.node_key === origin;
          const isImpacted = impacted?.has(n.node_key);
          const isSelected = n.node_key === selected;
          const box = isOrigin
            ? "fill-red-50 stroke-red-500 dark:fill-red-950"
            : isImpacted
              ? "fill-amber-50 stroke-amber-500 dark:fill-amber-950"
              : "fill-white stroke-slate-300 dark:fill-slate-900 dark:stroke-slate-600";
          return (
            <g
              key={n.node_key}
              transform={`translate(${p.x},${p.y})`}
              tabIndex={onSelect ? 0 : -1}
              role={onSelect ? "button" : undefined}
              aria-label={`${n.display_name} (${n.node_type})${isOrigin ? ", incident origin" : isImpacted ? ", impacted" : ""}`}
              onClick={() => onSelect?.(n.node_key)}
              onKeyDown={(ev) => {
                if (ev.key === "Enter" || ev.key === " ") onSelect?.(n.node_key);
              }}
              className={onSelect ? "cursor-pointer outline-none" : ""}
            >
              <rect
                width={NODE_W}
                height={NODE_H}
                rx={8}
                strokeWidth={isSelected ? 3 : 1.5}
                className={box}
              />
              <text x={10} y={21} className="fill-slate-900 text-[12px] font-semibold dark:fill-slate-100">
                {n.display_name.length > 22 ? `${n.display_name.slice(0, 21)}…` : n.display_name}
              </text>
              <text x={10} y={38} className="fill-slate-500 text-[10px] uppercase tracking-wide">
                {n.node_type.replace("_", " ")}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
