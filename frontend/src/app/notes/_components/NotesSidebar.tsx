import { FolderPlus, Loader2, Plus, Search, Zap } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Note } from "@/lib/types";
import { isActiveProcessingNote } from "../_lib/processing-status";
import type { ProcessedFilter, VaultFileEntry } from "../_lib/types";
import { NotesBatchBar } from "./NotesBatchBar";
import { NotesEmptyState } from "./NotesEmptyState";
import { VaultFolderTree } from "./VaultFolderTree";

type NotesSidebarProps = {
  currentKB: string;
  currentKBName: string;
  notes: Note[];
  searchQuery: string;
  processedFilter: ProcessedFilter;
  isLoading: boolean;
  isSaving: boolean;
  selectedFolder: string;
  vaultName: string;
  vaultFolders: string[];
  attachmentFiles: VaultFileEntry[];
  expandedFolders: Set<string>;
  selectedNoteId: string | null;
  selectedNoteIds: Set<string>;
  batchDeleting: boolean;
  dragNoteId: string | null;
  dragFileRel: string | null;
  selectedFileRels: Set<string>;
  onToggleFileSelected: (relPath: string) => void;
  onMoveVaultFiles: (rels: string[], folder: string) => void;
  onSearchChange: (query: string) => void;
  onFilterChange: (filter: ProcessedFilter) => void;
  onReingestVault: () => void;
  onOpenFolderDialog: (parent: string) => void;
  onCreateNote: (folderOverride?: string) => void;
  onToggleSelectAll: () => void;
  onBatchDelete: () => void;
  onToggleFolder: (path: string) => void;
  onSelectFolder: (path: string) => void;
  onNoteSelect: (note: Note) => void;
  onNoteContextMenu?: (note: Note, x: number, y: number) => void;
  onFolderContextMenu?: (path: string, x: number, y: number) => void;
  onMoveVaultFolder: (fromPath: string, folder: string) => void;
  onDeleteVaultFolder: (path: string) => void;
  onToggleNoteSelected: (noteId: string) => void;
  onMoveNoteToFolder: (noteId: string, folder: string) => void;
  onMoveVaultFile: (fromRel: string, folder: string) => void;
  onDragNoteStart: (noteId: string) => void;
  onDragNoteEnd: () => void;
  onDragFileStart: (relPath: string) => void;
  onDragFileEnd: () => void;
  onFileClick: (relPath: string, name: string) => void;
  onRenameFile: (relPath: string) => void;
  onDeleteVaultAttachment: (relPath: string, name: string) => void;
};

export function NotesSidebar({
  notes,
  searchQuery,
  processedFilter,
  isLoading,
  isSaving,
  selectedFolder,
  vaultName,
  vaultFolders,
  attachmentFiles,
  expandedFolders,
  selectedNoteId,
  selectedNoteIds,
  batchDeleting,
  dragNoteId,
  dragFileRel,
  selectedFileRels,
  onToggleFileSelected,
  onMoveVaultFiles,
  onSearchChange,
  onFilterChange,
  onOpenFolderDialog,
  onCreateNote,
  onToggleSelectAll,
  onBatchDelete,
  onToggleFolder,
  onSelectFolder,
  onNoteSelect,
  onNoteContextMenu,
  onFolderContextMenu,
  onMoveVaultFolder,
  onDeleteVaultFolder,
  onToggleNoteSelected,
  onMoveNoteToFolder,
  onMoveVaultFile,
  onDragNoteStart,
  onDragNoteEnd,
  onDragFileStart,
  onDragFileEnd,
  onFileClick,
  onRenameFile,
  onDeleteVaultAttachment,
}: NotesSidebarProps) {
  const visibleNotes =
    processedFilter === "ingesting" ? notes.filter(isActiveProcessingNote) : notes;
  const needsOn = processedFilter === "needs";
  const needsCount = notes.filter((n) => !n.processed).length;

  return (
    <div className="flex w-[288px] shrink-0 flex-col border-r border-n-900">
      <div className="pane-header">
        <h5 className="pane-title">Notes</h5>
        <button
          type="button"
          title={selectedFolder ? `New folder under ${selectedFolder}` : "New folder"}
          onClick={() => onOpenFolderDialog(selectedFolder)}
          className="btn btn-ghost btn-icon"
        >
          <FolderPlus className="h-[15px] w-[15px]" />
        </button>
        <button
          onClick={() => void onCreateNote()}
          disabled={isSaving}
          title="New note (⌘N)"
          className="btn btn-primary btn-icon"
        >
          {isSaving ? (
            <Loader2 className="h-[15px] w-[15px] animate-spin" />
          ) : (
            <Plus className="h-[15px] w-[15px]" />
          )}
        </button>
      </div>

      <div className="flex gap-1.5 px-3 pb-2">
        <div className="flex h-[30px] min-w-0 flex-1 items-center gap-1.5 rounded-md border border-n-900 bg-surface px-2">
          <Search className="h-3.5 w-3.5 text-n-500" />
          <input
            type="text"
            placeholder="Filter notes"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            className="min-w-0 flex-1 bg-transparent text-[12.5px] outline-none placeholder:text-n-600"
          />
        </div>
        <button
          type="button"
          onClick={() => onFilterChange(needsOn ? "all" : "needs")}
          title="Only notes that still need ingesting"
          className={cn(
            "flex h-[30px] shrink-0 items-center gap-1 whitespace-nowrap rounded-md border px-2 text-[11.5px]",
            needsOn
              ? "border-accent bg-accent/12 text-accent"
              : "border-n-900 bg-surface text-n-400 hover:border-n-700",
          )}
        >
          <Zap className="h-3.5 w-3.5" />
          {needsCount}
        </button>
      </div>

      <NotesBatchBar
        noteCount={notes.length}
        selectedCount={selectedNoteIds.size}
        batchDeleting={batchDeleting}
        onToggleSelectAll={onToggleSelectAll}
        onBatchDelete={onBatchDelete}
      />

      <div className="flex-1 overflow-y-auto px-1.5 pb-2">
        {isLoading && notes.length === 0 && vaultFolders.length === 0 ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-5 w-5 animate-spin text-n-500" />
          </div>
        ) : notes.length === 0 && vaultFolders.length === 0 ? (
          <NotesEmptyState variant="sidebar" searchQuery={searchQuery} />
        ) : (
          <VaultFolderTree
            notes={visibleNotes}
            vaultFolders={vaultFolders}
            vaultName={vaultName}
            attachmentFiles={attachmentFiles}
            expandedFolders={expandedFolders}
            selectedFolder={selectedFolder}
            selectedNoteId={selectedNoteId}
            selectedNoteIds={selectedNoteIds}
            dragNoteId={dragNoteId}
            dragFileRel={dragFileRel}
            selectedFileRels={selectedFileRels}
            onToggleFileSelected={onToggleFileSelected}
            onMoveVaultFiles={onMoveVaultFiles}
            onToggleFolder={onToggleFolder}
            onSelectFolder={onSelectFolder}
            onNoteSelect={onNoteSelect}
            onNoteContextMenu={onNoteContextMenu}
            onFolderContextMenu={onFolderContextMenu}
            onMoveVaultFolder={onMoveVaultFolder}
            onDeleteVaultFolder={onDeleteVaultFolder}
            onToggleNoteSelected={onToggleNoteSelected}
            onCreateNote={onCreateNote}
            onOpenFolderDialog={onOpenFolderDialog}
            onMoveNoteToFolder={onMoveNoteToFolder}
            onMoveVaultFile={onMoveVaultFile}
            onDragNoteStart={onDragNoteStart}
            onDragNoteEnd={onDragNoteEnd}
            onDragFileStart={onDragFileStart}
            onDragFileEnd={onDragFileEnd}
            onFileClick={onFileClick}
            onRenameFile={onRenameFile}
            onDeleteVaultAttachment={onDeleteVaultAttachment}
          />
        )}
      </div>

      <div className="flex justify-between border-t border-n-900 px-3 py-2 text-[11px] text-n-500">
        <span className="truncate">
          {visibleNotes.length} {visibleNotes.length === 1 ? "note" : "notes"} ·{" "}
          {selectedFolder || vaultName}
        </span>
      </div>
    </div>
  );
}
