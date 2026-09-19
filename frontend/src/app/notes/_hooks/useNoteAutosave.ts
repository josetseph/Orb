import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from "react";
import { api } from "@/lib/api";
import type { Note } from "@/lib/types";

type UseNoteAutosaveArgs = {
  selectedNote: Note | null;
  selectedNoteRef: MutableRefObject<Note | null>;
  contentBeforeEditRef: MutableRefObject<string>;
  titleBeforeEditRef: MutableRefObject<string>;
  currentKB: string;
  currentKBRef: MutableRefObject<string>;
  patchLocalNote: (note: Note) => void;
};

export function useNoteAutosave({
  selectedNote,
  selectedNoteRef,
  contentBeforeEditRef,
  titleBeforeEditRef,
  currentKB,
  currentKBRef,
  patchLocalNote,
}: UseNoteAutosaveArgs) {
  const [isSaving, setIsSaving] = useState(false);
  const autoSaveTimeoutRef = useRef<NodeJS.Timeout | undefined>(undefined);

  const handleSaveNote = useCallback(
    async (note: Note) => {
      // Don't save if content hasn't changed
      if (
        !note ||
        (note.content === contentBeforeEditRef.current &&
          (note.title || "") === titleBeforeEditRef.current)
      ) {
        return;
      }

      try {
        setIsSaving(true);
        const savedNote = await api.updateNote(
          note.id,
          note.content,
          undefined,
          currentKB,
          note.title || undefined,
        );
        const nextNote = savedNote ?? note;
        const live = selectedNoteRef.current;
        if (!live || live.id !== note.id) {
          // Note switched while the PUT was in flight — nothing to patch.
          return;
        }
        // Move the baseline to what actually got saved.
        contentBeforeEditRef.current = nextNote.content;
        titleBeforeEditRef.current = nextNote.title || "";
        if (
          live.content !== note.content ||
          (live.title || "") !== (note.title || "")
        ) {
          // The user kept typing during the PUT. Replacing the note now would
          // reset the controlled editor to the saved snapshot and silently
          // wipe those keystrokes — skip the patch; the moved baseline makes
          // the autosave effect re-save the newer edits.
          if ((nextNote.rel_path ?? null) !== (live.rel_path ?? null)) {
            // Retitling renames the vault file server-side; carry the fresh
            // path over without touching the newer content/title.
            patchLocalNote({ ...live, rel_path: nextNote.rel_path });
          }
          return;
        }
        patchLocalNote(nextNote);
      } catch (error) {
        console.error("Error saving note:", error);
      } finally {
        setIsSaving(false);
      }
    },
    [patchLocalNote, currentKB, contentBeforeEditRef, titleBeforeEditRef, selectedNoteRef],
  );

  // Auto-save: Debounced save 1.5 seconds after user stops typing
  useEffect(() => {
    if (!selectedNote) return;

    // Clear any existing timeout
    if (autoSaveTimeoutRef.current) {
      clearTimeout(autoSaveTimeoutRef.current);
    }

    // Only auto-save if content has changed
    if (
      selectedNote.content !== contentBeforeEditRef.current ||
      (selectedNote.title || "") !== titleBeforeEditRef.current
    ) {
      autoSaveTimeoutRef.current = setTimeout(() => {
        handleSaveNote(selectedNote);
      }, 1500); // Save 1.5 seconds after user stops typing
    }

    return () => {
      if (autoSaveTimeoutRef.current) {
        clearTimeout(autoSaveTimeoutRef.current);
      }
    };
  }, [selectedNote, handleSaveNote, contentBeforeEditRef, titleBeforeEditRef]);

  // Flush pending edits when leaving the page.
  useEffect(() => {
    return () => {
      if (autoSaveTimeoutRef.current) {
        clearTimeout(autoSaveTimeoutRef.current);
      }
      const note = selectedNoteRef.current;
      if (
        note &&
        (note.content !== contentBeforeEditRef.current ||
          (note.title || "") !== titleBeforeEditRef.current)
      ) {
        void api
          .updateNote(
            note.id,
            note.content,
            undefined,
            currentKBRef.current,
            note.title || undefined,
          )
          .catch(() => {});
      }
    };
  }, [selectedNoteRef, contentBeforeEditRef, titleBeforeEditRef, currentKBRef]);

  // Save on window unload. A plain XHR is killed with the window, so use the
  // keepalive path which the browser lets finish after close.
  useEffect(() => {
    const handleBeforeUnload = () => {
      const note = selectedNoteRef.current;
      if (
        note &&
        (contentBeforeEditRef.current !== note.content ||
          titleBeforeEditRef.current !== (note.title || ""))
      ) {
        api.updateNoteOnUnload(
          note.id,
          note.content,
          currentKBRef.current,
          note.title || undefined,
        );
      }
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    return () =>
      window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [selectedNoteRef, currentKBRef, contentBeforeEditRef, titleBeforeEditRef]);

  const cancelPendingAutosave = useCallback(() => {
    if (autoSaveTimeoutRef.current) {
      clearTimeout(autoSaveTimeoutRef.current);
      autoSaveTimeoutRef.current = undefined;
    }
  }, []);

  return {
    isSaving,
    setIsSaving,
    handleSaveNote,
    autoSaveTimeoutRef,
    cancelPendingAutosave,
  };
}

export type NoteAutosaveApi = ReturnType<typeof useNoteAutosave> & {
  setIsSaving: Dispatch<SetStateAction<boolean>>;
};
