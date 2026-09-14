"use client";

import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Note } from "@/lib/types";
import {
  getProcessingLabel,
  getProcessingStage,
  isActiveProcessingNote,
  isPendingReingestNote,
} from "../_lib/processing-status";

type Status = {
  key: "ingested" | "failed" | "ingesting" | "pending" | "saved";
  label: string;
  /** Tailwind classes for the dot. */
  dot: string;
  /** Tailwind classes for the label text. */
  text: string;
};

/** One source of truth for a note's status colour + wording. */
export function noteStatus(note: Note): Status {
  if (note.processed) {
    return { key: "ingested", label: "In graph", dot: "bg-accent", text: "text-n-400" };
  }
  if (note.failed) {
    return { key: "failed", label: "Failed", dot: "bg-danger", text: "text-danger-text" };
  }
  if (isActiveProcessingNote(note)) {
    return {
      key: "ingesting",
      label: getProcessingStage(note),
      dot: "bg-accent-300 animate-pulse",
      text: "text-accent-300",
    };
  }
  if (isPendingReingestNote(note)) {
    return { key: "pending", label: "Needs re-ingest", dot: "bg-n-500", text: "text-n-500" };
  }
  return { key: "saved", label: "Saved · not in graph yet", dot: "bg-n-700", text: "text-n-500" };
}

type NoteStatusBadgeProps = {
  note: Note;
  /** Shows an × on the Failed badge. Omit in lists where it would be noise. */
  onDismissFailure?: () => void;
};

export function NoteStatusBadge({ note, onDismissFailure }: NoteStatusBadgeProps) {
  const s = noteStatus(note);
  return (
    <span
      className={cn("inline-flex max-w-full items-center gap-1.5 text-[12px]", s.text)}
      title={s.key === "ingesting" ? getProcessingLabel(note) : undefined}
    >
      {s.key === "ingesting" ? (
        <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
      ) : (
        <span className={cn("dot h-1.5 w-1.5", s.dot)} />
      )}
      <span className="truncate">{s.label}</span>
      {s.key === "failed" && onDismissFailure && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onDismissFailure();
          }}
          title="Clear this — the note stays un-ingested"
          className="rounded px-1 text-n-500 hover:bg-n-900 hover:text-text"
        >
          ×
        </button>
      )}
    </span>
  );
}

/** Compact status dot for sidebar note rows. */
export function NoteStatusDot({ note }: NoteStatusBadgeProps) {
  const s = noteStatus(note);
  return <span className={cn("dot", s.dot)} title={s.label} />;
}
