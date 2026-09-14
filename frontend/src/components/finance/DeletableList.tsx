import { Trash2 } from "lucide-react";
import { DELETE_BTN, EMPTY_ROW, LIST_ROW } from "./utils";

export function DeletableList({
  rows,
  empty,
  busy,
  onDelete,
}: {
  rows: Array<{ id: string; title: string; subtitle?: string }>;
  empty: string;
  busy?: boolean;
  onDelete: (id: string) => void;
}) {
  return (
    <ul>
      {rows.map((row) => (
        <li key={row.id} className={LIST_ROW}>
          <div className="min-w-0 flex-1">
            <div className="truncate">{row.title}</div>
            {row.subtitle ? <div className="text-[11px] text-n-500">{row.subtitle}</div> : null}
          </div>
          <button
            type="button"
            disabled={busy}
            onClick={() => onDelete(row.id)}
            className={DELETE_BTN}
            aria-label={`Delete ${row.title}`}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </li>
      ))}
      {rows.length === 0 && <li className={EMPTY_ROW}>{empty}</li>}
    </ul>
  );
}
