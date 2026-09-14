import type { FormEvent } from "react";
import { RefreshCw, Trash2 } from "lucide-react";
import type { FinanceWorkspace } from "@/lib/types";

export function FinanceWorkspaceBar({
  workspace,
  currentKB,
  currency,
  onCurrencyChange,
  onSubmit,
  onRefresh,
  onReset,
  busy,
}: {
  workspace: FinanceWorkspace;
  currentKB: string;
  currency: string;
  onCurrencyChange: (value: string) => void;
  onSubmit: (e: FormEvent) => void;
  onRefresh: () => void;
  onReset: () => void;
  busy: boolean;
}) {
  return (
    <div className="card-outline flex flex-wrap items-center gap-3 px-3.5 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="text-[13px]">
          {workspace.administration_title || `${currentKB} finance`}
        </div>
        <div className="text-[11.5px] text-n-500">
          Primary currency{" "}
          <span className="font-mono text-accent-300">{workspace.currency || "—"}</span>
        </div>
      </div>
      <form onSubmit={onSubmit} className="flex items-center gap-2">
        <input
          value={currency}
          onChange={(e) => onCurrencyChange(e.target.value.toUpperCase().slice(0, 3))}
          className="input input-mono w-20 uppercase"
          maxLength={3}
          required
          aria-label="Change currency"
        />
        <button type="submit" disabled={busy} className="btn btn-secondary btn-sm">
          {busy ? "Saving…" : "Update"}
        </button>
      </form>
      <button type="button" onClick={onRefresh} className="btn btn-secondary btn-sm">
        <RefreshCw className="h-3.5 w-3.5" /> Refresh
      </button>
      <button type="button" disabled={busy} onClick={onReset} className="btn btn-danger btn-sm">
        <Trash2 className="h-3.5 w-3.5" /> Clear finance data
      </button>
    </div>
  );
}
