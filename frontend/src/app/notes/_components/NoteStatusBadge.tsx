"use client";

import { Loader2 } from "lucide-react";
import type { Note } from "@/lib/types";
import {
  getProcessingLabel,
  getProcessingStage,
  isActiveProcessingNote,
  isPendingReingestNote,
} from "../_lib/processing-status";

type NoteStatusBadgeProps = {
  note: Note;
  /** Shows an × on the Failed badge. Omit in lists where it would be noise. */
  onDismissFailure?: () => void;
};

export function NoteStatusBadge({ note, onDismissFailure }: NoteStatusBadgeProps) {
  if (note.processed) {
    return (
      <span className="inline-flex max-w-full items-center gap-1 truncate rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs font-medium text-emerald-400">
        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400" />
        Ingested
      </span>
    );
  }
  if (note.failed) {
    return (
      <span className="inline-flex max-w-full items-center gap-1 truncate rounded-full bg-red-500/15 px-2 py-0.5 text-xs font-medium text-red-400">
        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-red-400" />
        Failed
        {onDismissFailure && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onDismissFailure();
            }}
            title="Clear this — the note stays un-ingested"
            className="-mr-0.5 ml-0.5 rounded-full px-1 text-red-300/70 transition hover:bg-red-500/20 hover:text-red-200"
          >
            ×
          </button>
        )}
      </span>
    );
  }
  if (isActiveProcessingNote(note)) {
    return (
      <span
        className="inline-flex max-w-full items-center gap-1 truncate rounded-full bg-amber-500/15 px-2 py-0.5 text-xs font-medium text-amber-400"
        title={getProcessingLabel(note)}
      >
        <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
        <span className="truncate">{getProcessingStage(note)}</span>
      </span>
    );
  }
  if (isPendingReingestNote(note)) {
    return (
      <span className="inline-flex max-w-full items-center gap-1 truncate rounded-full bg-orange-500/15 px-2 py-0.5 text-xs font-medium text-orange-300/90">
        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-orange-400" />
        Needs re-ingest
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-white/8 px-2 py-0.5 text-xs font-medium text-white/40">
      <span className="h-1.5 w-1.5 rounded-full bg-white/30" />
      Saved
    </span>
  );
}

/** Compact status dot for sidebar note rows. */
export function NoteStatusDot({ note }: NoteStatusBadgeProps) {
  if (note.processed) {
    return (
      <span
        className="block h-1.5 w-1.5 rounded-full bg-emerald-400"
        title="Ingested"
      />
    );
  }
  if (note.failed) {
    return (
      <span
        className="block h-1.5 w-1.5 rounded-full bg-red-400"
        title="Failed"
      />
    );
  }
  if (isActiveProcessingNote(note)) {
    return (
      <span
        className="block h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400"
        title={getProcessingLabel(note)}
      />
    );
  }
  return (
    <span
      className="block h-1.5 w-1.5 rounded-full bg-white/20"
      title="Saved"
    />
  );
}
