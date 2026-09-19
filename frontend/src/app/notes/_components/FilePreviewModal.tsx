import { FolderOpen, X } from "lucide-react";
import { BlobMediaPlayer } from "@/components/blob-media-player";
import { isDesktopApp, revealInFolderLabel } from "@/lib/desktop";
import type { FilePreview } from "@/lib/types";

type FilePreviewModalProps = {
  filePreview: FilePreview;
  currentKB: string;
  onClose: () => void;
  onReveal: () => void;
};

export function FilePreviewModal({
  filePreview,
  currentKB,
  onClose,
  onReveal,
}: FilePreviewModalProps) {
  const revealLabel = isDesktopApp() ? revealInFolderLabel() : "Open";
  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div
        className="dialog max-h-[90vh] max-w-4xl gap-0 p-0"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-divider px-4 py-3">
          <h2 className="min-w-0 flex-1 truncate text-[14px] font-medium">
            {filePreview.filename}
          </h2>
          <button type="button" onClick={() => void onReveal()} className="btn btn-sm btn-secondary">
            <FolderOpen className="h-3.5 w-3.5" />
            {revealLabel}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="btn btn-sm btn-ghost btn-icon w-6"
            aria-label="Close file preview"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="max-h-[calc(90vh-52px)] overflow-y-auto p-4">
          {filePreview.type === "image" && (
            <img
              src={filePreview.url}
              alt={filePreview.filename}
              className="mx-auto max-w-full rounded-md shadow-sm"
            />
          )}
          {filePreview.type === "pdf" && (
            <iframe
              src={filePreview.url}
              className="h-[70vh] w-full rounded-md bg-bg-deep shadow-sm"
              title="PDF preview"
            />
          )}
          {filePreview.type === "video" && (
            <BlobMediaPlayer
              url={filePreview.url}
              kbId={currentKB}
              kind="video"
              className="mx-auto max-h-[70vh] w-full max-w-full rounded-md bg-bg-deep shadow-sm"
            />
          )}
          {filePreview.type === "audio" && (
            <div className="flex justify-center">
              <BlobMediaPlayer
                url={filePreview.url}
                kbId={currentKB}
                kind="audio"
                className="w-full max-w-2xl"
              />
            </div>
          )}
          {filePreview.type === "other" && (
            <div className="py-6 text-center">
              <p className="mb-4 text-n-500">No preview for this file type.</p>
              <button type="button" onClick={() => void onReveal()} className="btn btn-primary">
                <FolderOpen className="h-4 w-4" />
                {revealLabel}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
