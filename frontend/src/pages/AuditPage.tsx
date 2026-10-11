import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";

import { api } from "@/api/endpoints";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Input,
  Json,
  KeyValue,
  Notice,
  PageHeader,
  Pagination,
  Select,
  Skeleton,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";
import { shortId, utc } from "@/lib/format";

const LIMIT = 50;
const ENTITY_TYPES = ["", "incident", "repair_proposal", "recovery_job", "batch", "quality_rule", "simulation", "dataset", "user", "source"];

export function AuditPage() {
  const { user, can } = useAuth();
  const info = useQuery({ queryKey: ["system-info"], queryFn: api.systemInfo });
  const ready = useQuery({ queryKey: ["ready"], queryFn: api.ready, retry: false, refetchInterval: 15_000 });

  return (
    <div className="space-y-6">
      <PageHeader title="Audit & Settings" subtitle="Append-only audit trail, your access, and how this environment is configured." />
      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Your access">
          {user && (
            <>
              <KeyValue items={[["Email", user.email], ["Role", <Badge key="r" tone="blue">{user.role}</Badge>]]} />
              <div className="mt-3 flex flex-wrap gap-1">
                {user.permissions?.map((p) => (
                  <Badge key={p}>{p}</Badge>
                ))}
              </div>
            </>
          )}
        </Card>
        <Card title="Environment">
          {info.isError ? (
            <ErrorState error={info.error} />
          ) : !info.data ? (
            <Skeleton />
          ) : (
            <KeyValue
              items={[
                ["Environment", info.data.environment],
                ["Task execution", info.data.task_mode === "rq" ? "Redis + RQ worker" : "inline"],
                ["LLM enrichment", info.data.llm.active ? `on (${info.data.llm.model})` : "off — deterministic only"],
                ["Failure injection", info.data.chaos_enabled ? "enabled" : "disabled"],
                ...Object.entries(info.data.integrations).map(([k, v]) => [k, v] as [string, string]),
              ]}
            />
          )}
        </Card>
        <Card title="Readiness">
          {ready.data ? (
            <KeyValue items={Object.entries(ready.data.checks).map(([k, v]) => [k, <Badge key={k} tone={v === "ok" || v === "inline" ? "green" : "red"}>{v}</Badge>])} />
          ) : ready.isError ? (
            <Notice tone="red">Not ready (database or queue unreachable).</Notice>
          ) : (
            <Skeleton />
          )}
        </Card>
      </div>
      {can("read_audit") ? (
        <AuditLog />
      ) : (
        <Notice>The full audit log is visible to admins. Incident and recovery pages show the audit events for those records.</Notice>
      )}
    </div>
  );
}

function AuditLog() {
  const [entityType, setEntityType] = useState("");
  const [action, setAction] = useState("");
  const [actionInput, setActionInput] = useState("");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const log = useQuery({
    queryKey: ["audit", entityType, action, offset],
    queryFn: () => api.audit({ limit: LIMIT, offset, entity_type: entityType || undefined, action: action || undefined }),
  });
  return (
    <Card title="Audit log" padded={false}>
      <div className="flex flex-wrap gap-3 border-b border-slate-200 p-3 dark:border-slate-800">
        <Select aria-label="Entity type" value={entityType} onChange={(e) => { setEntityType(e.target.value); setOffset(0); }} className="w-48">
          {ENTITY_TYPES.map((t) => (
            <option key={t} value={t}>
              {t || "All entities"}
            </option>
          ))}
        </Select>
        <form onSubmit={(e) => { e.preventDefault(); setAction(actionInput.trim()); setOffset(0); }}>
          <Input aria-label="Action" placeholder="Action, e.g. proposal.approved" value={actionInput} onChange={(e) => setActionInput(e.target.value)} className="w-64" />
        </form>
      </div>
      {log.isError ? (
        <div className="p-4"><ErrorState error={log.error} /></div>
      ) : !log.data ? (
        <div className="p-4"><Skeleton rows={8} /></div>
      ) : log.data.items.length === 0 ? (
        <div className="p-4"><EmptyState title="No audit events match" /></div>
      ) : (
        <>
          <Table>
            <thead>
              <tr>
                <Th>Time (UTC)</Th>
                <Th>Actor</Th>
                <Th>Action</Th>
                <Th>Entity</Th>
                <Th>Before → after hash</Th>
                <Th />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {log.data.items.map((e) => (
                <Fragment key={e.id}>
                  <tr>
                    <Td className="text-xs">{utc(e.occurred_at)}</Td>
                    <Td className="text-xs">
                      {e.actor_id.startsWith("system:") ? e.actor_id : shortId(e.actor_id)}
                      {e.actor_role && <span className="ml-1 text-slate-500">({e.actor_role})</span>}
                    </Td>
                    <Td className="font-mono text-xs">{e.action}</Td>
                    <Td className="text-xs">
                      {e.entity_type} <span className="font-mono">{shortId(e.entity_id)}</span>
                    </Td>
                    <Td className="font-mono text-xs text-slate-500">
                      {e.before_hash ? e.before_hash.slice(0, 8) : "—"} → {e.after_hash ? e.after_hash.slice(0, 8) : "—"}
                    </Td>
                    <Td>
                      <Button size="sm" variant="ghost" aria-expanded={open === e.id} onClick={() => setOpen(open === e.id ? null : e.id)}>
                        Details
                      </Button>
                    </Td>
                  </tr>
                  {open === e.id && (
                    <tr>
                      <td colSpan={6} className="px-3 pb-3">
                        <Json value={{ ...e.details, correlation_id: e.correlation_id }} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </Table>
          <Pagination total={log.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
        </>
      )}
    </Card>
  );
}
