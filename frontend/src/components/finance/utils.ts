/** Empty-state row shared by every list on the Finance screen. */
export const EMPTY_ROW =
  "rounded-md border border-dashed border-n-800 px-3 py-5 text-[12.5px] text-n-500";

/** One list row: a hairline under each, none under the last. */
export const LIST_ROW =
  "flex items-center gap-3 border-b border-n-900 px-2 py-2 text-[13px] last:border-0";

/** Icon button that deletes a row. */
export const DELETE_BTN =
  "rounded p-1 text-n-500 hover:text-danger-text disabled:opacity-50";

export type TabId =
  | "overview"
  | "accounts"
  | "transactions"
  | "budgets"
  | "categories"
  | "recurring"
  | "rules"
  | "search"
  | "reports";

export function tomorrowIso() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10);
}

export function money(value: number, currency?: string | null) {
  const amount = Number.isFinite(value) ? value.toFixed(2) : "0.00";
  return currency ? `${amount} ${currency}` : amount;
}

export function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

export function monthStartIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
}
