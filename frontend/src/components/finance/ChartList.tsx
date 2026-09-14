import { LIST_ROW } from "./utils";
export function ChartList({ data }: { data: unknown }) {
  const rows = Array.isArray(data)
    ? data
    : data && typeof data === "object" && Array.isArray((data as { data?: unknown }).data)
      ? ((data as { data: unknown[] }).data)
      : [];
  if (!rows.length) {
    return <p className="text-[12.5px] text-n-500">No chart data for this range.</p>;
  }
  return (
    <ul>
      {rows.map((item, idx) => {
        const row = item && typeof item === "object" ? (item as Record<string, unknown>) : {};
        const label = String(row.label || row.key || row.name || `Series ${idx + 1}`);
        const entries = Array.isArray(row.entries) ? row.entries : null;
        const value =
          row.y ??
          row.value ??
          (entries
            ? entries.reduce(
                (sum: number, entry: unknown) =>
                  sum +
                  Number(
                    (entry && typeof entry === "object"
                      ? (entry as { y?: unknown }).y
                      : 0) || 0,
                  ),
                0,
              )
            : null);
        return (
          <li
            key={`${label}-${idx}`}
            className={LIST_ROW}
          >
            <span className="truncate text-n-300">{label}</span>
            <span className="tabular-nums text-accent-300">
              {value == null ? "—" : Number(value).toFixed(2)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
