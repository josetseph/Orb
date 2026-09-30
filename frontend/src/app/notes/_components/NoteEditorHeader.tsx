import { useEffect, useRef, useState } from "react";
import {
  Calendar,
  Check,
  FolderOpen,
  Loader2,
  Mic,
  FileDown,
  MoreHorizontal,
  X,
  ArrowLeft,
  Network,
  Paperclip,
  RefreshCw,
  Square,
  Trash2,
  Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { revealInFolder, revealInFolderLabel } from "@/lib/desktop";
import type { Note } from "@/lib/types";
import { getProcessingStage, isActiveProcessingNote } from "../_lib/processing-status";


type NoteEditorHeaderProps = {
  selectedNote: Note;
  currentKB: string;
  isSaving: boolean;
  isUploading: boolean;
  isRecording: boolean;
  showConnectedPanel: boolean;
  onIngest: () => void;
  onCancelIngest: () => void;
  onAttachFile: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onToggleDatePicker: () => void;
  onToggleRecording: () => void;
  onToggleConnectedPanel: () => void;
  onDelete: () => void;
  onExportPdf: () => void;
  /** Title of the note Back returns to; null when there is no history. */
  backTitle: string | null;
  onBack: () => void;
};

function folderOf(relPath?: string | null): string {
  const parts = (relPath || "").split("/");
  parts.pop();
  return parts.join("/");
}

export function NoteEditorHeader({
  selectedNote,
  currentKB,
  isSaving,
  isUploading,
  isRecording,
  showConnectedPanel,
  onIngest,
  onCancelIngest,
  onAttachFile,
  onToggleDatePicker,
  onToggleRecording,
  onToggleConnectedPanel,
  onDelete,
  onExportPdf,
  backTitle,
  onBack,
}: NoteEditorHeaderProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const busy = isActiveProcessingNote(selectedNote);

  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [menuOpen]);

  const reveal = async () => {
    setMenuOpen(false);
    if (!selectedNote.rel_path) return;
    try {
      const { local_path } = await api.resolveVaultLocalPath(selectedNote.rel_path, currentKB);
      await revealInFolder(local_path);
    } catch {
      /* browser build — nothing to reveal into */
    }
  };

  const ingestLabel = busy
    ? getProcessingStage(selectedNote)
    : selectedNote.processed
      ? "Re-ingest"
      : selectedNote.failed
        ? "Retry"
        : "Ingest";

  return (
    <div className="flex shrink-0 items-center gap-1.5 border-b border-n-900 px-5 py-2.5">
      <button
        type="button"
        onClick={onBack}
        disabled={backTitle === null}
        title={backTitle === null ? "No previous note" : `Back to “${backTitle}” (⌘⌥←)`}
        aria-label="Back to previous note"
        className="btn btn-ghost btn-icon h-7 w-7 shrink-0 disabled:opacity-30"
      >
        <ArrowLeft className="h-4 w-4" />
      </button>
      <div className="flex min-w-0 flex-1 items-center gap-2 text-[12px] text-n-500">
        <FolderOpen className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate">{folderOf(selectedNote.rel_path) || "Vault"}</span>
        <span className="opacity-50">/</span>
        <span className="truncate text-n-300">{selectedNote.title || "Untitled"}</span>
      </div>

      <span className="mr-1 flex items-center gap-1.5 text-[12px] text-n-500">
        {isSaving || isUploading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Check className="h-3.5 w-3.5" />
        )}
        {isSaving ? "Saving…" : isUploading ? "Uploading…" : "Saved"}
      </span>


      <input
        ref={fileRef}
        type="file"
        multiple
        className="hidden"
        onChange={onAttachFile}
        disabled={isUploading}
      />
      <button
        type="button"
        onClick={() => fileRef.current?.click()}
        disabled={isUploading}
        title="Attach photo, PDF or file"
        className="btn btn-secondary"
      >
        <Paperclip className="h-3.5 w-3.5" /> Attach
      </button>

      <button
        type="button"
        onClick={onToggleRecording}
        disabled={isUploading}
        title={isRecording ? "Stop recording" : "Record voice note"}
        className={cn(
          "btn",
          isRecording
            ? "border-danger/60 bg-danger/10 text-danger-text"
            : "btn-secondary",
        )}
      >
        {isRecording ? (
          <>
            <span className="dot animate-blink bg-danger" />
            <Square className="h-3 w-3" /> Stop
          </>
        ) : (
          <>
            <Mic className="h-3.5 w-3.5" /> Record
          </>
        )}
      </button>

      <button
        type="button"
        onClick={onIngest}
        disabled={isSaving || busy || !selectedNote.content.trim()}
        title="Extract entities and links into the graph"
        className="btn btn-primary min-w-[96px]"
      >
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : selectedNote.processed ? (
          <RefreshCw className="h-3.5 w-3.5" />
        ) : (
          <Zap className="h-3.5 w-3.5" />
        )}
        <span className="max-w-[14rem] truncate">{ingestLabel}</span>
      </button>
      {busy && (
        <button
          type="button"
          onClick={onCancelIngest}
          title="Stop this ingestion"
          className="btn btn-secondary btn-icon w-8"
          aria-label="Cancel ingestion"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}

      <div ref={menuRef} className="relative">
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          title="More"
          className="btn btn-secondary btn-icon"
        >
          <MoreHorizontal className="h-4 w-4" />
        </button>
        {menuOpen && (
          <div className="popover absolute right-0 top-[34px] z-40 min-w-[200px]">
            <button
              type="button"
              className="menu-item"
              onClick={() => {
                setMenuOpen(false);
                onToggleDatePicker();
              }}
            >
              <Calendar className="h-3.5 w-3.5" /> Change date…
            </button>
            <button
              type="button"
              className="menu-item"
              onClick={() => {
                setMenuOpen(false);
                onExportPdf();
              }}
            >
              <FileDown className="h-3.5 w-3.5" /> Export as PDF…
            </button>
            <button type="button" className="menu-item" onClick={() => void reveal()}>
              <FolderOpen className="h-3.5 w-3.5" /> {revealInFolderLabel()}
            </button>
            <div className="my-1 h-px bg-divider" />
            <button
              type="button"
              className="menu-item text-danger-text"
              onClick={() => {
                setMenuOpen(false);
                onDelete();
              }}
            >
              <Trash2 className="h-3.5 w-3.5" /> Delete
            </button>
          </div>
        )}
      </div>

      <button
        type="button"
        onClick={onToggleConnectedPanel}
        title="Note graph"
        className={cn(
          "btn btn-icon",
          showConnectedPanel ? "border-accent-700 text-accent" : "btn-secondary",
        )}
      >
        <Network className="h-4 w-4" />
      </button>
    </div>
  );
}
