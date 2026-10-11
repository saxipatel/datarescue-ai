import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "@/api/endpoints";
import { Button, Dialog, ErrorState, Field, Input, Notice, Select, TextArea } from "@/components/ui";
import type { RepairAction } from "@/types/api";

const ACTIONS: { value: RepairAction; help: string }[] = [
  { value: "SKIP_IDENTICAL_DUPLICATE", help: "Re-check each skipped duplicate's hash and confirm it. No business rows change." },
  { value: "NORMALIZE_CURRENCY_CASE", help: "Upper-case otherwise-valid currency codes and replay those rows." },
  { value: "REPLAY_QUARANTINED", help: "Re-validate quarantined rows under the current rules (after a rule or source fix)." },
  { value: "RETRY_MISSING_BATCH", help: "Request redelivery of a missing sequence with stable event IDs." },
  { value: "MANUAL_REVIEW_REQUIRED", help: "Keep rows quarantined and flag them for a person. Values are never guessed." },
];

export function ProposalDialog({
  open,
  onClose,
  incidentId,
  initialAction,
  initialCodes = [],
  initialIds = [],
}: {
  open: boolean;
  onClose: () => void;
  incidentId: string;
  initialAction?: RepairAction;
  initialCodes?: string[];
  initialIds?: string[];
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [action, setAction] = useState<RepairAction>(initialAction ?? "MANUAL_REVIEW_REQUIRED");
  const [justification, setJustification] = useState("");
  const [codes, setCodes] = useState(initialCodes.join(", "));

  useEffect(() => {
    if (!open) return;
    if (initialAction) setAction(initialAction);
    setCodes(initialCodes.join(", "));
    // Reset only when the dialog opens with new defaults.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const create = useMutation({
    mutationFn: () => {
      const target: Record<string, unknown> = {};
      const codeList = codes.split(",").map((c) => c.trim()).filter(Boolean);
      if (codeList.length) target.error_codes = codeList;
      if (initialIds.length) target.quarantine_ids = initialIds;
      return api.createProposal({ incident_id: incidentId, action, justification, target });
    },
    onSuccess: (proposal) => {
      queryClient.invalidateQueries({ queryKey: ["incident", incidentId] });
      queryClient.invalidateQueries({ queryKey: ["proposals"] });
      onClose();
      navigate(`/recovery?proposal=${proposal.id}`);
    },
  });

  const supportsFilters = action === "REPLAY_QUARANTINED" || action === "MANUAL_REVIEW_REQUIRED" || action === "NORMALIZE_CURRENCY_CASE";

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate();
  }

  return (
    <Dialog open={open} title="Propose a bounded repair" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Action">
          {(id) => (
            <Select id={id} value={action} onChange={(e) => setAction(e.target.value as RepairAction)}>
              {ACTIONS.map((a) => (
                <option key={a.value} value={a.value}>
                  {a.value}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <p className="text-xs text-slate-600 dark:text-slate-400">{ACTIONS.find((a) => a.value === action)?.help}</p>
        {initialIds.length > 0 && <Notice>Limited to {initialIds.length} selected quarantine record(s).</Notice>}
        {supportsFilters && (
          <Field label="Narrow by error codes (optional)" hint="Comma separated, e.g. CURRENCY_INVALID_FORMAT. Targets never extend beyond this incident's batch.">
            {(id) => <Input id={id} value={codes} onChange={(e) => setCodes(e.target.value)} />}
          </Field>
        )}
        <Field label="Justification" hint="Recorded in the audit log and shown to the reviewer.">
          {(id) => <TextArea id={id} rows={3} required minLength={10} value={justification} onChange={(e) => setJustification(e.target.value)} />}
        </Field>
        <Notice tone="amber">
          The server resolves targets, classifies risk and builds a preview. Nothing changes until a steward approves after a dry run.
        </Notice>
        {create.isError && <ErrorState error={create.error} />}
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" busy={create.isPending}>
            Create proposal
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
