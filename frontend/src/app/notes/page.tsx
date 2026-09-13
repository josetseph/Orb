"use client";

import { AnimatePresence } from "framer-motion";
import { ShaderBackground } from "@/components/shader-background";
import {
  MarkdownNoteEditor,
} from "@/components/markdown-editor";
import { EntityDetailPanel } from "@/components/entity-detail-panel";
import { ConnectedNotesPanel } from "@/components/connected-notes-panel";
import { useNotesPageController } from "./_hooks/useNotesPageController";
import { NotesSidebar } from "./_components/NotesSidebar";
import { NoteEditorHeader } from "./_components/NoteEditorHeader";
import { NoteAttachmentsStrip } from "./_components/NoteAttachmentsStrip";
import { NotesEmptyState } from "./_components/NotesEmptyState";
import { DatePickerModal } from "./_components/DatePickerModal";
import { FilePreviewModal } from "./_components/FilePreviewModal";
import { FolderDialog } from "./_components/FolderDialog";
import { RenameDialog } from "./_components/RenameDialog";
import { WikilinkHoverCard } from "./_components/WikilinkHoverCard";

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
    handleEntityClick,
    handleNoteSelect,
    handleCreateNote,
    handleDeleteNote,
    handleReingestVault,
    handleDeleteVaultAttachment,
  } = useNotesPageController();

  const selectedNote = selection.selectedNote;

  return (
    <div className="flex h-screen w-full overflow-hidden bg-black">
      <ShaderBackground />
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
        mediaFiles={vault.mediaFiles}
        attachmentFiles={vault.attachmentFiles}
        collapsedFolders={vault.collapsedFolders}
        selectedNoteId={selectedNote?.id ?? null}
        selectedNoteIds={batch.selectedNoteIds}
        batchDeleting={batch.batchDeleting}
        dragNoteId={vault.dragNoteId}
        dragFileRel={vault.dragFileRel}
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

      <div className="relative z-10 flex min-h-0 flex-1 flex-col">
        {selectedNote ? (
          <>
            <NoteEditorHeader
              selectedNote={selectedNote}
              isSaving={autosave.isSaving}
              isUploading={media.isUploading}
              isRecording={media.isRecording}
              showConnectedPanel={showConnectedPanel}
              onTitleChange={selection.handleTitleChange}
              onIngest={ingest.handleIngestNote}
              onDismissFailure={ingest.handleDismissFailure}
              onToggleDatePicker={() =>
                media.setShowDatePicker(!media.showDatePicker)
              }
              onToggleRecording={
                media.isRecording ? media.stopRecording : media.startRecording
              }
              onToggleConnectedPanel={() => setShowConnectedPanel((v) => !v)}
              onDelete={handleDeleteNote}
            />

            <NoteAttachmentsStrip
              content={selectedNote.content}
              onFileClick={media.handleFileClick}
              onDeleteFile={media.handleDeleteFile}
            />

            <div className="flex min-h-0 flex-1 overflow-hidden">
              <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
                <MarkdownNoteEditor
                  key={selectedNote.id}
                  ref={editorRef}
                  value={selectedNote.content}
                  onChange={selection.handleContentChange}
                  onEntityClick={handleEntityClick}
                  onWikilinkClick={wikilink.handleWikilinkClick}
                  onWikilinkHover={wikilink.handleWikilinkHover}
                  onWikilinkLeave={wikilink.handleWikilinkLeave}
                  onAttachFile={media.handleFileAttach}
                  onDropFiles={media.attachFiles}
                  attachDisabled={media.isUploading}
                  kb={currentKB}
                  notes={list.notes}
                  placeholder="Start writing..."
                  className="h-full w-full"
                />
              </div>
              {showConnectedPanel && (
                <ConnectedNotesPanel
                  noteId={selectedNote.id}
                  noteContent={selectedNote.content}
                  kb={currentKB}
                  onClose={() => setShowConnectedPanel(false)}
                  onSelectNote={(id) => {
                    const match = list.notes.find((n) => n.id === id);
                    if (match) void handleNoteSelect(match);
                  }}
                  onSelectEntity={(nodeId, name) => {
                    handleEntityClick(nodeId, name);
                  }}
                />
              )}
            </div>

            <EntityDetailPanel
              nodeId={entityPanelNodeId}
              name={entityPanelName}
              kb={currentKB}
              onClose={() => setEntityPanelNodeId(null)}
            />
          </>
        ) : (
          <NotesEmptyState
            variant="editor"
            isSaving={autosave.isSaving}
            onCreateNote={() => void handleCreateNote()}
          />
        )}
      </div>

      <AnimatePresence>
        {media.showDatePicker && selectedNote && (
          <DatePickerModal
            createdAt={selectedNote.created_at}
            pendingDateChange={media.pendingDateChange}
            onPendingChange={media.setPendingDateChange}
            onClose={() => void media.handleCloseDatePicker()}
          />
        )}
      </AnimatePresence>

      <AnimatePresence>
        {media.filePreview && (
          <FilePreviewModal
            filePreview={media.filePreview}
            currentKB={currentKB}
            onClose={() => media.setFilePreview(null)}
            onReveal={media.handleRevealPreviewFile}
          />
        )}
      </AnimatePresence>

      {vault.folderDialog && (
        <FolderDialog
          folderDialog={vault.folderDialog}
          vaultName={vault.vaultName}
          onNameChange={(name) =>
            vault.setFolderDialog((d) => (d ? { ...d, name } : d))
          }
          onSubmit={vault.submitFolderDialog}
          onCancel={() => vault.setFolderDialog(null)}
        />
      )}

      {vault.renameDialog && (
        <RenameDialog
          renameDialog={vault.renameDialog}
          onNameChange={(name) =>
            vault.setRenameDialog((d) => (d ? { ...d, name } : d))
          }
          onSubmit={vault.submitRenameDialog}
          onCancel={() => vault.setRenameDialog(null)}
        />
      )}

      {wikilink.wikilinkPreview && (
        <WikilinkHoverCard preview={wikilink.wikilinkPreview} />
      )}
    </div>
  );
}
