import type { FormEvent } from "react";
import { Plus } from "lucide-react";
import type {
  FinanceAccount,
  FinanceBudget,
  FinanceCategory,
  FinanceTransaction,
  FinanceWorkspace,
} from "@/lib/types";
import { Field } from "../Field";
import { Panel } from "../Panel";
import { SuggestInput } from "../SuggestInput";
import { TransactionList } from "../TransactionList";
import { FIELD_INPUT } from "../utils";

type TxForm = {
  type: string;
  description: string;
  amount: string;
  account_id: string;
  transfer_account_id: string;
  counterparty_name: string;
  category: string;
  budget_id: string;
  date: string;
};

export function TransactionsTab({
  transactions,
  accounts,
  categories,
  budgets,
  assetAccounts,
  expenseAccounts,
  revenueAccounts,
  workspace,
  txForm,
  setTxForm,
  onCreate,
  onDelete,
  busy,
}: {
  transactions: FinanceTransaction[];
  accounts: FinanceAccount[];
  categories: FinanceCategory[];
  budgets: FinanceBudget[];
  assetAccounts: FinanceAccount[];
  expenseAccounts: FinanceAccount[];
  revenueAccounts: FinanceAccount[];
  workspace: FinanceWorkspace;
  txForm: TxForm;
  setTxForm: React.Dispatch<React.SetStateAction<TxForm>>;
  onCreate: (e: FormEvent) => void;
  onDelete: (id: string) => void;
  busy: boolean;
}) {
  return (
    <section className="grid gap-4 xl:grid-cols-[0.95fr,1.05fr]">
      <form
        onSubmit={onCreate}
        className="card-outline space-y-3"
      >
        <h2 className="kicker flex items-center gap-1.5 text-[11px]">
          <Plus className="h-3 w-3" /> New transaction
        </h2>
        <Field label="Type">
          <select
            value={txForm.type}
            onChange={(e) => setTxForm((p) => ({ ...p, type: e.target.value }))}
            className={FIELD_INPUT}
          >
            <option value="withdrawal">Expense (withdrawal)</option>
            <option value="deposit">Income (deposit)</option>
            <option value="transfer">Transfer</option>
          </select>
        </Field>
        <Field label="Description">
          <input
            required
            value={txForm.description}
            onChange={(e) => setTxForm((p) => ({ ...p, description: e.target.value }))}
            className={FIELD_INPUT}
            placeholder="Groceries"
          />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Amount">
            <input
              required
              type="number"
              min="0.01"
              step="0.01"
              value={txForm.amount}
              onChange={(e) => setTxForm((p) => ({ ...p, amount: e.target.value }))}
              className={FIELD_INPUT}
            />
          </Field>
          <Field label="Date">
            <input
              type="date"
              value={txForm.date}
              onChange={(e) => setTxForm((p) => ({ ...p, date: e.target.value }))}
              className={FIELD_INPUT}
            />
          </Field>
        </div>
        <Field label={txForm.type === "deposit" ? "To account" : "From account"}>
          <select
            required
            value={txForm.account_id}
            onChange={(e) => setTxForm((p) => ({ ...p, account_id: e.target.value }))}
            className={FIELD_INPUT}
          >
            <option value="">Select account</option>
            {(txForm.type === "transfer" || txForm.type === "withdrawal"
              ? assetAccounts
              : assetAccounts
            ).map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} ({a.account_type})
              </option>
            ))}
          </select>
        </Field>
        {txForm.type === "transfer" ? (
          <Field label="To account">
            <select
              required
              value={txForm.transfer_account_id}
              onChange={(e) =>
                setTxForm((p) => ({ ...p, transfer_account_id: e.target.value }))
              }
              className={FIELD_INPUT}
            >
              <option value="">Select destination</option>
              {assetAccounts
                .filter((a) => a.id !== txForm.account_id)
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
            </select>
          </Field>
        ) : (
          <Field
            label={
              txForm.type === "withdrawal" ? "Payee / expense account" : "Payer / income source"
            }
          >
            <SuggestInput
              value={txForm.counterparty_name}
              onChange={(value) =>
                setTxForm((p) => ({ ...p, counterparty_name: value }))
              }
              suggestions={(txForm.type === "withdrawal"
                ? expenseAccounts
                : revenueAccounts
              ).map((a) => a.name)}
              placeholder={txForm.type === "withdrawal" ? "Shop name" : "Employer"}
            />
          </Field>
        )}
        <Field label="Category (optional)">
          <SuggestInput
            value={txForm.category}
            onChange={(value) => setTxForm((p) => ({ ...p, category: value }))}
            suggestions={categories.map((c) => c.name)}
            placeholder="Food"
          />
        </Field>
        {txForm.type === "withdrawal" && (
          <Field label="Budget (optional)">
            <select
              value={txForm.budget_id}
              onChange={(e) => setTxForm((p) => ({ ...p, budget_id: e.target.value }))}
              className={FIELD_INPUT}
            >
              <option value="">No budget</option>
              {budgets.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </Field>
        )}
        <button
          type="submit"
          disabled={busy || !txForm.account_id}
          className="btn btn-primary"
        >
          {busy ? "Saving…" : "Add transaction"}
        </button>
        {accounts.length === 0 && (
          <p className="text-[11.5px] text-n-500">
            Create an asset account first before adding transactions.
          </p>
        )}
      </form>
      <Panel title="Transactions">
        <TransactionList
          rows={transactions}
          currency={workspace.currency}
          onDelete={onDelete}
          busy={busy}
        />
      </Panel>
    </section>
  );
}
