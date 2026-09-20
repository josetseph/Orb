import {
  useCallback,
  useEffect,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from "react";
import { api } from "@/lib/api";
import type { Note } from "@/lib/types";

type UseNoteIngestArgs = {
  currentKB: string;
  selectedNote: Note | null;
  contentBeforeEditRef: MutableRefObject<string>;
  titleBeforeEditRef: MutableRefObject<string>;
  handleSaveNote: (note: Note) => Promise<void>;
  setSelectedNote: Dispatch<SetStateAction<Note | null>>;
  setNotes: Dispatch<SetStateAction<Note[]>>;
  setIsSaving: Dispatch<SetStateAction<boolean>>;
};

export function useNoteIngest({
  currentKB,
  selectedNote,
  contentBeforeEditRef,
  titleBeforeEditRef,
  handleSaveNote,
  setSelectedNote,
  setNotes,
  setIsSaving,
}: UseNoteIngestArgs) {
  const [ingestingNoteIds, setIngestingNoteIds] = useState<Set<string>>(
    () => new Set(),
  );

  /** Clear a failed flag on a note that will not be ingested. */
  const handleDismissFailure = useCallback(async () => {
    if (!selectedNote?.failed) return;
    const id = selectedNote.id;
    const cleared = { failed: false, processing_stage: "Saved" };
    // Optimistic: the badge is the whole point, so it should go at once.
    setSelectedNote((prev) => (prev && prev.id === id ? { ...prev, ...cleared } : prev));
    setNotes((prev) => prev.map((n) => (n.id === id ? { ...n, ...cleared } : n)));
    try {
      await api.dismissNoteFailure(id, currentKB);
    } catch (error) {
      console.error("Could not clear the failed status:", error);
      setSelectedNote((prev) =>
        prev && prev.id === id ? { ...prev, failed: true } : prev,
      );
      setNotes((prev) => prev.map((n) => (n.id === id ? { ...n, failed: true } : n)));
    }
  }, [selectedNote, currentKB, setSelectedNote, setNotes]);

  const handleIngestNote = useCallback(async () => {
    if (!selectedNote || !selectedNote.content.trim()) {
      alert("Cannot ingest an empty note");
      return;
    }

    try {
      setIsSaving(true);
      if (
        selectedNote.content !== contentBeforeEditRef.current ||
        (selectedNote.title || "") !== titleBeforeEditRef.current
      ) {
        await handleSaveNote(selectedNote);
      }
      await api.ingestNote(selectedNote.id, currentKB);
      // Track as in-flight — polling will update state when done
      setIngestingNoteIds((prev) => new Set([...prev, selectedNote.id]));
      // Optimistically clear any previous failure flag and show queue status.
      const patched = {
        ...selectedNote,
        processed: false,
        failed: false,
        processing_stage: "Queued for ingestion",
        processing_model: null,
      };
      setSelectedNote(patched);
      setNotes((prev) =>
        prev.map((n) => (n.id === selectedNote.id ? patched : n)),
      );
    } catch (error) {
      console.error("Error ingesting note:", error);
      alert("Failed to ingest note. Please try again.");
    } finally {
      setIsSaving(false);
    }
  }, [
    selectedNote,
    contentBeforeEditRef,
    titleBeforeEditRef,
    handleSaveNote,
    currentKB,
    setIsSaving,
    setSelectedNote,
    setNotes,
  ]);

  // Poll status for any notes that have been queued for ingestion
  useEffect(() => {
    if (ingestingNoteIds.size === 0) return;

    const poll = async () => {
      const updates: Array<{
        id: string;
        processed: boolean;
        failed: boolean;
        processing_stage?: string | null;
        processing_model?: string | null;
      }> = [];
      const refreshedNotes: Note[] = [];
      await Promise.all(
        Array.from(ingestingNoteIds).map(async (noteId) => {
          try {
            const status = await api.getNoteStatus(noteId, currentKB);
            updates.push({
              id: noteId,
              processed: status.processed,
              failed: status.failed,
              processing_stage: status.processing_stage,
              processing_model: status.processing_model,
            });
            if (status.processed || status.failed) {
              refreshedNotes.push(await api.getNote(noteId, currentKB));
            }
          } catch {
            /* ignore */
          }
        }),
      );
      if (updates.length === 0) return;
      const refreshedById = new Map(
        refreshedNotes.map((note) => [note.id, note]),
      );
      setIngestingNoteIds((prev) => {
        const next = new Set(prev);
        updates
          .filter((c) => c.processed || c.failed)
          .forEach((c) => next.delete(c.id));
        return next;
      });
      setNotes((prev) =>
        prev.map((n) => {
          const refreshed = refreshedById.get(n.id);
          if (refreshed) return refreshed;
          const update = updates.find((c) => c.id === n.id);
          return update
            ? {
                ...n,
                processed: update.processed,
                failed: update.failed,
                processing_stage: update.processing_stage,
                processing_model: update.processing_model,
              }
            : n;
        }),
      );
      setSelectedNote((prev) => {
        if (!prev) return null;
        const refreshed = refreshedById.get(prev.id);
        if (refreshed) {
          const hasUnsavedEdits =
            prev.content !== contentBeforeEditRef.current ||
            (prev.title || "") !== titleBeforeEditRef.current;
          if (!hasUnsavedEdits) {
            contentBeforeEditRef.current = refreshed.content;
            titleBeforeEditRef.current = refreshed.title || "";
            return refreshed;
          }
          return {
            ...refreshed,
            content: prev.content,
          };
        }
        const update = updates.find((c) => c.id === prev.id);
        return update
          ? {
              ...prev,
              processed: update.processed,
              failed: update.failed,
              processing_stage: update.processing_stage,
              processing_model: update.processing_model,
            }
          : prev;
      });
    };

    const interval = setInterval(poll, 5000);
    return () => clearInterval(interval);
  }, [
    ingestingNoteIds,
    currentKB,
    setNotes,
    setSelectedNote,
    contentBeforeEditRef,
    titleBeforeEditRef,
  ]);

  /** Stop the open note's ingestion; it goes back to plain "Saved". */
  const handleCancelIngest = useCallback(async () => {
    if (!selectedNote) return;
    const id = selectedNote.id;
    const idle = { processed: false, failed: false, processing_stage: "Saved", processing_model: null };
    setSelectedNote((prev) => (prev && prev.id === id ? { ...prev, ...idle } : prev));
    setNotes((prev) => prev.map((n) => (n.id === id ? { ...n, ...idle } : n)));
    setIngestingNoteIds((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
    try {
      await api.cancelIngest(id, currentKB);
    } catch (error) {
      console.error("Could not cancel ingestion:", error);
    }
  }, [selectedNote, currentKB, setSelectedNote, setNotes]);

  return {
    ingestingNoteIds,
    setIngestingNoteIds,
    handleIngestNote,
    handleCancelIngest,
    handleDismissFailure,
  };
}
