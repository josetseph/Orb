import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  ArrowUpDown,
  Check,
  ChevronsDownUp,
  FileText,
  FolderPlus,
  Loader2,
  PanelTopOpen,
  Search,
  SquarePen,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useNotes } from "@/lib/notes-context";
import type { Note } from "@/lib/types";
import { WorkspaceFooter } from "@/components/sidebar";
import { NOTE_SORTS } from "@/app/notes/_lib/folder-tree";
import { isActiveProcessingNote } from "@/app/notes/_lib/processing-status";
import { NotesBatchBar } from "@/app/notes/_components/NotesBatchBar";
import { NotesEmptyState } from "@/app/notes/_components/NotesEmptyState";
import { VaultFolderTree } from "@/app/notes/_components/VaultFolderTree";
import { FilePreviewModal } from "@/app/notes/_components/FilePreviewModal";
import { FolderDialog } from "@/app/notes/_components/FolderDialog";
import { RenameDialog } from "@/app/notes/_components/RenameDialog";

/** Close a popover on an outside click or Escape. */
function useDismiss(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);
  return ref;
}

const tool = "grid h-7 w-7 place-items-center rounded-md text-n-500 hover:bg-n-900 hover:text-text";

/** The vault's file tree, on every page. Clicking a note opens it in the notes view. */
export function FileExplorer() {
  const {
    list,
    autosave,
    vault,
    batch,
    media,
    sort,
    setSort,
    openNote,
    setCtxMenu,
    handleCreateNote,
    handleDeleteVaultFolder,
    handleDeleteVaultAttachment,
    selection,
  } = useNotes();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const createNote = (folder?: string) => {
    if (pathname !== "/notes") navigate("/notes");
    void handleCreateNote(folder);
  };
  const [sortOpen, setSortOpen] = useState(false);
  const sortRef = useDismiss(sortOpen, () => setSortOpen(false));
  const [filterOpen, setFilterOpen] = useState(Boolean(list.searchQuery));
  const needsOn = list.processedFilter === "needs";
  const needsCount = list.notes.filter((n) => !n.processed).length;
  const visibleNotes =
    list.processedFilter === "ingesting" ? list.notes.filter(isActiveProcessingNote) : list.notes;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-center gap-0.5 px-2 pb-1.5 pt-2.5">
        <button
          type="button"
          className={tool}
          title="New note (⌘N)"
          disabled={autosave.isSaving}
          onClick={() => createNote()}
        >
          {autosave.isSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <SquarePen className="h-4 w-4" />}
        </button>
        <button
          type="button"
          className={tool}
          title={vault.selectedFolder ? `New folder under ${vault.selectedFolder}` : "New folder"}
          onClick={() => vault.openFolderDialog(vault.selectedFolder)}
        >
          <FolderPlus className="h-4 w-4" />
        </button>
        <div ref={sortRef} className="relative">
          <button type="button" className={tool} title="Change sort order" onClick={() => setSortOpen((v) => !v)}>
            <ArrowUpDown className="h-4 w-4" />
          </button>
          {sortOpen && (
            <div className="popover absolute left-1/2 top-[32px] z-40 w-[230px] -translate-x-1/2">
              {NOTE_SORTS.map((o) => (
                <button
                  key={o.key}
                  type="button"
                  className="menu-item"
                  onClick={() => {
                    setSort(o.key);
                    setSortOpen(false);
                  }}
                >
                  <span className="flex-1">{o.label}</span>
                  {sort === o.key && <Check className="h-3.5 w-3.5 text-accent" />}
                </button>
              ))}
            </div>
          )}
        </div>
        <button
          type="button"
          className={tool}
          title="Collapse all"
          onClick={() => vault.setExpandedFolders(new Set())}
        >
          <ChevronsDownUp className="h-4 w-4" />
        </button>
        <button
          type="button"
          className={cn(tool, (filterOpen || list.searchQuery) && "text-accent")}
          title="Filter notes"
          onClick={() => {
            if (filterOpen) list.setSearchQuery("");
            setFilterOpen((v) => !v);
          }}
        >
          <Search className="h-4 w-4" />
        </button>
        <button
          type="button"
          className={cn(
            "flex h-7 items-center gap-1 rounded-md px-1.5 text-[11px] text-n-500 hover:bg-n-900 hover:text-text",
            needsOn && "text-accent",
          )}
          title="Only notes that still need ingesting"
          onClick={() => list.setProcessedFilter(needsOn ? "all" : "needs")}
        >
          <Zap className="h-3.5 w-3.5" />
          {needsCount}
        </button>
      </div>

      {filterOpen && (
        <div className="mx-2.5 mb-1.5 flex h-[28px] items-center gap-1.5 rounded-md border border-n-900 bg-surface px-2">
          <Search className="h-3.5 w-3.5 text-n-500" />
          <input
            autoFocus
            type="text"
            placeholder="Filter notes"
            value={list.searchQuery}
            onChange={(e) => list.setSearchQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                list.setSearchQuery("");
                setFilterOpen(false);
              }
            }}
            className="min-w-0 flex-1 bg-transparent text-[12.5px] outline-none placeholder:text-n-600"
          />
          {list.searchQuery && (
            <button type="button" onClick={() => list.setSearchQuery("")} className="text-n-500 hover:text-text">
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      )}

      {batch.selectedNoteIds.size > 0 && (
        <NotesBatchBar
          noteCount={list.notes.length}
          selectedCount={batch.selectedNoteIds.size}
          batchDeleting={batch.batchDeleting}
          onToggleSelectAll={batch.toggleSelectAll}
          onBatchDelete={batch.handleBatchDeleteNotes}
        />
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
        {list.isLoading && list.notes.length === 0 && vault.vaultFolders.length === 0 ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-5 w-5 animate-spin text-n-500" />
          </div>
        ) : list.notes.length === 0 && vault.vaultFolders.length === 0 ? (
          <NotesEmptyState variant="sidebar" searchQuery={list.searchQuery} />
        ) : (
          <VaultFolderTree
            notes={visibleNotes}
            sort={sort}
            vaultFolders={vault.vaultFolders}
            vaultName={vault.vaultName}
            attachmentFiles={vault.attachmentFiles}
            expandedFolders={vault.expandedFolders}
            selectedFolder={vault.selectedFolder}
            selectedNoteId={selection.selectedNote?.id ?? null}
            selectedNoteIds={batch.selectedNoteIds}
            dragNoteId={vault.dragNoteId}
            dragFileRel={vault.dragFileRel}
            selectedFileRels={vault.selectedFileRels}
            onToggleFileSelected={vault.toggleFileSelected}
            onMoveVaultFiles={vault.handleMoveVaultFiles}
            onToggleFolder={vault.toggleFolder}
            onSelectFolder={vault.setSelectedFolder}
            onNoteSelect={(note, newTab) => void openNote(note.id, { newTab })}
            onNoteContextMenu={(note, x, y) => setCtxMenu({ kind: "note", note, x, y })}
            onFolderContextMenu={(path, x, y) => setCtxMenu({ kind: "folder", path, x, y })}
            onMoveVaultFolder={vault.handleMoveVaultFolder}
            onDeleteVaultFolder={handleDeleteVaultFolder}
            onToggleNoteSelected={batch.toggleNoteSelected}
            onCreateNote={createNote}
            onOpenFolderDialog={vault.openFolderDialog}
            onMoveNoteToFolder={vault.handleMoveNoteToFolder}
            onMoveVaultFile={vault.handleMoveVaultFile}
            onDragNoteStart={vault.setDragNoteId}
            onDragNoteEnd={() => vault.setDragNoteId(null)}
            onDragFileStart={vault.setDragFileRel}
            onDragFileEnd={() => vault.setDragFileRel(null)}
            onFileClick={media.handleFileClick}
            onRenameFile={vault.openRenameDialog}
            onDeleteVaultAttachment={handleDeleteVaultAttachment}
          />
        )}
      </div>

      <WorkspaceFooter />
    </div>
  );
}

/**
 * Explorer menus and dialogs, mounted once at the app level so they work
 * from any page (and with the explorer collapsed, for the file preview).
 */
export function NotesOverlays() {
  const {
    currentKB,
    ctxMenu,
    setCtxMenu,
    selection,
    ingest,
    list,
    batch,
    vault,
    media,
    openNote,
    handleCreateNote,
    handleDeleteNote,
    handleDeleteVaultFolder,
  } = useNotes();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const selectedNote = selection.selectedNote;

  // Closes on any click, Escape, or scroll.
  useEffect(() => {
    if (!ctxMenu) return;
    const close = () => setCtxMenu(null);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", close, true);
    };
  }, [ctxMenu, setCtxMenu]);

  // The editor's buttons act on the open note; these act on any row.
  const ingestNoteById = async (note: Note) => {
    if (selectedNote?.id === note.id) {
      await ingest.handleIngestNote();
      return;
    }
    try {
      await api.ingestNote(note.id, currentKB);
      ingest.setIngestingNoteIds((prev) => new Set([...prev, note.id]));
      list.setNotes((prev) =>
        prev.map((n) =>
          n.id === note.id
            ? { ...n, processed: false, failed: false, processing_stage: "Queued for ingestion", processing_model: null }
            : n,
        ),
      );
    } catch {
      alert("Could not queue this note for ingestion.");
    }
  };

  const deleteNoteById = async (note: Note) => {
    if (selectedNote?.id === note.id) {
      await handleDeleteNote();
      return;
    }
    if (
      !window.confirm(
        `Delete "${note.title || "Untitled"}"?\n\nThis removes it from Orb and deletes the markdown file in your vault folder.`,
      )
    ) {
      return;
    }
    try {
      await api.deleteNote(note.id, currentKB);
      batch.setSelectedNoteIds((prev) => {
        const next = new Set(prev);
        next.delete(note.id);
        return next;
      });
      await list.fetchNotes(list.searchQuery, list.processedFilter);
    } catch {
      alert("Delete failed. Refresh and try again.");
    }
  };

  const menuStyle = (x: number, y: number) => ({
    left: Math.min(x, window.innerWidth - 220),
    top: Math.min(y, window.innerHeight - 200),
  });

  return (
    <>
      {ctxMenu?.kind === "folder" && (
        <div
          className="popover fixed z-[85] min-w-[200px]"
          style={menuStyle(ctxMenu.x, ctxMenu.y)}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <div className="truncate px-2.5 pb-1.5 pt-1 text-[11px] text-n-500">{ctxMenu.path.split("/").pop()}</div>
          <button
            type="button"
            className="menu-item"
            onClick={() => {
              const { path } = ctxMenu;
              setCtxMenu(null);
              if (pathname !== "/notes") navigate("/notes");
              void handleCreateNote(path);
            }}
          >
            <FileText className="h-3.5 w-3.5 text-n-500" /> New note here
          </button>
          <button
            type="button"
            className="menu-item"
            onClick={() => {
              const { path } = ctxMenu;
              setCtxMenu(null);
              vault.openFolderDialog(path);
            }}
          >
            <FolderPlus className="h-3.5 w-3.5 text-n-500" /> New subfolder
          </button>
          <div className="my-1 h-px bg-divider" />
          <button
            type="button"
            className="menu-item text-danger-text"
            onClick={() => {
              const { path } = ctxMenu;
              setCtxMenu(null);
              void handleDeleteVaultFolder(path);
            }}
          >
            <Trash2 className="h-3.5 w-3.5" /> Delete folder
          </button>
        </div>
      )}

      {ctxMenu?.kind === "note" && (
        <div
          className="popover fixed z-[85] min-w-[200px]"
          style={menuStyle(ctxMenu.x, ctxMenu.y)}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <div className="truncate px-2.5 pb-1.5 pt-1 text-[11px] text-n-500">{ctxMenu.note.title || "Untitled"}</div>
          <button
            type="button"
            className="menu-item"
            onClick={() => {
              const { note } = ctxMenu;
              setCtxMenu(null);
              void openNote(note.id);
            }}
          >
            <FileText className="h-3.5 w-3.5 text-n-500" /> Open
          </button>
          <button
            type="button"
            className="menu-item"
            onClick={() => {
              const { note } = ctxMenu;
              setCtxMenu(null);
              void openNote(note.id, { newTab: true });
            }}
          >
            <PanelTopOpen className="h-3.5 w-3.5 text-n-500" /> Open in new tab
          </button>
          <button
            type="button"
            className="menu-item"
            disabled={isActiveProcessingNote(ctxMenu.note)}
            onClick={() => {
              const { note } = ctxMenu;
              setCtxMenu(null);
              void ingestNoteById(note);
            }}
          >
            <Zap className="h-3.5 w-3.5 text-n-500" />
            {ctxMenu.note.processed ? "Re-ingest" : ctxMenu.note.failed ? "Retry ingest" : "Ingest now"}
          </button>
          <div className="my-1 h-px bg-divider" />
          <button
            type="button"
            className="menu-item text-danger-text"
            onClick={() => {
              const { note } = ctxMenu;
              setCtxMenu(null);
              void deleteNoteById(note);
            }}
          >
            <Trash2 className="h-3.5 w-3.5" /> Delete
          </button>
        </div>
      )}

      {media.filePreview && (
        <FilePreviewModal
          filePreview={media.filePreview}
          currentKB={currentKB}
          onClose={() => media.setFilePreview(null)}
          onReveal={media.handleRevealPreviewFile}
        />
      )}

      {vault.folderDialog && (
        <FolderDialog
          folderDialog={vault.folderDialog}
          vaultName={vault.vaultName}
          onNameChange={(name) => vault.setFolderDialog((d) => (d ? { ...d, name } : d))}
          onSubmit={vault.submitFolderDialog}
          onCancel={() => vault.setFolderDialog(null)}
        />
      )}

      {vault.renameDialog && (
        <RenameDialog
          renameDialog={vault.renameDialog}
          onNameChange={(name) => vault.setRenameDialog((d) => (d ? { ...d, name } : d))}
          onSubmit={vault.submitRenameDialog}
          onCancel={() => vault.setRenameDialog(null)}
        />
      )}
    </>
  );
}
