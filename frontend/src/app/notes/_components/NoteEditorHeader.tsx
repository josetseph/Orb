"use client";

import {
  Calendar,
  Database,
  Loader2,
  Mic,
  MicOff,
  Network,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { Note } from "@/lib/types";
import {
  getProcessingLabel,
  getProcessingStage,
  isActiveProcessingNote,
} from "../_lib/processing-status";
import { NoteStatusBadge } from "./NoteStatusBadge";

type NoteEditorHeaderProps = {
  selectedNote: Note;
  isSaving: boolean;
  isUploading: boolean;
  isRecording: boolean;
  showConnectedPanel: boolean;
  onTitleChange: (title: string) => void;
  onIngest: () => void;
  onDismissFailure?: () => void;
  onToggleDatePicker: () => void;
  onToggleRecording: () => void;
  onToggleConnectedPanel: () => void;
  onDelete: () => void;
};

export function NoteEditorHeader({
  selectedNote,
  isSaving,
  isUploading,
  isRecording,
  showConnectedPanel,
  onTitleChange,
  onIngest,
  onDismissFailure,
  onToggleDatePicker,
  onToggleRecording,
  onToggleConnectedPanel,
  onDelete,
}: NoteEditorHeaderProps) {
  return (
    <div className="flex shrink-0 flex-wrap items-start justify-between gap-3 border-b border-white/10 bg-black/50 px-4 py-3 backdrop-blur-xl sm:px-6 sm:py-4">
      <div className="min-w-0 max-w-full flex-1 basis-[12rem]">
        <input
          type="text"
          value={selectedNote.title || ""}
          onChange={(e) => onTitleChange(e.target.value)}
          placeholder="Untitled"
          className="w-full max-w-xl rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm font-medium text-white placeholder-white/25 outline-none transition focus:border-purple-500/50 focus:ring-1 focus:ring-purple-500/30"
        />
        <div className="mt-1.5 flex min-w-0 flex-wrap items-center gap-2">
          <p className="text-xs text-white/50">
            {isSaving ? "Saving…" : isUploading ? "Uploading…" : "Saved"}
          </p>
          <p className="shrink-0 text-xs text-white/40">
            {new Date(selectedNote.created_at).toLocaleDateString("en-US", {
              month: "short",
              day: "numeric",
              year: "numeric",
              hour: "numeric",
              minute: "2-digit",
            })}
          </p>
          <NoteStatusBadge
            note={selectedNote}
            onDismissFailure={onDismissFailure}
          />
        </div>
      </div>
      <div className="flex max-w-full flex-wrap items-center justify-end gap-2">
        <>
          <button
            onClick={onIngest}
            disabled={
              isSaving ||
              !selectedNote?.content.trim() ||
              Boolean(
                selectedNote && isActiveProcessingNote(selectedNote),
              )
            }
            title={
              selectedNote && isActiveProcessingNote(selectedNote)
                ? getProcessingLabel(selectedNote)
                : selectedNote?.processed
                  ? "Re-ingest note"
                  : "Ingest note"
            }
            className="flex h-9 shrink-0 items-center gap-2 rounded-lg bg-linear-to-r from-purple-500 to-pink-500 px-3 text-sm font-medium text-white transition-all hover:from-purple-600 hover:to-pink-600 disabled:cursor-not-allowed disabled:opacity-50 sm:px-4"
          >
            {selectedNote && isActiveProcessingNote(selectedNote) ? (
              <>
                <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                <span className="max-w-[16rem] truncate">
                  {getProcessingStage(selectedNote)}
                </span>
              </>
            ) : isSaving ? (
              <>
                <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                <span className="whitespace-nowrap">Saving…</span>
              </>
            ) : selectedNote?.processed ? (
              <>
                <Database className="h-4 w-4 shrink-0" />
                <span className="whitespace-nowrap">Re-ingest</span>
              </>
            ) : selectedNote?.failed ? (
              <>
                <Database className="h-4 w-4 shrink-0" />
                <span className="whitespace-nowrap">Retry</span>
              </>
            ) : (
              <>
                <Database className="h-4 w-4 shrink-0" />
                <span className="whitespace-nowrap">Ingest</span>
              </>
            )}
          </button>
          <button
            onClick={onToggleDatePicker}
            title="Change date"
            className="flex h-9 items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-2.5 text-sm text-white/80 transition-all hover:bg-white/10 sm:px-4"
          >
            <Calendar className="h-4 w-4 shrink-0" />
            <span className="hidden sm:inline">Date</span>
          </button>
          <div className="hidden h-6 w-px bg-white/10 sm:block" />
          <button
            onClick={onToggleRecording}
            disabled={isUploading}
            title={isRecording ? "Stop recording" : "Record audio"}
            className={cn(
              "flex h-9 items-center gap-2 rounded-xl border px-2.5 text-sm transition-all sm:px-4",
              isRecording
                ? "border-red-500/30 bg-red-500/10 text-red-400 animate-pulse"
                : "border-white/10 bg-white/5 text-white/80 hover:bg-white/10",
            )}
          >
            {isRecording ? (
              <MicOff className="h-4 w-4 shrink-0" />
            ) : (
              <Mic className="h-4 w-4 shrink-0" />
            )}
            <span className="hidden sm:inline">
              {isRecording ? "Stop" : "Record"}
            </span>
          </button>
        </>
        <button
          onClick={onToggleConnectedPanel}
          title="Connected notes"
          className={cn(
            "flex h-9 items-center gap-2 rounded-xl px-2.5 text-sm transition-all sm:px-4",
            showConnectedPanel
              ? "bg-teal-500/25 text-teal-200 border border-teal-500/40"
              : "border border-white/10 bg-white/5 text-white/80 hover:bg-white/10",
          )}
        >
          <Network className="h-4 w-4 shrink-0" />
          <span className="hidden sm:inline">Connected</span>
        </button>
        <button
          onClick={onDelete}
          title="Delete note"
          className="flex h-9 items-center gap-2 rounded-xl border border-red-500/30 bg-red-500/10 px-2.5 text-sm text-red-400 transition-all hover:bg-red-500/20 sm:px-4"
        >
          <Trash2 className="h-4 w-4 shrink-0" />
          <span className="hidden sm:inline">Delete</span>
        </button>
      </div>
    </div>
  );
}
