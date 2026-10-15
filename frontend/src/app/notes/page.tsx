import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, AudioLines, FileText, Film, Image as ImageIcon, Loader2, Plus, X } from "lucide-react";
import type { Note } from "@/lib/types";
import { cn, isAudioUrl, isImageUrl, isVideoUrl } from "@/lib/utils";
import { useNotes } from "@/lib/notes-context";
import MarkdownNoteEditor from "@/components/markdown-editor/MarkdownNoteEditor";
import { NotePrintView, type PrintJob } from "@/components/note-print-view";
import { EntityDetailPanel } from "@/components/entity-detail-panel";
import { ConnectedNotesPanel } from "@/components/connected-notes-panel";
import { ResizeHandle, useStored } from "@/components/resize-handle";
import { parseNoteAttachments } from "./_lib/parse-note-attachments";
import { isActiveProcessingNote } from "./_lib/processing-status";
import { NoteEditorHeader } from "./_components/NoteEditorHeader";
import { NotesEmptyState } from "./_components/NotesEmptyState";
import { noteStatus } from "./_components/NoteStatusBadge";
import { DatePickerModal } from "./_components/DatePickerModal";
import { WikilinkHoverCard } from "./_components/WikilinkHoverCard";

function AttachmentIcon({ url }: { url: string }) {
  const cls = "h-4 w-4 text-accent-300";
  if (isAudioUrl(url)) return <AudioLines className={cls} />;
  if (isImageUrl(url)) return <ImageIcon className={cls} />;
  if (isVideoUrl(url)) return <Film className={cls} />;
  return <FileText className={cls} />;
}

const PANEL_WIDTH = 400;

/** Open notes, one tab each; ⌘-click in the explorer or ⌘T opens another. */
function TabBar({ titleOf }: { titleOf: (id: string) => string }) {
  const { tabs } = useNotes();
  return (
    <div className="flex h-9 shrink-0 items-end gap-0.5 overflow-x-auto border-b border-n-900 bg-bg-deep/40 px-2">
      {tabs.ids.map((id, i) => {
        const active = i === tabs.active;
        return (
          <div
            key={id}
            role="tab"
            aria-selected={active}
            onClick={() => void tabs.activate(i)}
            onAuxClick={(e) => {
              if (e.button === 1) void tabs.close(i);
            }}
            className={cn(
              "group/tab flex h-[30px] min-w-[90px] max-w-[220px] flex-1 cursor-default items-center gap-1.5 rounded-t-md px-3 text-[12.5px]",
              active ? "bg-bg text-text shadow-sm" : "text-n-500 hover:bg-n-900 hover:text-n-300",
            )}
            title={titleOf(id)}
          >
            <span className="min-w-0 flex-1 truncate">{titleOf(id)}</span>
            <button
              type="button"
              aria-label="Close tab"
              onClick={(e) => {
                e.stopPropagation();
                void tabs.close(i);
              }}
              className={cn(
                "grid h-4 w-4 shrink-0 place-items-center rounded text-n-500 hover:bg-n-800 hover:text-text",
                !active && "opacity-0 group-hover/tab:opacity-100",
              )}
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        );
      })}
      <button
        type="button"
        onClick={() => void tabs.create()}
        title="New tab (⌘T)"
        aria-label="New tab"
        className="mb-[3px] grid h-6 w-6 shrink-0 place-items-center rounded text-n-500 hover:bg-n-900 hover:text-text"
      >
        <Plus className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function countWords(text: string): number {
  return text.split(/\s+/).filter(Boolean).length;
}

/** Obsidian-style bar in the bottom-right corner: graph status, date, counts, save state. */
function StatusBar({ note, onDate }: { note: Note; onDate: () => void }) {
  const { autosave, media } = useNotes();
  const status = noteStatus(note);
  const body = note.content ?? "";
  const words = countWords(body);
  return (
    <div className="pointer-events-auto absolute bottom-0 right-0 z-10 flex items-center gap-3 rounded-tl-md border-l border-t border-n-900 bg-bg-deep/90 px-3 py-1 text-[11.5px] text-n-500 backdrop-blur">
      <span className={cn("inline-flex items-center gap-1.5", status.text)}>
        {status.key === "ingesting" ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <span className={cn("dot h-1.5 w-1.5", status.dot)} />
        )}
        {status.label}
      </span>
      <button type="button" onClick={onDate} className="hover:text-text" title="Change date">
        {new Date(note.created_at).toLocaleDateString("en-US", {
          month: "short",
          day: "numeric",
          year: "numeric",
          hour: "numeric",
          minute: "2-digit",
        })}
      </button>
      <span>
        {words.toLocaleString()} {words === 1 ? "word" : "words"}
      </span>
      <span>
        {body.length.toLocaleString()} {body.length === 1 ? "character" : "characters"}
      </span>
      {(autosave.isSaving || media.isUploading) && (
        <span className="inline-flex items-center gap-1">
          <Loader2 className="h-3 w-3 animate-spin" />
          {autosave.isSaving ? "Saving…" : "Uploading…"}
        </span>
      )}
    </div>
  );
}

export default function NotesPage() {
  const {
    currentKB,
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
    wikilink,
    attachments,
    notesById,
    history,
    openNote,
    handleEntityClick,
    handleCreateNote,
    handleDeleteNote,
  } = useNotes();
  // The note as it was when Export was chosen; cleared once printed.
  const [printJob, setPrintJob] = useState<PrintJob | null>(null);
  // Stable: the print view prints once per job and must not re-run on re-render.
  const clearPrintJob = useCallback(() => setPrintJob(null), []);
  const [panelWidth, setPanelWidth] = useStored("orb:right-panel-width", PANEL_WIDTH);

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

  const titleOf = (id: string) =>
    (id === selectedNoteId ? selectedNote?.title : notesById.get(id)?.title) || "Untitled";

  const busy = selectedNote ? isActiveProcessingNote(selectedNote) : false;
  const noteContent = selectedNote?.content ?? "";
  const noteAttachments = useMemo(() => parseNoteAttachments(noteContent), [noteContent]);
  const panelOpen = Boolean(selectedNote && (showConnectedPanel || entityPanelNodeId));
  const openFromPanel = (id: string) => void openNote(id);

  return (
    <div className="screen flex-col">
      <TabBar titleOf={titleOf} />
      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
        {selectedNote ? (
          <>
            <NoteEditorHeader
              selectedNote={selectedNote}
              currentKB={currentKB}
              isSaving={autosave.isSaving}
              isUploading={media.isUploading}
              isRecording={media.isRecording}
              showConnectedPanel={showConnectedPanel}
              onIngest={ingest.handleIngestNote}
              onCancelIngest={ingest.handleCancelIngest}
              onAttachFile={media.handleFileAttach}
              onToggleDatePicker={() => media.setShowDatePicker(!media.showDatePicker)}
              onToggleRecording={media.isRecording ? media.stopRecording : media.startRecording}
              onToggleConnectedPanel={() => setShowConnectedPanel((v) => !v)}
              onDelete={handleDeleteNote}
              backTitle={history.back}
              forwardTitle={history.forward}
              onBack={() => void history.goBack()}
              onForward={() => void history.goForward()}
              onRevealFolder={(path) => {
                vault.expandFolderAndAncestors(path);
                vault.setSelectedFolder(path);
              }}
              onExportPdf={() =>
                setPrintJob({ title: selectedNote.title ?? "", content: noteContent, createdAt: selectedNote.created_at })
              }
            />
            {printJob && <NotePrintView job={printJob} kb={currentKB} onDone={clearPrintJob} />}

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
              <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
              <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto pb-20 pt-7">
                <div className="flex min-h-0 w-full flex-1 flex-col">
                  {/* The editor spans the pane so its scrollbar sits at the edge;
                      the title block and the text share one centred reading column
                      that grows with the pane (same width rule as .cm-content, and
                      the editor's px-4 so the percentage has the same base). */}
                  <div className="px-4">
                  <div className="mx-auto w-full max-w-[clamp(720px,80%,1100px)] px-3">
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
                      className="mb-4 w-full bg-transparent text-[28px] font-semibold leading-tight tracking-[-0.015em] outline-none placeholder:text-n-700"
                    />
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
                  </div>
                  </div>

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
                    attachmentJobs={attachments.jobs}
                    onProcessAttachment={attachments.start}
                    onCancelAttachment={attachments.cancel}
                    onOpenFile={media.handleFileClick}
                    placeholder="Keep writing… drop a file to embed it, [[ links a note."
                    className="min-h-[50vh] w-full"
                  />
                </div>
              </div>
              <StatusBar note={selectedNote} onDate={() => media.setShowDatePicker(true)} />
              </div>

              {panelOpen && (
                <aside
                  className="relative flex shrink-0 flex-col border-l border-n-900 bg-bg-deep/40"
                  style={{ width: panelWidth }}
                >
                  <ResizeHandle
                    edge="left"
                    width={panelWidth}
                    min={260}
                    max={720}
                    initial={PANEL_WIDTH}
                    onWidth={setPanelWidth}
                  />
                  {entityPanelNodeId ? (
                    <EntityDetailPanel
                      nodeId={entityPanelNodeId}
                      name={entityPanelName}
                      kb={currentKB}
                      onClose={() => setEntityPanelNodeId(null)}
                      onOpenNote={openFromPanel}
                    />
                  ) : (
                    <ConnectedNotesPanel
                      noteId={selectedNote.id}
                      noteContent={selectedNote.content}
                      kb={currentKB}
                      onClose={() => setShowConnectedPanel(false)}
                      onSelectNote={openFromPanel}
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

      {media.showDatePicker && selectedNote && (
        <DatePickerModal
          createdAt={selectedNote.created_at}
          pendingDateChange={media.pendingDateChange}
          onPendingChange={media.setPendingDateChange}
          onClose={() => void media.handleCloseDatePicker()}
        />
      )}

      {wikilink.wikilinkPreview && <WikilinkHoverCard preview={wikilink.wikilinkPreview} />}
    </div>
  );
}
