import { utc } from "@/lib/format";
import type { TimelineEntry } from "@/types/api";

function summary(entry: TimelineEntry): string {
  const d = entry.details;
  if (entry.action === "incident.transition") return `${d.from} → ${d.to}${d.note ? ` · ${d.note}` : ""}`;
  if (entry.action === "proposal.dry_run" && d.counts) return `dry run ${JSON.stringify(d.counts)}`;
  if (typeof d.note === "string" && d.note) return d.note;
  if (typeof d.action === "string") return d.action;
  if (typeof d.error === "string") return d.error;
  return "";
}

export function Timeline({ entries }: { entries: TimelineEntry[] }) {
  if (!entries.length) return <p className="text-sm text-slate-500">No events yet.</p>;
  return (
    <ol className="relative space-y-3 border-l border-slate-200 pl-4 dark:border-slate-700">
      {entries.map((e, i) => (
        <li key={`${e.at}-${i}`} className="relative">
          <span className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-white bg-teal-600 dark:border-slate-900" />
          <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
            <span className="font-medium">{e.action}</span>
            <span className="text-xs text-slate-500">
              {e.actor_id.startsWith("system:") ? e.actor_id : `${e.actor_role ?? "user"}`}
            </span>
            <time className="text-xs text-slate-500" dateTime={e.at}>
              {utc(e.at)}
            </time>
          </div>
          {summary(e) && (
            <p className="mt-0.5 break-words text-xs text-slate-600 dark:text-slate-400">{summary(e)}</p>
          )}
        </li>
      ))}
    </ol>
  );
}
