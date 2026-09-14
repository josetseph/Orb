import type { FinanceAccount } from "@/lib/types";
import { EMPTY_ROW, LIST_ROW, money } from "./utils";

export function AccountList({
  accounts,
  currency,
  empty,
}: {
  accounts: FinanceAccount[];
  currency?: string;
  empty: string;
}) {
  return (
    <ul>
      {accounts.map((account) => (
        <li key={account.id} className={LIST_ROW}>
          <div className="min-w-0 flex-1">
            <div className="truncate">{account.name}</div>
            <span className="tag tag-neutral mt-0.5">{account.account_type}</span>
          </div>
          <div className="tabular-nums text-accent-300">
            {money(account.balance, currency || account.currency)}
          </div>
        </li>
      ))}
      {accounts.length === 0 && <li className={EMPTY_ROW}>{empty}</li>}
    </ul>
  );
}
