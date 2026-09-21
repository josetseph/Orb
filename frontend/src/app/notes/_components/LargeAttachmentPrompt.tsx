import { useState } from "react";
import { FileText, Loader2 } from "lucide-react";
import { cn, errMessage } from "@/lib/utils";
import type { AttachmentMode, PendingAttachment } from "../_lib/large-attachments";

const OPTIONS: Array<{ mode: AttachmentMode; label: string; hint: string }> = [
  { mode: "graph", label: "Graph the whole document", hint: "Every entity and link. Slow for long files." },
  { mode: "summary", label: "Summarize instead", hint: "Graph a few pages of summary." },
  { mode: "index", label: "Index for search", hint: "Searchable and usable in Ask, nothing added to the graph." },
];

/** Asks how to ingest attachments too large to graph without a decision. */
export function LargeAttachmentPrompt({
  pending,
  onChoose,
}: {
  pending: PendingAttachment[];
  onChoose: (link: string, mode: AttachmentMode) => Promise<void>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (pending.length === 0) return null;

  const choose = async (link: string, mode: AttachmentMode) => {
    setBusy(link);
    setError(null);
    try {
      await onChoose(link, mode);
    } catch (e) {
      setError(errMessage(e, "Could not save that choice."));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mb-5 space-y-2">
      {pending.map((att) => (
        <div key={att.link} className="rounded-md bg-surface px-3.5 py-3 shadow-sm">
          <div className="flex items-center gap-2 text-[13px]">
            <FileText className="h-3.5 w-3.5 shrink-0 text-accent-300" />
            <span className="min-w-0 truncate">{att.name}</span>
            <span className="shrink-0 text-[11.5px] text-n-500">
              about {Math.round(att.tokens / 1000)}k tokens · the rest of this note is already in the graph
            </span>
          </div>
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            {OPTIONS.map((opt, i) => (
              <button
                key={opt.mode}
                type="button"
                title={opt.hint}
                disabled={busy !== null}
                onClick={() => void choose(att.link, opt.mode)}
                className={cn("btn btn-sm", i === 0 ? "btn-primary" : "btn-secondary")}
              >
                {opt.label}
              </button>
            ))}
            {busy === att.link && <Loader2 className="h-3.5 w-3.5 animate-spin text-n-500" />}
          </div>
        </div>
      ))}
      {error && <p className="text-[11.5px] text-danger-text">{error}</p>}
    </div>
  );
}
