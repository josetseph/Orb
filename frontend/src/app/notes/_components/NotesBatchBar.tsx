"use client";

import { Loader2, Trash2 } from "lucide-react";

type NotesBatchBarProps = {
  noteCount: number;
  selectedCount: number;
  batchDeleting: boolean;
  onToggleSelectAll: () => void;
  onBatchDelete: () => void;
};

export function NotesBatchBar({
  noteCount,
  selectedCount,
  batchDeleting,
  onToggleSelectAll,
  onBatchDelete,
}: NotesBatchBarProps) {
  if (noteCount === 0) return null;

  return (
    <div className="flex items-center gap-1.5 px-3 pb-2">
      <button type="button" onClick={onToggleSelectAll} className="btn btn-sm btn-secondary">
        {selectedCount === noteCount ? "Clear selection" : "Select all"}
      </button>
      {selectedCount > 0 && (
        <button
          type="button"
          disabled={batchDeleting}
          onClick={() => void onBatchDelete()}
          className="btn btn-sm btn-danger"
        >
          {batchDeleting ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Trash2 className="h-3 w-3" />
          )}
          Delete {selectedCount}
        </button>
      )}
    </div>
  );
}
