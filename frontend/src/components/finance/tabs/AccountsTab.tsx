import type { FormEvent } from "react";
import { Landmark, Plus } from "lucide-react";
import type { FinanceAccount, FinanceWorkspace } from "@/lib/types";
import { AccountList } from "../AccountList";
import { Field } from "../Field";
import { Panel } from "../Panel";
import { FIELD_INPUT } from "../utils";

export function AccountsTab({
  accounts,
  workspace,
  accountForm,
  setAccountForm,
  onCreate,
  busy,
}: {
  accounts: FinanceAccount[];
  workspace: FinanceWorkspace;
  accountForm: { name: string; account_type: string; opening_balance: string };
  setAccountForm: React.Dispatch<
    React.SetStateAction<{ name: string; account_type: string; opening_balance: string }>
  >;
  onCreate: (e: FormEvent) => void;
  busy: boolean;
}) {
  return (
    <section className="grid gap-4 xl:grid-cols-[0.9fr,1.1fr]">
      <form
        onSubmit={onCreate}
        className="card-outline space-y-3"
      >
        <h2 className="kicker flex items-center gap-1.5 text-[11px]">
          <Plus className="h-3 w-3" /> New account
        </h2>
        <Field label="Name">
          <input
            required
            value={accountForm.name}
            onChange={(e) => setAccountForm((p) => ({ ...p, name: e.target.value }))}
            className={FIELD_INPUT}
            placeholder="Checking"
          />
        </Field>
        <Field label="Type">
          <select
            value={accountForm.account_type}
            onChange={(e) =>
              setAccountForm((p) => ({ ...p, account_type: e.target.value }))
            }
            className={FIELD_INPUT}
          >
            <option value="asset">Asset</option>
            <option value="expense">Expense</option>
            <option value="revenue">Revenue</option>
            <option value="liability">Liability</option>
            <option value="cash">Cash</option>
          </select>
        </Field>
        {(accountForm.account_type === "asset" ||
          accountForm.account_type === "liability") && (
          <Field label="Opening balance">
            <input
              type="number"
              step="0.01"
              value={accountForm.opening_balance}
              onChange={(e) =>
                setAccountForm((p) => ({ ...p, opening_balance: e.target.value }))
              }
              className={FIELD_INPUT}
              placeholder="0.00"
            />
          </Field>
        )}
        <button
          type="submit"
          disabled={busy}
          className="btn btn-primary"
        >
          {busy ? "Creating…" : "Create account"}
        </button>
      </form>
      <Panel title="All accounts" icon={<Landmark className="h-3.5 w-3.5" />}>
        <AccountList
          accounts={accounts}
          currency={workspace.currency}
          empty="No accounts yet."
        />
      </Panel>
    </section>
  );
}
