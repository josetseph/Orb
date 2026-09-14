import type { FormEvent } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import type { FinanceWorkspace } from "@/lib/types";
import { cn } from "@/lib/utils";

export function FinanceNotReady({
  workspace,
  currency,
  onCurrencyChange,
  onSubmit,
  onRefresh,
  busy,
}: {
  workspace: FinanceWorkspace | null;
  currency: string;
  onCurrencyChange: (value: string) => void;
  onSubmit: (e: FormEvent) => void;
  onRefresh: () => void;
  busy: boolean;
}) {
  const mismatch = workspace?.status === "auth_mismatch";
  return (
    <form
      onSubmit={onSubmit}
      className={cn(
        "card-outline max-w-[560px] space-y-4 p-5",
        mismatch && "border border-danger/40",
      )}
    >
      <div className="flex items-start gap-3">
        <AlertTriangle
          className={cn(
            "mt-0.5 h-[18px] w-[18px] shrink-0",
            mismatch ? "text-danger-text" : "text-accent-300",
          )}
        />
        <div>
          <h2 className="text-[15px] font-medium">
            {workspace?.status === "starting" && "Firefly is starting"}
            {workspace?.status === "bootstrapping" && "Finishing first-time setup"}
            {mismatch && "Finance auth needs attention"}
            {!workspace?.status && "Finance is not ready yet"}
          </h2>
          <p className="mt-1 text-[12.5px] text-n-500">
            {workspace?.detail ||
              "Orb is still preparing the embedded Firefly III runtime and API access."}
          </p>
        </div>
      </div>
      <p className="text-[12.5px] text-n-500">
        Set the primary currency to finish setup. You can manage accounts, transactions,
        budgets, and reports entirely inside Orb.
      </p>
      <div className="flex gap-2">
        <input
          value={currency}
          onChange={(e) => onCurrencyChange(e.target.value.toUpperCase().slice(0, 3))}
          className="input input-mono w-20 uppercase"
          maxLength={3}
          required
          aria-label="Primary currency"
        />
        <button type="submit" disabled={busy} className="btn btn-primary">
          {busy ? "Saving…" : "Set primary currency"}
        </button>
        <button type="button" onClick={onRefresh} className="btn btn-secondary">
          <RefreshCw className="h-3.5 w-3.5" /> Refresh
        </button>
      </div>
    </form>
  );
}
