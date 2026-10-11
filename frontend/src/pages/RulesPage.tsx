import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";

import { api } from "@/api/endpoints";
import {
  Badge,
  Button,
  Card,
  Dialog,
  ErrorState,
  Field,
  Notice,
  PageHeader,
  Select,
  SeverityBadge,
  Skeleton,
  Table,
  Td,
  TextArea,
  Th,
} from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";
import { utc } from "@/lib/format";
import type { Rule } from "@/types/api";

export function RulesPage() {
  const { can } = useAuth();
  const [editing, setEditing] = useState<Rule | null>(null);
  const rules = useQuery({ queryKey: ["rules"], queryFn: api.rules });
  const history = useQuery({
    queryKey: ["audit", "rule.updated"],
    queryFn: () => api.audit({ limit: 20, offset: 0, action: "rule.updated" }),
    enabled: can("read_audit"),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Quality Rules"
        subtitle="Rules run on every batch. Locked rules protect database constraints: their thresholds and severity are editable, but they can't be turned off."
      />
      {!can("manage_rules") && <Notice>Read-only. Admins can change thresholds; every change is versioned and audited.</Notice>}
      <Card padded={false}>
        {rules.isError ? (
          <div className="p-4">
            <ErrorState error={rules.error} />
          </div>
        ) : !rules.data ? (
          <div className="p-4">
            <Skeleton rows={8} />
          </div>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Rule</Th>
                <Th>Severity</Th>
                <Th>Enabled</Th>
                <Th>Configuration</Th>
                <Th>Version</Th>
                <Th />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {rules.data.map((r) => (
                <tr key={r.id}>
                  <Td className="max-w-sm whitespace-normal">
                    <div className="font-mono text-xs font-semibold">{r.rule_key}</div>
                    <div className="text-xs text-slate-500">{r.description}</div>
                  </Td>
                  <Td>
                    <SeverityBadge severity={r.severity} />
                  </Td>
                  <Td>
                    {r.enabled ? <Badge tone="green">on</Badge> : <Badge>off</Badge>}
                    {Boolean(r.configuration.locked) && <span className="ml-1 text-xs text-slate-500">locked</span>}
                  </Td>
                  <Td className="max-w-xs whitespace-normal font-mono text-xs">
                    {Object.entries(r.configuration)
                      .filter(([k]) => k !== "locked")
                      .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
                      .join("  ")}
                  </Td>
                  <Td>v{r.version}</Td>
                  <Td>
                    {can("manage_rules") && (
                      <Button size="sm" onClick={() => setEditing(r)}>
                        Edit
                      </Button>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      {can("read_audit") && (
        <Card title="Change history">
          {!history.data ? (
            <Skeleton />
          ) : history.data.items.length === 0 ? (
            <p className="text-sm text-slate-500">No rule changes yet.</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {history.data.items.map((e) => (
                <li key={e.id} className="rounded-md border border-slate-200 p-2 dark:border-slate-800">
                  <div className="flex flex-wrap gap-2">
                    <span className="font-mono text-xs font-semibold">{String(e.details.rule_key)}</span>
                    <span className="text-xs text-slate-500">{utc(e.occurred_at)}</span>
                    <span className="text-xs">{String(e.details.reason ?? "")}</span>
                  </div>
                  <div className="mt-1 grid gap-2 font-mono text-xs sm:grid-cols-2">
                    <span className="rounded bg-red-50 p-1 dark:bg-red-950">before {JSON.stringify(e.details.before)}</span>
                    <span className="rounded bg-emerald-50 p-1 dark:bg-emerald-950">after {JSON.stringify(e.details.after)}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
      {editing && <EditRule rule={editing} onClose={() => setEditing(null)} />}
    </div>
  );
}

function EditRule({ rule, onClose }: { rule: Rule; onClose: () => void }) {
  const queryClient = useQueryClient();
  const editable = Object.fromEntries(Object.entries(rule.configuration).filter(([k]) => k !== "locked"));
  const [severity, setSeverity] = useState(rule.severity);
  const [enabled, setEnabled] = useState(rule.enabled);
  const [config, setConfig] = useState(JSON.stringify(editable, null, 2));
  const [reason, setReason] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (configuration: Record<string, unknown>) =>
      api.updateRule(rule.id, { severity, enabled, configuration, expected_version: rule.version, reason }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["rules"] });
      queryClient.invalidateQueries({ queryKey: ["audit"] });
      onClose();
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    try {
      const parsed = JSON.parse(config);
      setParseError(null);
      save.mutate(parsed);
    } catch {
      setParseError("Configuration must be valid JSON");
    }
  }

  return (
    <Dialog open title={`Edit ${rule.rule_key} (v${rule.version})`} onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Severity" hint="medium and above open incidents">
            {(id) => (
              <Select id={id} value={severity} onChange={(e) => setSeverity(e.target.value as Rule["severity"])}>
                {["low", "medium", "high", "critical"].map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Enabled">
            {(id) => (
              <Select id={id} value={String(enabled)} disabled={Boolean(rule.configuration.locked)} onChange={(e) => setEnabled(e.target.value === "true")}>
                <option value="true">on</option>
                <option value="false">off</option>
              </Select>
            )}
          </Field>
        </div>
        <Field label="Thresholds and parameters (JSON)" hint="Unknown keys are rejected by the server.">
          {(id) => <TextArea id={id} rows={6} className="font-mono text-xs" value={config} onChange={(e) => setConfig(e.target.value)} />}
        </Field>
        <Field label="Reason for change">
          {(id) => <TextArea id={id} rows={2} required minLength={5} value={reason} onChange={(e) => setReason(e.target.value)} />}
        </Field>
        <Notice tone="amber">Changes apply to batches ingested from now on. Existing results keep the rule version they ran with.</Notice>
        {parseError && <ErrorState error={new Error(parseError)} />}
        {save.isError && <ErrorState error={save.error} />}
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" busy={save.isPending}>
            Save
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
