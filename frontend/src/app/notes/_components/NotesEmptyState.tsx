"use client";

import { FileText, NotebookPen } from "lucide-react";

type NotesEmptyStateProps = {
  variant: "sidebar" | "editor";
  searchQuery?: string;
  isSaving?: boolean;
  onCreateNote?: () => void;
};

export function NotesEmptyState({
  variant,
  searchQuery,
  isSaving,
  onCreateNote,
}: NotesEmptyStateProps) {
  if (variant === "sidebar") {
    return (
      <div className="flex flex-col items-center justify-center px-4 py-12 text-center">
        <FileText className="mb-2 h-7 w-7 text-n-700" />
        <p className="text-[12.5px] text-n-500">
          {searchQuery ? "No notes match" : "No notes yet"}
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col items-center justify-center gap-2.5 text-center">
      <NotebookPen className="h-[34px] w-[34px] text-n-700" />
      <p className="text-[14px] text-n-300">No note selected</p>
      <p className="text-[12px] text-n-500">
        Pick one on the left, or press <kbd className="font-mono">⌘N</kbd>
      </p>
      <button onClick={onCreateNote} disabled={isSaving} className="btn btn-primary mt-2">
        New note
      </button>
    </div>
  );
}
