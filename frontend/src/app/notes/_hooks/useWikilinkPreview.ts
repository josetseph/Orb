import { useCallback, useMemo, useRef, useState } from "react";
import type { Note } from "@/lib/types";
import { api } from "@/lib/api";
import {
  parseWikilinkCreateTarget,
  WikilinkResolver,
} from "../_lib/wikilinks";
import type { WikilinkPreviewState } from "../_lib/types";

type UseWikilinkPreviewArgs = {
  notes: Note[];
  onNoteSelect: (note: Note) => void;
  sourceNote?: Note | null;
  kb: string;
  /** Refresh the sidebar after creating a missing linked note. */
  onNotesChanged?: () => void | Promise<void>;
};

export function useWikilinkPreview({
  notes,
  onNoteSelect,
  sourceNote,
  kb,
  onNotesChanged,
}: UseWikilinkPreviewArgs) {
  const [wikilinkPreview, setWikilinkPreview] =
    useState<WikilinkPreviewState | null>(null);
  const creatingRef = useRef(false);

  // Index once per notes list — hover fires often and must not re-scan the vault.
  const resolver = useMemo(() => new WikilinkResolver(notes), [notes]);

  const handleWikilinkClick = useCallback(
    async (target: string) => {
      setWikilinkPreview(null);
      const match = resolver.resolve(target, sourceNote);
      if (match) {
        void onNoteSelect(match);
        return;
      }

      // Obsidian-style: clicking a missing link creates the note.
      if (creatingRef.current) return;
      creatingRef.current = true;
      try {
        const parsed = parseWikilinkCreateTarget(target);
        let folder = parsed.folder;
        const title = parsed.title;
        // Bare names land beside the linking note (same folder).
        if (!folder && sourceNote?.rel_path) {
          const src = sourceNote.rel_path.replace(/\\/g, "/");
          const slash = src.lastIndexOf("/");
          if (slash >= 0) folder = src.slice(0, slash);
        }
        const newNote = await api.createNote(
          "",
          new Date().toISOString(),
          kb,
          title,
          folder || undefined,
        );
        await onNotesChanged?.();
        void onNoteSelect(newNote);
      } catch (error) {
        console.error("Failed to create note from wikilink:", error);
        alert(`Failed to create note "${target}". Please try again.`);
      } finally {
        creatingRef.current = false;
      }
    },
    [resolver, onNoteSelect, sourceNote, kb, onNotesChanged],
  );

  const handleWikilinkHover = useCallback(
    (target: string, rect: DOMRect) => {
      const match = resolver.resolve(target, sourceNote);
      setWikilinkPreview({
        title: match?.title || target,
        content: match?.content || "",
        x: Math.min(rect.left, window.innerWidth - 340),
        y: Math.min(rect.bottom + 8, window.innerHeight - 220),
        missing: !match,
      });
    },
    [resolver, sourceNote],
  );

  const handleWikilinkLeave = useCallback(() => {
    setWikilinkPreview(null);
  }, []);

  return {
    wikilinkPreview,
    handleWikilinkClick,
    handleWikilinkHover,
    handleWikilinkLeave,
  };
}
