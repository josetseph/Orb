import { LIST_ROW } from "./utils";
export function BasicSummaryList({ basic }: { basic: Record<string, unknown> }) {
  const entries = Object.entries(basic || {});
  if (!entries.length) {
    return <p className="text-[12.5px] text-n-500">No summary data for this range.</p>;
  }
  return (
    <ul>
      {entries.map(([key, value]) => {
        const row = value && typeof value === "object" ? (value as Record<string, unknown>) : null;
        const label = String(row?.title || row?.monetary_value || key);
        const amount =
          row?.value_parsed ??
          row?.value ??
          (typeof value === "number" || typeof value === "string" ? value : null);
        return (
          <li
            key={key}
            className={LIST_ROW}
          >
            <span className="truncate text-n-300">{label}</span>
            <span className="tabular-nums text-accent-300">
              {amount == null ? "—" : String(amount)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
