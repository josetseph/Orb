import {
  ArrowDownLeft,
  ArrowRightLeft,
  ArrowUpRight,
  Landmark,
  Wallet,
} from "lucide-react";
import type { FinanceSummary, FinanceWorkspace } from "@/lib/types";
import { AccountList } from "../AccountList";
import { MetricCard } from "../MetricCard";
import { Panel } from "../Panel";
import { TransactionList } from "../TransactionList";

export function OverviewTab({
  summary,
  workspace,
  busy,
  onDeleteTransaction,
}: {
  summary: FinanceSummary;
  workspace: FinanceWorkspace;
  busy: boolean;
  onDeleteTransaction: (id: string) => void;
}) {
  return (
    <section className="space-y-4">
      <div className="grid max-w-[1100px] grid-cols-2 gap-3 xl:grid-cols-4">
        <MetricCard
          icon={<Wallet className="h-3 w-3" />}
          label="Tracked balance"
          value={summary.asset_balance}
          currency={workspace.currency}
        />
        <MetricCard
          icon={<ArrowUpRight className="h-3 w-3" />}
          label={`Income (${summary.days}d)`}
          value={summary.income_total}
          currency={workspace.currency}
        />
        <MetricCard
          icon={<ArrowDownLeft className="h-3 w-3" />}
          label={`Expenses (${summary.days}d)`}
          value={summary.expense_total}
          currency={workspace.currency}
        />
        <MetricCard
          icon={<ArrowRightLeft className="h-3 w-3" />}
          label="Net flow"
          value={summary.net_flow}
          currency={workspace.currency}
        />
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Accounts" icon={<Landmark className="h-3.5 w-3.5" />}>
          <AccountList
            accounts={summary.accounts}
            currency={workspace.currency}
            empty="No accounts yet — create one in the Accounts tab."
          />
        </Panel>
        <Panel title="Recent transactions">
          <TransactionList
            rows={summary.recent_transactions}
            currency={workspace.currency}
            onDelete={onDeleteTransaction}
            busy={busy}
          />
        </Panel>
      </div>
    </section>
  );
}
