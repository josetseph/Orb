import type { TabId } from "./utils";
import { cn } from "@/lib/utils";

const TABS: { id: TabId; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "accounts", label: "Accounts" },
  { id: "transactions", label: "Transactions" },
  { id: "budgets", label: "Budgets" },
  { id: "categories", label: "Categories" },
  { id: "recurring", label: "Recurring" },
  { id: "rules", label: "Rules" },
  { id: "search", label: "Search" },
  { id: "reports", label: "Reports" },
];

export function FinanceTabs({
  tab,
  onSelect,
}: {
  tab: TabId;
  onSelect: (id: TabId) => void;
}) {
  return (
    <div className="seg flex-wrap">
      {TABS.map((item) => (
        <button
          key={item.id}
          type="button"
          onClick={() => onSelect(item.id)}
          className={cn("seg-opt", tab === item.id && "seg-opt-active")}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
