import { Trash2 } from "lucide-react";
import type { FinanceTransaction } from "@/lib/types";
import { DELETE_BTN, EMPTY_ROW, LIST_ROW, money } from "./utils";

export function TransactionList({
  rows,
  currency,
  onDelete,
  busy,
}: {
  rows: FinanceTransaction[];
  currency?: string;
  onDelete?: (id: string) => void;
  busy?: boolean;
}) {
  return (
    <ul>
      {rows.map((tx) => (
        <li key={tx.id} className={LIST_ROW}>
          <span className="w-16 shrink-0 whitespace-nowrap text-[12px] text-n-400">
            {tx.date ? new Date(tx.date).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "—"}
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate">{tx.description || "(no description)"}</div>
            <div className="truncate text-[11px] text-n-500">
              {tx.account_name || ""}
              {tx.counterparty_name ? ` → ${tx.counterparty_name}` : ""}
            </div>
          </div>
          {tx.category && <span className="tag tag-neutral">{tx.category}</span>}
          <div
            className={
              tx.type === "deposit"
                ? "tabular-nums text-accent-300"
                : tx.type === "withdrawal"
                  ? "tabular-nums text-text"
                  : "tabular-nums text-n-300"
            }
          >
            {money(tx.amount, tx.currency_code || currency)}
          </div>
          {onDelete && (
            <button
              type="button"
              disabled={busy}
              onClick={() => onDelete(tx.group_id || tx.id)}
              className={DELETE_BTN}
              aria-label="Delete transaction"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          )}
        </li>
      ))}
      {rows.length === 0 && <li className={EMPTY_ROW}>No transactions yet.</li>}
    </ul>
  );
}
