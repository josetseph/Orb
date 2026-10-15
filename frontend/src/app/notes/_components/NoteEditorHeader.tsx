import { useEffect, useRef, useState } from "react";
import {
  Calendar,
  FolderOpen,
  Loader2,
  Mic,
  FileDown,
  MoreHorizontal,
  X,
  ArrowLeft,
  ArrowRight,
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
  /** Titles of the notes Back / Forward go to; null when there is none. */
  backTitle: string | null;
  forwardTitle: string | null;
  onBack: () => void;
  onForward: () => void;
  /** Folder segment clicked in the breadcrumb: reveal it in the explorer. */
  onRevealFolder: (path: string) => void;
};

function folderParts(relPath?: string | null): string[] {
  const parts = (relPath || "").split("/").filter(Boolean);
  parts.pop();
  return parts;
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
  forwardTitle,
  onBack,
  onForward,
  onRevealFolder,
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
    <div className="flex shrink-0 items-center gap-1.5 px-3 py-1.5">
      <button
        type="button"
        onClick={onBack}
        disabled={backTitle === null}
        title={backTitle === null ? "Navigate back" : `Back to “${backTitle}” (⌘⌥←)`}
        aria-label="Navigate back"
        className="btn btn-ghost btn-icon h-7 w-7 shrink-0 disabled:opacity-30"
      >
        <ArrowLeft className="h-4 w-4" />
      </button>
      <button
        type="button"
        onClick={onForward}
        disabled={forwardTitle === null}
        title={forwardTitle === null ? "Navigate forward" : `Forward to “${forwardTitle}” (⌘⌥→)`}
        aria-label="Navigate forward"
        className="btn btn-ghost btn-icon h-7 w-7 shrink-0 disabled:opacity-30"
      >
        <ArrowRight className="h-4 w-4" />
      </button>
      <nav className="ml-2 flex min-w-0 flex-1 items-center gap-1.5 text-[13px] text-n-500" aria-label="Note location">
        {folderParts(selectedNote.rel_path).map((part, i, all) => (
          <span key={i} className="flex min-w-0 shrink items-center gap-1.5">
            <button
              type="button"
              onClick={() => onRevealFolder(all.slice(0, i + 1).join("/"))}
              className="truncate hover:text-text"
              title="Show in file explorer"
            >
              {part}
            </button>
            <span className="opacity-50">/</span>
          </span>
        ))}
        <span className="truncate text-n-200">{selectedNote.title || "Untitled"}</span>
      </nav>

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
        aria-label="Attach file"
        className="btn btn-ghost btn-icon h-7 w-7 text-n-400"
      >
        <Paperclip className="h-4 w-4" />
      </button>

      <button
        type="button"
        onClick={onToggleRecording}
        disabled={isUploading}
        title={isRecording ? "Stop recording" : "Record voice note"}
        aria-label={isRecording ? "Stop recording" : "Record voice note"}
        className={cn(
          isRecording
            ? "btn border-danger/60 bg-danger/10 text-danger-text"
            : "btn btn-ghost btn-icon h-7 w-7 text-n-400",
        )}
      >
        {isRecording ? (
          <>
            <span className="dot animate-blink bg-danger" />
            <Square className="h-3 w-3" /> Stop
          </>
        ) : (
          <Mic className="h-4 w-4" />
        )}
      </button>

      <button
        type="button"
        onClick={onIngest}
        disabled={isSaving || busy || !selectedNote.content.trim()}
        title="Extract entities and links into the graph"
        className="btn btn-primary btn-sm mr-1 min-w-[88px]"
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
          title="More options"
          className="btn btn-ghost btn-icon h-7 w-7 text-n-400"
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
        title={showConnectedPanel ? "Hide connections" : "Show connections"}
        className={cn(
          "btn btn-ghost btn-icon h-7 w-7",
          showConnectedPanel ? "text-accent" : "text-n-400",
        )}
      >
        <Network className="h-4 w-4" />
      </button>
    </div>
  );
}
