import type { FormEvent } from "react";
import { Plus } from "lucide-react";
import type { FinanceBudget, FinanceWorkspace } from "@/lib/types";
import { Field } from "../Field";
import { Panel } from "../Panel";
import { EMPTY_ROW, LIST_ROW, money } from "../utils";

export function BudgetsTab({
  budgets,
  workspace,
  budgetForm,
  setBudgetForm,
  onCreate,
  busy,
}: {
  budgets: FinanceBudget[];
  workspace: FinanceWorkspace;
  budgetForm: { name: string; amount: string };
  setBudgetForm: React.Dispatch<React.SetStateAction<{ name: string; amount: string }>>;
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
          <Plus className="h-3 w-3" /> New budget
        </h2>
        <Field label="Name">
          <input
            required
            value={budgetForm.name}
            onChange={(e) => setBudgetForm((p) => ({ ...p, name: e.target.value }))}
            className="input"
            placeholder="Groceries"
          />
        </Field>
        <Field label="Monthly amount (optional)">
          <input
            type="number"
            min="0.01"
            step="0.01"
            value={budgetForm.amount}
            onChange={(e) => setBudgetForm((p) => ({ ...p, amount: e.target.value }))}
            className="input"
            placeholder="500"
          />
        </Field>
        <button
          type="submit"
          disabled={busy}
          className="btn btn-primary"
        >
          {busy ? "Creating…" : "Create budget"}
        </button>
      </form>
      <Panel title="Budgets">
        <ul>
          {budgets.map((budget) => (
            <li
              key={budget.id}
              className={LIST_ROW}
            >
              <div>
                <div>{budget.name}</div>
                <div className="text-[11px] text-n-500">
                  {budget.auto_budget_amount
                    ? `${money(budget.auto_budget_amount, workspace.currency)} / ${budget.auto_budget_period || "month"}`
                    : "No auto amount"}
                </div>
              </div>
              <div className="tabular-nums text-n-300">
                spent {money(budget.spent, budget.currency || workspace.currency)}
              </div>
            </li>
          ))}
          {budgets.length === 0 && (
            <li className={EMPTY_ROW}>
              No budgets yet.
            </li>
          )}
        </ul>
      </Panel>
    </section>
  );
}
