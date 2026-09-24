import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  AudioLines,
  Clock,
  FileText,
  Film,
  FolderPlus,
  Image as ImageIcon,
  Loader2,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Note } from "@/lib/types";
import { cn, isAudioUrl, isImageUrl, isVideoUrl } from "@/lib/utils";
import MarkdownNoteEditor from "@/components/markdown-editor/MarkdownNoteEditor";
import { EntityDetailPanel } from "@/components/entity-detail-panel";
import { ConnectedNotesPanel } from "@/components/connected-notes-panel";
import { useNotesPageController } from "./_hooks/useNotesPageController";
import { parseNoteAttachments } from "./_lib/parse-note-attachments";
import { isActiveProcessingNote } from "./_lib/processing-status";
import { NotesSidebar } from "./_components/NotesSidebar";
import { NoteEditorHeader, type ViewMode } from "./_components/NoteEditorHeader";
import { NotesEmptyState } from "./_components/NotesEmptyState";
import { noteStatus } from "./_components/NoteStatusBadge";
import { DatePickerModal } from "./_components/DatePickerModal";
import { FilePreviewModal } from "./_components/FilePreviewModal";
import { FolderDialog } from "./_components/FolderDialog";
import { RenameDialog } from "./_components/RenameDialog";
import { WikilinkHoverCard } from "./_components/WikilinkHoverCard";

function AttachmentIcon({ url }: { url: string }) {
  const cls = "h-4 w-4 text-accent-300";
  if (isAudioUrl(url)) return <AudioLines className={cls} />;
  if (isImageUrl(url)) return <ImageIcon className={cls} />;
  if (isVideoUrl(url)) return <Film className={cls} />;
  return <FileText className={cls} />;
}

export default function NotesPage() {
  const {
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
  } = useNotesPageController();
  const [viewMode, setViewMode] = useState<ViewMode>("live");
  type CtxMenu =
    | { kind: "note"; note: Note; x: number; y: number }
    | { kind: "folder"; path: string; x: number; y: number };
  const [ctxMenu, setCtxMenu] = useState<CtxMenu | null>(null);

  const selectedNote = selection.selectedNote;
  const selectedNoteId = selectedNote?.id ?? null;
  const selectedNoteEmpty = Boolean(selectedNote && !selectedNote.title && !selectedNote.content);

  // A fresh note lands in the title; an existing one lands in the body (the
  // editor's autoFocus). (Remounting the input with key={id} leaked one input
  // per note switch, so focus is driven by an effect.)
  const titleRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (selectedNoteId && selectedNoteEmpty) titleRef.current?.focus();
  }, [selectedNoteId, selectedNoteEmpty]);

  // Right-click menu on a note row: closes on any click, Escape, or scroll.
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
  }, [ctxMenu]);

  // The header buttons act on the open note; these act on any row.
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

  // ⌘N new note, ⌘/ Live↔Source. ?new=1 comes from the command palette.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return;
      if (e.key.toLowerCase() === "n") {
        e.preventDefault();
        void handleCreateNote();
      } else if (e.key === "/") {
        e.preventDefault();
        setViewMode((m) => (m === "live" ? "source" : "live"));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handleCreateNote]);

  useEffect(() => {
    if (!list.isLoading && window.location.search.includes("new=1")) {
      window.history.replaceState({}, "", "/notes");
      void handleCreateNote();
    }
    // Runs once the first list load settles; later loads must not re-create.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list.isLoading]);

  const status = selectedNote ? noteStatus(selectedNote) : null;
  const busy = selectedNote ? isActiveProcessingNote(selectedNote) : false;
  const noteContent = selectedNote?.content ?? "";
  const noteAttachments = useMemo(() => parseNoteAttachments(noteContent), [noteContent]);
  const panelOpen = Boolean(selectedNote && (showConnectedPanel || entityPanelNodeId));

  return (
    <div className="screen">
      <NotesSidebar
        currentKB={currentKB}
        currentKBName={currentKBName}
        notes={list.notes}
        searchQuery={list.searchQuery}
        processedFilter={list.processedFilter}
        isLoading={list.isLoading}
        isSaving={autosave.isSaving}
        selectedFolder={vault.selectedFolder}
        vaultName={vault.vaultName}
        vaultFolders={vault.vaultFolders}
        attachmentFiles={vault.attachmentFiles}
        expandedFolders={vault.expandedFolders}
        selectedNoteId={selectedNote?.id ?? null}
        selectedNoteIds={batch.selectedNoteIds}
        batchDeleting={batch.batchDeleting}
        dragNoteId={vault.dragNoteId}
        dragFileRel={vault.dragFileRel}
        selectedFileRels={vault.selectedFileRels}
        onToggleFileSelected={vault.toggleFileSelected}
        onMoveVaultFiles={vault.handleMoveVaultFiles}
        onSearchChange={list.setSearchQuery}
        onFilterChange={list.setProcessedFilter}
        onReingestVault={handleReingestVault}
        onOpenFolderDialog={vault.openFolderDialog}
        onCreateNote={handleCreateNote}
        onToggleSelectAll={batch.toggleSelectAll}
        onBatchDelete={batch.handleBatchDeleteNotes}
        onToggleFolder={vault.toggleFolder}
        onSelectFolder={vault.setSelectedFolder}
        onNoteSelect={handleNoteSelect}
        onNoteContextMenu={(note, x, y) => setCtxMenu({ kind: "note", note, x, y })}
        onFolderContextMenu={(path, x, y) => setCtxMenu({ kind: "folder", path, x, y })}
        onMoveVaultFolder={vault.handleMoveVaultFolder}
        onDeleteVaultFolder={handleDeleteVaultFolder}
        onToggleNoteSelected={batch.toggleNoteSelected}
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

      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
        {selectedNote && status ? (
          <>
            <NoteEditorHeader
              selectedNote={selectedNote}
              currentKB={currentKB}
              isSaving={autosave.isSaving}
              isUploading={media.isUploading}
              isRecording={media.isRecording}
              showConnectedPanel={showConnectedPanel}
              viewMode={viewMode}
              onViewModeChange={setViewMode}
              onIngest={ingest.handleIngestNote}
              onCancelIngest={ingest.handleCancelIngest}
              onAttachFile={media.handleFileAttach}
              onToggleDatePicker={() => media.setShowDatePicker(!media.showDatePicker)}
              onToggleRecording={media.isRecording ? media.stopRecording : media.startRecording}
              onToggleConnectedPanel={() => setShowConnectedPanel((v) => !v)}
              onDelete={handleDeleteNote}
            />

            {busy && (
              <div className="h-0.5 bg-n-900">
                <div className="h-full w-2/3 animate-pulse bg-accent" />
              </div>
            )}

            {selectedNote.failed && (
              <div className="mx-6 mt-3.5 flex items-center gap-2.5 rounded-md bg-surface px-3 py-2.5 text-[12.5px] shadow-sm">
                <AlertCircle className="h-4 w-4 shrink-0 text-danger" />
                <span className="flex-1">
                  {selectedNote.processing_stage?.startsWith("Ingestion failed: ")
                    ? `Ingestion failed — ${selectedNote.processing_stage.slice("Ingestion failed: ".length)}.`
                    : selectedNote.processing_stage &&
                        selectedNote.processing_stage !== "Ingestion failed"
                      ? `Ingestion failed — ${selectedNote.processing_stage}.`
                      : "Ingestion failed. Fix the note or attachment and retry."}
                </span>
                <button type="button" onClick={ingest.handleIngestNote} className="btn btn-sm btn-primary">
                  Retry
                </button>
                <button
                  type="button"
                  onClick={() => void ingest.handleDismissFailure()}
                  className="btn btn-sm btn-ghost btn-icon w-6"
                  aria-label="Dismiss"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            )}

            <div className="flex min-h-0 flex-1 overflow-hidden">
              <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto px-12 pb-20 pt-7">
                <div className="flex min-h-0 w-full max-w-[760px] flex-1 flex-col">
                  <input
                    ref={titleRef}
                    type="text"
                    value={selectedNote.title || ""}
                    onChange={(e) => selection.handleTitleChange(e.target.value)}
                    onKeyDown={(e) => {
                      // Title → body in one keystroke, like Obsidian.
                      if (e.key === "Enter" || e.key === "ArrowDown" || (e.key === "Tab" && !e.shiftKey)) {
                        e.preventDefault();
                        editorRef.current?.focus();
                      }
                    }}
                    placeholder="Untitled"
                    className="mb-1.5 w-full bg-transparent text-[28px] font-medium leading-tight tracking-[-0.015em] outline-none placeholder:text-n-700"
                  />
                  <div className="mb-5 flex flex-wrap items-center gap-3.5 text-[12px] text-n-500">
                    <span className="inline-flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      {new Date(selectedNote.created_at).toLocaleDateString("en-US", {
                        month: "short",
                        day: "numeric",
                        year: "numeric",
                        hour: "numeric",
                        minute: "2-digit",
                      })}
                    </span>
                    <span className={cn("inline-flex items-center gap-1.5", status.text)}>
                      {status.key === "ingesting" ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <span className={cn("dot h-1.5 w-1.5", status.dot)} />
                      )}
                      {status.label}
                    </span>
                  </div>

                  {noteAttachments.length > 0 && (
                    <div className="mb-5 flex flex-wrap gap-2">
                      {noteAttachments.map((att) => (
                        <span
                          key={att.url}
                          className="group/chip inline-flex items-center gap-2 rounded-md bg-surface py-1.5 pl-2 pr-1.5 text-[12px] shadow-sm hover:shadow-md"
                        >
                          <button
                            type="button"
                            onClick={() => media.handleFileClick(att.url, att.label)}
                            className="inline-flex items-center gap-2"
                          >
                            <AttachmentIcon url={att.url} />
                            <span className="max-w-[220px] truncate">{att.label}</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => media.handleDeleteFile(att.url, att.raw)}
                            title="Delete file"
                            className="grid h-4 w-4 place-items-center rounded text-n-600 hover:text-danger-text"
                          >
                            <X className="h-3 w-3" />
                          </button>
                        </span>
                      ))}
                    </div>
                  )}

                  <MarkdownNoteEditor
                    key={selectedNote.id}
                    ref={editorRef}
                    noteId={selectedNote.id}
                    autoFocus={!selectedNoteEmpty}
                    value={selectedNote.content}
                    onChange={selection.handleContentChange}
                    onEntityClick={handleEntityClick}
                    onWikilinkClick={wikilink.handleWikilinkClick}
                    onWikilinkHover={wikilink.handleWikilinkHover}
                    onWikilinkLeave={wikilink.handleWikilinkLeave}
                    onDropFiles={media.attachFiles}
                    attachDisabled={media.isUploading}
                    kb={currentKB}
                    notes={list.notes}
                    viewMode={viewMode}
                    attachmentJobs={attachments.jobs}
                    onProcessAttachment={attachments.start}
                    onCancelAttachment={attachments.cancel}
                    onOpenFile={media.handleFileClick}
                    placeholder="Keep writing… drop a file to embed it, [[ links a note."
                    className="min-h-[50vh] w-full"
                  />
                </div>
              </div>

              {panelOpen && (
                <aside className="flex w-[320px] shrink-0 flex-col border-l border-n-900 bg-bg-deep/40">
                  {entityPanelNodeId ? (
                    <EntityDetailPanel
                      nodeId={entityPanelNodeId}
                      name={entityPanelName}
                      kb={currentKB}
                      onClose={() => setEntityPanelNodeId(null)}
                    />
                  ) : (
                    <ConnectedNotesPanel
                      noteId={selectedNote.id}
                      noteContent={selectedNote.content}
                      kb={currentKB}
                      onClose={() => setShowConnectedPanel(false)}
                      onSelectNote={(id) => {
                        const match = list.notes.find((n) => n.id === id);
                        if (match) void handleNoteSelect(match);
                      }}
                      onSelectEntity={handleEntityClick}
                    />
                  )}
                </aside>
              )}
            </div>
          </>
        ) : (
          <NotesEmptyState
            variant="editor"
            isSaving={autosave.isSaving}
            onCreateNote={() => void handleCreateNote()}
          />
        )}
      </div>

      {ctxMenu?.kind === "folder" && (
        <div
          className="popover fixed z-[85] min-w-[200px]"
          style={{
            left: Math.min(ctxMenu.x, window.innerWidth - 220),
            top: Math.min(ctxMenu.y, window.innerHeight - 160),
          }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <div className="truncate px-2.5 pb-1.5 pt-1 text-[11px] text-n-500">
            {ctxMenu.path.split("/").pop()}
          </div>
          <button
            type="button"
            className="menu-item"
            onClick={() => {
              const { path } = ctxMenu;
              setCtxMenu(null);
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
          style={{
            left: Math.min(ctxMenu.x, window.innerWidth - 220),
            top: Math.min(ctxMenu.y, window.innerHeight - 160),
          }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <div className="truncate px-2.5 pb-1.5 pt-1 text-[11px] text-n-500">
            {ctxMenu.note.title || "Untitled"}
          </div>
          <button
            type="button"
            className="menu-item"
            onClick={() => {
              const { note } = ctxMenu;
              setCtxMenu(null);
              void handleNoteSelect(note);
            }}
          >
            <FileText className="h-3.5 w-3.5 text-n-500" /> Open
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

      {media.showDatePicker && selectedNote && (
        <DatePickerModal
          createdAt={selectedNote.created_at}
          pendingDateChange={media.pendingDateChange}
          onPendingChange={media.setPendingDateChange}
          onClose={() => void media.handleCloseDatePicker()}
        />
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

      {wikilink.wikilinkPreview && <WikilinkHoverCard preview={wikilink.wikilinkPreview} />}
    </div>
  );
}
