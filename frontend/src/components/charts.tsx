import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Button, Table, Td, Th } from "@/components/ui";
import { num, pct, utcShort } from "@/lib/format";
import type { TimeseriesPoint } from "@/types/api";

// Categorical slots 1-3 of the reference palette, stepped separately for each surface.
// Validated with the dataviz palette checker in both modes (all-pairs safe for 3 series).
const PALETTE = {
  light: {
    accepted: "#2a78d6",
    quarantined: "#eb6834",
    skipped: "#1baf7a",
    surface: "#ffffff",
    grid: "#e2e8f0",
    text: "#64748b",
  },
  dark: {
    accepted: "#3987e5",
    quarantined: "#d95926",
    skipped: "#199e70",
    surface: "#0f172a",
    grid: "#1e293b",
    text: "#94a3b8",
  },
};

function useChartPalette() {
  const isDark = () => document.documentElement.classList.contains("dark");
  const [dark, setDark] = useState(isDark);
  useEffect(() => {
    const observer = new MutationObserver(() => setDark(isDark()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return dark ? PALETTE.dark : PALETTE.light;
}

const SERIES = [
  { key: "accepted", label: "Accepted" },
  { key: "quarantined", label: "Quarantined" },
  { key: "skipped", label: "Skipped (duplicates)" },
] as const;

function tooltipStyle(c: (typeof PALETTE)["light"]) {
  return {
    contentStyle: {
      background: c.surface,
      border: `1px solid ${c.grid}`,
      borderRadius: 6,
      fontSize: 12,
    },
    labelStyle: { color: c.text },
  };
}

export function ThroughputChart({ points }: { points: TimeseriesPoint[] }) {
  const c = useChartPalette();
  const [asTable, setAsTable] = useState(false);
  const data = points.map((p) => ({ ...p, label: utcShort(p.bucket) }));
  return (
    <div>
      <div className="mb-2 flex justify-end">
        <Button size="sm" variant="ghost" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}>
          {asTable ? "Show chart" : "Show table"}
        </Button>
      </div>
      {asTable ? (
        <Table>
          <thead>
            <tr>
              <Th>Bucket (UTC)</Th>
              <Th className="text-right">Batches</Th>
              {SERIES.map((s) => (
                <Th key={s.key} className="text-right">
                  {s.label}
                </Th>
              ))}
              <Th className="text-right">Pass rate</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {data.map((p) => (
              <tr key={p.bucket}>
                <Td>{p.label}</Td>
                <Td className="text-right tabular-nums">{p.batches}</Td>
                {SERIES.map((s) => (
                  <Td key={s.key} className="text-right tabular-nums">
                    {num(p[s.key])}
                  </Td>
                ))}
                <Td className="text-right tabular-nums">{pct(p.pass_rate)}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      ) : (
        <div className="h-64" role="img" aria-label="Rows per time bucket by outcome; a table view is available">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }} barCategoryGap="20%" maxBarSize={56}>
              <CartesianGrid vertical={false} stroke={c.grid} />
              <XAxis dataKey="label" tick={{ fill: c.text, fontSize: 11 }} tickLine={false} axisLine={{ stroke: c.grid }} minTickGap={24} />
              <YAxis tick={{ fill: c.text, fontSize: 11 }} tickLine={false} axisLine={false} width={48} allowDecimals={false} />
              <Tooltip cursor={{ fill: c.grid, opacity: 0.5 }} formatter={(v: number) => num(v)} {...tooltipStyle(c)} />
              <Legend wrapperStyle={{ fontSize: 12, color: c.text }} iconType="square" iconSize={10} />
              {SERIES.map((s, i) => (
                <Bar
                  key={s.key}
                  dataKey={s.key}
                  name={s.label}
                  stackId="rows"
                  fill={c[s.key]}
                  // 2px surface gap between stacked segments; rounded data-end on the top segment.
                  stroke={c.surface}
                  strokeWidth={2}
                  radius={i === SERIES.length - 1 ? [4, 4, 0, 0] : 0}
                  isAnimationActive={false}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

export function PassRateChart({ points }: { points: TimeseriesPoint[] }) {
  const c = useChartPalette();
  const data = points
    .filter((p) => p.pass_rate !== null)
    .map((p) => ({ label: utcShort(p.bucket), rate: Number(((p.pass_rate ?? 0) * 100).toFixed(2)) }));
  return (
    <div className="h-48" role="img" aria-label="Share of received rows accepted per time bucket">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke={c.grid} />
          <XAxis dataKey="label" tick={{ fill: c.text, fontSize: 11 }} tickLine={false} axisLine={{ stroke: c.grid }} minTickGap={24} />
          <YAxis domain={[0, 100]} tick={{ fill: c.text, fontSize: 11 }} tickLine={false} axisLine={false} width={48} unit="%" />
          <Tooltip formatter={(v: number) => [`${v}%`, "Accepted share"]} {...tooltipStyle(c)} />
          <Line
            type="monotone"
            dataKey="rate"
            stroke={c.accepted}
            strokeWidth={2}
            dot={{ r: 4, fill: c.accepted, stroke: c.surface, strokeWidth: 2 }}
            activeDot={{ r: 5 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
