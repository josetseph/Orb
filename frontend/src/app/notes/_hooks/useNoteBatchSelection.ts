import {
  useCallback,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from "react";
import { api } from "@/lib/api";
import type { Note } from "@/lib/types";
import type { ProcessedFilter } from "../_lib/types";

type UseNoteBatchSelectionArgs = {
  currentKB: string;
  notes: Note[];
  selectedNote: Note | null;
  setSelectedNote: Dispatch<SetStateAction<Note | null>>;
  contentBeforeEditRef: MutableRefObject<string>;
  autoSaveTimeoutRef: MutableRefObject<NodeJS.Timeout | undefined>;
  fetchNotes: (search?: string, filter?: ProcessedFilter) => Promise<void>;
  searchQuery: string;
  processedFilter: ProcessedFilter;
};

export function useNoteBatchSelection({
  currentKB,
  notes,
  selectedNote,
  setSelectedNote,
  contentBeforeEditRef,
  autoSaveTimeoutRef,
  fetchNotes,
  searchQuery,
  processedFilter,
}: UseNoteBatchSelectionArgs) {
  const [selectedNoteIds, setSelectedNoteIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [batchDeleting, setBatchDeleting] = useState(false);

  const toggleNoteSelected = useCallback((noteId: string) => {
    setSelectedNoteIds((prev) => {
      const next = new Set(prev);
      if (next.has(noteId)) next.delete(noteId);
      else next.add(noteId);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(() => {
    setSelectedNoteIds((prev) => {
      if (prev.size === notes.length) {
        return new Set();
      }
      return new Set(notes.map((n) => n.id));
    });
  }, [notes]);

  const handleBatchDeleteNotes = useCallback(async () => {
    const ids = Array.from(selectedNoteIds);
    if (ids.length === 0) return;
    if (
      !window.confirm(
        `Delete ${ids.length} selected note${ids.length === 1 ? "" : "s"}?\n\n` +
          `This removes them from Orb and deletes the markdown files in your vault.`,
      )
    ) {
      return;
    }
    setBatchDeleting(true);
    try {
      if (selectedNote && selectedNoteIds.has(selectedNote.id)) {
        if (autoSaveTimeoutRef.current) {
          clearTimeout(autoSaveTimeoutRef.current);
          autoSaveTimeoutRef.current = undefined;
        }
        contentBeforeEditRef.current = "";
        setSelectedNote(null);
      }
      const result = await api.batchDeleteNotes(ids, currentKB);
      setSelectedNoteIds(new Set());
      await fetchNotes(searchQuery, processedFilter);
      if (result.failed_count > 0) {
        alert(
          `Deleted ${result.deleted_count} note(s). ${result.failed_count} failed — check the vault and try again.`,
        );
      }
    } catch (error) {
      console.error("Batch delete failed:", error);
      alert("Batch delete failed. Refresh and try again.");
      try {
        await fetchNotes(searchQuery, processedFilter);
      } catch {
        /* ignore */
      }
    } finally {
      setBatchDeleting(false);
    }
  }, [
    selectedNoteIds,
    selectedNote,
    autoSaveTimeoutRef,
    contentBeforeEditRef,
    setSelectedNote,
    currentKB,
    fetchNotes,
    searchQuery,
    processedFilter,
  ]);

  return {
    selectedNoteIds,
    setSelectedNoteIds,
    batchDeleting,
    toggleNoteSelected,
    toggleSelectAll,
    handleBatchDeleteNotes,
  };
}
