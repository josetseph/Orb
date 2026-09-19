import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import { api } from "@/lib/api";
import { useKB } from "@/lib/kb-context";
import type { MarkdownNoteEditorHandle } from "@/components/markdown-editor/MarkdownNoteEditor";
import type { Note } from "@/lib/types";
import { lastNoteStorageKey } from "../_lib/storage-keys";
import type { VaultListing } from "./useNotesList";
import { useNotesList } from "./useNotesList";
import {
  useNoteSelectHandler,
  useNoteSelection,
} from "./useNoteSelection";
import { useNoteAutosave } from "./useNoteAutosave";
import { useNoteIngest } from "./useNoteIngest";
import { useVaultTree, useNoteRestoreEffects } from "./useVaultTree";
import { useNoteMedia } from "./useNoteMedia";
import { useNoteBatchSelection } from "./useNoteBatchSelection";
import { useWikilinkPreview } from "./useWikilinkPreview";
import { useAttachmentJobs } from "./useAttachmentJobs";

/** Composes all notes-page hooks. Keeps `page.tsx` as a thin view. */
export function useNotesPageController() {
  const { currentKB, currentKBName } = useKB();
  const currentKBRef = useRef(currentKB);
  const editorRef = useRef<MarkdownNoteEditorHandle>(null);
  const [showConnectedPanel, setShowConnectedPanel] = useState(false);
  const [entityPanelNodeId, setEntityPanelNodeId] = useState<string | null>(
    null,
  );
  const [entityPanelName, setEntityPanelName] = useState<string | undefined>();

  useEffect(() => {
    currentKBRef.current = currentKB;
  }, [currentKB]);

  // Bridge circular hook deps (list ↔ selection ↔ vault ↔ ingest).
  // Stable wrappers are required — inline lambdas recreate every render and
  // would retrigger restore/refresh effects into an infinite GET /notes/{id} loop.
  const setNotesBridge = useRef<Dispatch<SetStateAction<Note[]>>>(() => {});
  const syncBridge = useRef<(data: Note[]) => void>(() => {});
  const clearBridge = useRef<() => void>(() => {});
  const setIngestingBridge =
    useRef<Dispatch<SetStateAction<Set<string>>>>(() => {});
  const onVaultListingBridge = useRef<(listing: VaultListing) => void>(
    () => {},
  );

  const setNotesStable = useCallback<Dispatch<SetStateAction<Note[]>>>(
    (u) => setNotesBridge.current(u),
    [],
  );
  const syncSelectedNoteFromListStable = useCallback((d: Note[]) => {
    syncBridge.current(d);
  }, []);
  const setIngestingNoteIdsStable = useCallback<
    Dispatch<SetStateAction<Set<string>>>
  >((u) => setIngestingBridge.current(u), []);
  const onVaultListingStable = useCallback((l: VaultListing) => {
    onVaultListingBridge.current(l);
  }, []);
  const clearSelectionForKBSwitchStable = useCallback(() => {
    clearBridge.current();
  }, []);

  const selection = useNoteSelection({
    currentKB,
    setNotes: setNotesStable,
  });

  const list = useNotesList({
    currentKB,
    syncSelectedNoteFromList: syncSelectedNoteFromListStable,
    setIngestingNoteIds: setIngestingNoteIdsStable,
    onVaultListing: onVaultListingStable,
    clearSelectionForKBSwitch: clearSelectionForKBSwitchStable,
  });

  const autosave = useNoteAutosave({
    selectedNote: selection.selectedNote,
    selectedNoteRef: selection.selectedNoteRef,
    contentBeforeEditRef: selection.contentBeforeEditRef,
    titleBeforeEditRef: selection.titleBeforeEditRef,
    currentKB,
    currentKBRef,
    patchLocalNote: selection.patchLocalNote,
  });

  const { handleNoteSelect } = useNoteSelectHandler({
    currentKB,
    selectedNote: selection.selectedNote,
    contentBeforeEditRef: selection.contentBeforeEditRef,
    titleBeforeEditRef: selection.titleBeforeEditRef,
    handleSaveNote: autosave.handleSaveNote,
    setSelectedNote: selection.setSelectedNote,
  });

  const ingest = useNoteIngest({
    currentKB,
    selectedNote: selection.selectedNote,
    contentBeforeEditRef: selection.contentBeforeEditRef,
    titleBeforeEditRef: selection.titleBeforeEditRef,
    handleSaveNote: autosave.handleSaveNote,
    setSelectedNote: selection.setSelectedNote,
    setNotes: list.setNotes,
    setIsSaving: autosave.setIsSaving,
  });

  const vault = useVaultTree({
    currentKB,
    searchQuery: list.searchQuery,
    processedFilter: list.processedFilter,
    fetchNotes: list.fetchNotes,
    selectedNote: selection.selectedNote,
    onContentChange: selection.handleContentChange,
    refreshSelectedNote: selection.refreshSelectedNote,
    patchLocalNote: selection.patchLocalNote,
  });

  useLayoutEffect(() => {
    syncBridge.current = selection.syncSelectedNoteFromList;
    clearBridge.current = selection.clearSelectionForKBSwitch;
    setNotesBridge.current = list.setNotes;
    setIngestingBridge.current = ingest.setIngestingNoteIds;
    onVaultListingBridge.current = vault.applyVaultListing;
  });

  useNoteRestoreEffects({
    currentKB,
    searchQuery: list.searchQuery,
    processedFilter: list.processedFilter,
    selectedNoteRef: selection.selectedNoteRef,
    openNoteById: selection.openNoteById,
    refreshSelectedNote: selection.refreshSelectedNote,
    fetchNotes: list.fetchNotes,
  });

  const media = useNoteMedia({
    currentKB,
    selectedNote: selection.selectedNote,
    editorRef,
    handleContentChange: selection.handleContentChange,
    refreshSelectedNote: selection.refreshSelectedNote,
    fetchNotes: list.fetchNotes,
    searchQuery: list.searchQuery,
    processedFilter: list.processedFilter,
    setSelectedNote: selection.setSelectedNote,
    contentBeforeEditRef: selection.contentBeforeEditRef,
    titleBeforeEditRef: selection.titleBeforeEditRef,
    setIsSaving: autosave.setIsSaving,
    refreshVaultFiles: vault.refreshVaultFiles,
  });

  const batch = useNoteBatchSelection({
    currentKB,
    notes: list.notes,
    selectedNote: selection.selectedNote,
    setSelectedNote: selection.setSelectedNote,
    contentBeforeEditRef: selection.contentBeforeEditRef,
    autoSaveTimeoutRef: autosave.autoSaveTimeoutRef,
    fetchNotes: list.fetchNotes,
    searchQuery: list.searchQuery,
    processedFilter: list.processedFilter,
  });

  const wikilink = useWikilinkPreview({
    notes: list.notes,
    onNoteSelect: handleNoteSelect,
    sourceNote: selection.selectedNote,
    kb: currentKB,
    onNotesChanged: () =>
      list.fetchNotes(list.searchQuery, list.processedFilter),
  });

  const attachments = useAttachmentJobs({
    currentKB,
    selectedNote: selection.selectedNote,
    refreshSelectedNote: selection.refreshSelectedNote,
  });

  const handleEntityClick = useCallback((nodeId: string, name: string) => {
    setEntityPanelNodeId(nodeId);
    setEntityPanelName(name);
  }, []);

  const handleCreateNote = useCallback(
    async (folderOverride?: string) => {
      if (
        selection.selectedNote &&
        (selection.contentBeforeEditRef.current !==
          selection.selectedNote.content ||
          selection.titleBeforeEditRef.current !==
            (selection.selectedNote.title || ""))
      ) {
        await autosave.handleSaveNote(selection.selectedNote);
      }

      const folder =
        typeof folderOverride === "string"
          ? folderOverride
          : vault.selectedFolder;

      try {
        const newNote = await api.createNote(
          "",
          new Date().toISOString(),
          currentKB,
          undefined,
          folder || undefined,
        );

        await list.fetchNotes(list.searchQuery, list.processedFilter);
        selection.setSelectedNote(newNote);
        selection.resetBeforeEdit(newNote.title || "", "");
        if (newNote?.id) {
          sessionStorage.setItem(lastNoteStorageKey(currentKB), newNote.id);
        }
        if (folder) {
          vault.expandFolderAndAncestors(folder);
        }
      } catch (error) {
        console.error("Error creating note:", error);
        alert("Failed to create note. Please try again.");
      }
    },
    [selection, autosave, vault, currentKB, list],
  );

  const handleDeleteNote = useCallback(async () => {
    if (!selection.selectedNote) return;

    const confirmDelete = window.confirm(
      `Delete "${selection.selectedNote.title || "Untitled"}"?\n\nThis removes it from Orb and deletes the markdown file in your vault folder.`,
    );
    if (!confirmDelete) return;

    const deletedId = selection.selectedNote.id;
    try {
      autosave.cancelPendingAutosave();
      selection.resetBeforeEdit();
      selection.setSelectedNote(null);
      list.setNotes((prev) => prev.filter((n) => n.id !== deletedId));
      batch.setSelectedNoteIds((prev) => {
        const next = new Set(prev);
        next.delete(deletedId);
        return next;
      });
      await api.deleteNote(deletedId, currentKB);
      await list.fetchNotes(list.searchQuery, list.processedFilter);
    } catch (error) {
      console.error("Error deleting note:", error);
      selection.setSelectedNote(null);
      try {
        await list.fetchNotes(list.searchQuery, list.processedFilter);
      } catch {
        /* ignore */
      }
      alert(
        "Delete may have partially failed. If the note file is still in your vault folder, delete it manually, then Reload.",
      );
    }
  }, [selection, autosave, list, batch, currentKB]);

  const handleReingestVault = useCallback(async () => {
    if (
      !confirm(
        "Queue all notes in this vault for re-ingest? Graph/vectors will rebuild from current .md files.",
      )
    )
      return;
    try {
      await api.reingestVault(currentKB);
      await list.fetchNotes(list.searchQuery, list.processedFilter);
    } catch {
      /* ignore */
    }
  }, [currentKB, list]);

  const handleDeleteVaultAttachment = useCallback(
    async (relPath: string, name: string) => {
      const result = await vault.handleDeleteVaultAttachment(relPath, name);
      if (result?.deleted) {
        media.setFilePreview((prev) =>
          prev &&
          (prev.url.includes(relPath) ||
            prev.filename === name ||
            prev.url.endsWith(name))
            ? null
            : prev,
        );
      }
    },
    [vault, media],
  );

  const handleDeleteVaultFolder = useCallback(
    async (path: string) => {
      const open = selection.selectedNote;
      const inside = Boolean(open?.rel_path && open.rel_path.startsWith(`${path}/`));
      if (inside) autosave.cancelPendingAutosave();
      const deleted = await vault.handleDeleteVaultFolder(path);
      if (deleted && inside) {
        selection.resetBeforeEdit();
        selection.setSelectedNote(null);
      }
    },
    [selection, autosave, vault],
  );

  return {
    currentKB,
    currentKBName,
    editorRef,
    showConnectedPanel,
    setShowConnectedPanel,
    entityPanelNodeId,
    entityPanelName,
    setEntityPanelNodeId,
    selection,
    list,
    autosave,
    ingest,
    vault,
    media,
    batch,
    wikilink,
    attachments,
    handleEntityClick,
    handleNoteSelect,
    handleCreateNote,
    handleDeleteNote,
    handleReingestVault,
    handleDeleteVaultAttachment,
    handleDeleteVaultFolder,
  };
}
