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

/** `[[note#heading]]` reaches us as `note#heading`; `[[#heading]]` means this note. */
function splitTarget(target: string): [name: string, heading: string] {
  const hash = target.indexOf("#");
  return hash < 0 ? [target, ""] : [target.slice(0, hash), target.slice(hash + 1)];
}

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
      // TODO: scroll to the `#heading` part once the page exposes a scroll-to-heading hook.
      const [name] = splitTarget(target);
      const match = name ? resolver.resolve(name, sourceNote) : sourceNote;
      if (match) {
        void onNoteSelect(match);
        return;
      }

      // Obsidian-style: clicking a missing link creates the note.
      if (creatingRef.current) return;
      creatingRef.current = true;
      try {
        const parsed = parseWikilinkCreateTarget(name);
        let folder = parsed.folder;
        const title = parsed.title;
        // Bare names land beside the linking note (same folder).
        if (!folder && sourceNote?.rel_path) {
          const src = sourceNote.rel_path;
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
      const [name] = splitTarget(target);
      const match = name ? resolver.resolve(name, sourceNote) : sourceNote;
      setWikilinkPreview({
        title: match?.title || name,
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
