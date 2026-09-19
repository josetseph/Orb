import { useEffect, useRef, useState } from "react";
import {
  Calendar,
  Check,
  Code,
  Eye,
  FolderOpen,
  Loader2,
  Mic,
  MoreHorizontal,
  X,
  PanelRight,
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

export type ViewMode = "live" | "source";

type NoteEditorHeaderProps = {
  selectedNote: Note;
  currentKB: string;
  isSaving: boolean;
  isUploading: boolean;
  isRecording: boolean;
  showConnectedPanel: boolean;
  viewMode: ViewMode;
  onViewModeChange: (mode: ViewMode) => void;
  onIngest: () => void;
  onCancelIngest: () => void;
  onAttachFile: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onToggleDatePicker: () => void;
  onToggleRecording: () => void;
  onToggleConnectedPanel: () => void;
  onDelete: () => void;
};

function folderOf(relPath?: string | null): string {
  const parts = (relPath || "").replace(/\\/g, "/").split("/");
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
  viewMode,
  onViewModeChange,
  onIngest,
  onCancelIngest,
  onAttachFile,
  onToggleDatePicker,
  onToggleRecording,
  onToggleConnectedPanel,
  onDelete,
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

      <div
        className="seg mr-1"
        title="Live: Markdown renders as you type. Source: plain Markdown. ⌘/ toggles"
      >
        <button
          type="button"
          onClick={() => onViewModeChange("live")}
          className={cn("seg-opt", viewMode === "live" && "seg-opt-active")}
        >
          <Eye className="h-3.5 w-3.5" /> Live
        </button>
        <button
          type="button"
          onClick={() => onViewModeChange("source")}
          className={cn("seg-opt", viewMode === "source" && "seg-opt-active")}
        >
          <Code className="h-3.5 w-3.5" /> Source
        </button>
      </div>

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
        title="Connections panel"
        className={cn(
          "btn btn-icon",
          showConnectedPanel ? "border-accent-700 text-accent" : "btn-secondary",
        )}
      >
        <PanelRight className="h-4 w-4" />
      </button>
    </div>
  );
}
