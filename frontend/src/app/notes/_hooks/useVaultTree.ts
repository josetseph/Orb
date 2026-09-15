"use client";

import {
  useCallback,
  useEffect,
  useState,
  type MutableRefObject,
} from "react";
import { api } from "@/lib/api";
import type { Note } from "@/lib/types";
import { getApiErrorDetail } from "../_lib/api-error";
import { rewriteVaultPathsInContent } from "../_lib/rewrite-vault-urls";
import { lastNoteStorageKey } from "../_lib/storage-keys";
import type {
  FolderDialogState,
  ProcessedFilter,
  RenameDialogState,
  VaultFileEntry,
} from "../_lib/types";
import type { VaultListing } from "./useNotesList";

type UseVaultTreeArgs = {
  currentKB: string;
  searchQuery: string;
  processedFilter: ProcessedFilter;
  fetchNotes: (search?: string, filter?: ProcessedFilter) => Promise<void>;
  selectedNote: Note | null;
  onContentChange: (content: string) => void;
  refreshSelectedNote: (noteId: string) => Promise<void>;
  patchLocalNote: (note: Note) => void;
};

function expandedKey(kb: string) {
  return `orb:notes-expanded:${kb || "default"}`;
}

function readExpandedFolders(kb: string): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = localStorage.getItem(expandedKey(kb));
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
}

export function useVaultTree({
  currentKB,
  searchQuery,
  processedFilter,
  fetchNotes,
  selectedNote,
  onContentChange,
  refreshSelectedNote,
  patchLocalNote,
}: UseVaultTreeArgs) {
  // Folders start closed; the ones you open are remembered per workspace.
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(() =>
    readExpandedFolders(currentKB),
  );
  const [expandedFor, setExpandedFor] = useState(currentKB);
  if (expandedFor !== currentKB) {
    setExpandedFor(currentKB);
    setExpandedFolders(readExpandedFolders(currentKB));
  }
  useEffect(() => {
    try {
      localStorage.setItem(expandedKey(currentKB), JSON.stringify([...expandedFolders]));
    } catch {
      /* a lost preference is not worth an error */
    }
  }, [expandedFolders, currentKB]);
  /** Vault-relative folder selected for new notes / drop target ("" = root). */
  const [selectedFolder, setSelectedFolder] = useState<string>("");
  const [dragNoteId, setDragNoteId] = useState<string | null>(null);
  const [dragFileRel, setDragFileRel] = useState<string | null>(null);
  // ⌘-clicked attachments; dragging one of them moves the whole set.
  const [selectedFileRels, setSelectedFileRels] = useState<Set<string>>(() => new Set());
  const [vaultFolders, setVaultFolders] = useState<string[]>([]);
  const [vaultName, setVaultName] = useState<string>("Vault");
  const [attachmentFiles, setAttachmentFiles] = useState<VaultFileEntry[]>(
    [],
  );
  const [mediaFiles, setMediaFiles] = useState<VaultFileEntry[]>([]);
  const [folderDialog, setFolderDialog] = useState<FolderDialogState | null>(
    null,
  );
  const [renameDialog, setRenameDialog] = useState<RenameDialogState | null>(
    null,
  );

  const applyVaultListing = useCallback((listing: VaultListing) => {
    setVaultFolders(listing.folders || []);
    setAttachmentFiles(listing.attachments || []);
    setMediaFiles(listing.media_files || []);
    if (listing.vault_name) setVaultName(listing.vault_name);
  }, []);

  const rewriteOpenNotePaths = useCallback(
    (from: string, to: string) => {
      if (!selectedNote) return;
      const next = rewriteVaultPathsInContent(
        selectedNote.content,
        currentKB,
        from,
        to,
      );
      if (next !== selectedNote.content) {
        onContentChange(next);
      }
    },
    [selectedNote, currentKB, onContentChange],
  );

  const handleMoveNoteToFolder = useCallback(
    async (noteId: string, folder: string) => {
      try {
        const result = await api.moveNote(noteId, folder, currentKB);
        await fetchNotes(searchQuery, processedFilter);
        if (result?.note) {
          patchLocalNote(result.note);
        } else {
          void refreshSelectedNote(noteId);
        }
      } catch (error) {
        console.error("Error moving note:", error);
        alert(getApiErrorDetail(error) || "Failed to move note.");
      }
    },
    [
      currentKB,
      fetchNotes,
      searchQuery,
      processedFilter,
      patchLocalNote,
      refreshSelectedNote,
    ],
  );

  const handleMoveVaultFile = useCallback(
    async (fromRel: string, folder: string) => {
      const filename = fromRel.split("/").pop() || fromRel;
      const toRel = folder ? `${folder}/${filename}` : filename;
      if (toRel === fromRel) return;
      try {
        const result = await api.moveVaultFile(fromRel, toRel, currentKB);
        // Rewrite open note content if it referenced the old path
        if (result?.from && result?.to) {
          rewriteOpenNotePaths(result.from, result.to);
        }
        await fetchNotes(searchQuery, processedFilter);
      } catch (error) {
        console.error("Error moving file:", error);
        alert(getApiErrorDetail(error) || "Failed to move file.");
      }
    },
    [
      currentKB,
      fetchNotes,
      searchQuery,
      processedFilter,
      rewriteOpenNotePaths,
    ],
  );

  const toggleFileSelected = useCallback((relPath: string) => {
    setSelectedFileRels((prev) => {
      const next = new Set(prev);
      if (next.has(relPath)) next.delete(relPath);
      else next.add(relPath);
      return next;
    });
  }, []);

  const handleMoveVaultFiles = useCallback(
    async (rels: string[], folder: string) => {
      const moved: Array<{ from: string; to: string }> = [];
      const failed: string[] = [];
      for (const fromRel of rels) {
        const filename = fromRel.split("/").pop() || fromRel;
        const toRel = folder ? `${folder}/${filename}` : filename;
        if (toRel === fromRel) continue;
        try {
          const result = await api.moveVaultFile(fromRel, toRel, currentKB);
          if (result?.from && result?.to) moved.push({ from: result.from, to: result.to });
        } catch (error) {
          console.error("Error moving file:", error);
          failed.push(filename);
        }
      }
      for (const m of moved) rewriteOpenNotePaths(m.from, m.to);
      setSelectedFileRels(new Set());
      // One list refresh for the whole batch, not one per file.
      await fetchNotes(searchQuery, processedFilter);
      if (failed.length) alert(`Could not move: ${failed.join(", ")}`);
    },
    [currentKB, fetchNotes, searchQuery, processedFilter, rewriteOpenNotePaths],
  );

  const openRenameDialog = useCallback((relPath: string) => {
    setRenameDialog({
      rel_path: relPath,
      name: relPath.split("/").pop() || relPath,
    });
  }, []);

  const submitRenameDialog = useCallback(async () => {
    if (!renameDialog) return;
    const newName = renameDialog.name.trim();
    if (!newName) {
      alert("Enter a file name");
      return;
    }
    if (/[\\/]/.test(newName) || newName.includes("..")) {
      alert("File name cannot contain path separators");
      return;
    }
    const parts = renameDialog.rel_path.replace(/\\/g, "/").split("/");
    parts[parts.length - 1] = newName;
    const toRel = parts.join("/");
    if (toRel === renameDialog.rel_path) {
      setRenameDialog(null);
      return;
    }
    try {
      const result = await api.moveVaultFile(
        renameDialog.rel_path,
        toRel,
        currentKB,
      );
      setRenameDialog(null);
      if (result?.from && result?.to) {
        rewriteOpenNotePaths(result.from, result.to);
      }
      await fetchNotes(searchQuery, processedFilter);
    } catch (error) {
      console.error("Error renaming file:", error);
      alert(getApiErrorDetail(error) || "Failed to rename file.");
    }
  }, [
    renameDialog,
    currentKB,
    rewriteOpenNotePaths,
    fetchNotes,
    searchQuery,
    processedFilter,
  ]);

  const openFolderDialog = useCallback((parent: string) => {
    setSelectedFolder(parent);
    setFolderDialog({ parent, name: "" });
  }, []);

  const submitFolderDialog = useCallback(async () => {
    if (!folderDialog) return;
    const name = folderDialog.name.trim();
    if (!name) {
      alert("Enter a folder name");
      return;
    }
    if (/[\\/]/.test(name) || name.includes("..")) {
      alert("Folder name cannot contain path separators");
      return;
    }
    const path = folderDialog.parent
      ? `${folderDialog.parent}/${name}`
      : name;
    try {
      await api.mkdirVaultFolder(path, currentKB);
      setFolderDialog(null);
      setSelectedFolder(path);
      setExpandedFolders((prev) => {
        const next = new Set(prev);
        next.add(path);
        if (folderDialog.parent) next.add(folderDialog.parent);
        return next;
      });
      await fetchNotes(searchQuery, processedFilter);
    } catch (err) {
      console.error(err);
      alert(getApiErrorDetail(err) || "Failed to create folder");
    }
  }, [folderDialog, currentKB, fetchNotes, searchQuery, processedFilter]);

  const toggleFolder = useCallback((path: string) => {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const expandFolderAndAncestors = useCallback((folder: string) => {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      next.add(folder);
      const parts = folder.split("/");
      for (let i = 1; i < parts.length; i++) {
        next.add(parts.slice(0, i).join("/"));
      }
      return next;
    });
  }, []);

  const refreshVaultFiles = useCallback(async () => {
    try {
      const folderRes = await api.listVaultFolders(currentKB);
      setAttachmentFiles(folderRes.attachments || []);
      setMediaFiles(folderRes.media_files || []);
    } catch {
      /* optional */
    }
  }, [currentKB]);

  const handleDeleteVaultAttachment = useCallback(
    async (relPath: string, name: string) => {
      if (
        !confirm(
          `Delete "${name}" from your vault?\n\nLinks to this file will be removed from notes.`,
        )
      ) {
        return;
      }
      try {
        const result = await api.deleteVaultFile(relPath, currentKB);
        if (selectedNote && result?.deleted) {
          const oldUrl = `/vault-files/${currentKB}/${result.deleted}`;
          if (
            selectedNote.content.includes(result.deleted) ||
            selectedNote.content.includes(oldUrl)
          ) {
            // Server already stripped note bodies on disk; refresh open note
            void refreshSelectedNote(selectedNote.id);
          }
        }
        await fetchNotes(searchQuery, processedFilter);
        return result;
      } catch (error) {
        console.error("Error deleting vault file:", error);
        alert(getApiErrorDetail(error) || "Failed to delete file.");
        return null;
      }
    },
    [
      currentKB,
      selectedNote,
      refreshSelectedNote,
      fetchNotes,
      searchQuery,
      processedFilter,
    ],
  );

  return {
    expandedFolders,
    setExpandedFolders,
    selectedFolder,
    setSelectedFolder,
    dragNoteId,
    setDragNoteId,
    dragFileRel,
    setDragFileRel,
    selectedFileRels,
    toggleFileSelected,
    handleMoveVaultFiles,
    vaultFolders,
    vaultName,
    attachmentFiles,
    mediaFiles,
    folderDialog,
    setFolderDialog,
    renameDialog,
    setRenameDialog,
    applyVaultListing,
    handleMoveNoteToFolder,
    handleMoveVaultFile,
    openRenameDialog,
    submitRenameDialog,
    openFolderDialog,
    submitFolderDialog,
    toggleFolder,
    expandFolderAndAncestors,
    refreshVaultFiles,
    handleDeleteVaultAttachment,
  };
}

/** Restore last-opened note and listen for bfcache / visibility. */
export function useNoteRestoreEffects({
  isHydrated,
  currentKB,
  searchQuery,
  processedFilter,
  selectedNoteRef,
  openNoteById,
  refreshSelectedNote,
  fetchNotes,
}: {
  isHydrated: boolean;
  currentKB: string;
  searchQuery: string;
  processedFilter: ProcessedFilter;
  selectedNoteRef: MutableRefObject<Note | null>;
  openNoteById: (noteId: string) => Promise<void>;
  refreshSelectedNote: (noteId: string) => Promise<void>;
  fetchNotes: (search?: string, filter?: ProcessedFilter) => Promise<void>;
}) {
  useEffect(() => {
    if (!isHydrated) return;

    const params = new URLSearchParams(window.location.search);
    const noteParam = params.get("note");
    if (noteParam) {
      sessionStorage.setItem(lastNoteStorageKey(currentKB), noteParam);
      void openNoteById(noteParam);
      // Drop the query so refresh doesn't fight with in-page selection changes
      window.history.replaceState({}, "", "/notes");
      return;
    }

    // Only cold-restore when nothing is selected yet. Visibility / bfcache
    // handlers below refresh an already-open note; calling refresh on every
    // effect run used to amplify into a GET storm when callbacks were unstable.
    const restoreSelection = () => {
      if (selectedNoteRef.current) return;
      const noteId = sessionStorage.getItem(lastNoteStorageKey(currentKB));
      if (!noteId) return;
      void openNoteById(noteId);
    };

    const refreshOpenNote = () => {
      const noteId =
        selectedNoteRef.current?.id ??
        sessionStorage.getItem(lastNoteStorageKey(currentKB));
      if (!noteId) return;
      if (selectedNoteRef.current?.id === noteId) {
        void refreshSelectedNote(noteId);
      } else {
        void openNoteById(noteId);
      }
    };

    const handlePageShow = (event: PageTransitionEvent) => {
      if (event.persisted) {
        void fetchNotes(searchQuery, processedFilter);
        refreshOpenNote();
      }
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        refreshOpenNote();
      }
    };

    restoreSelection();
    window.addEventListener("pageshow", handlePageShow);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      window.removeEventListener("pageshow", handlePageShow);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isHydrated, currentKB, openNoteById, refreshSelectedNote]);
}
