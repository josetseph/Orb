import {
  useCallback,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type RefObject,
  type SetStateAction,
} from "react";
import { api } from "@/lib/api";
import type { MarkdownNoteEditorHandle } from "@/components/markdown-editor/MarkdownNoteEditor";
import { revealInFolder } from "@/lib/desktop";
import {
  encodeFileUrl,
  isAudioUrl,
  isImageUrl,
  isPdfUrl,
  isVideoUrl,
  resolveFileUrl,
} from "@/lib/utils";
import type { FilePreview, Note } from "@/lib/types";
import { errMessage } from "@/lib/utils";
import { pickSupportedAudioMimeType } from "../_lib/media-recorder";
import type { ProcessedFilter } from "../_lib/types";

type UseNoteMediaArgs = {
  currentKB: string;
  selectedNote: Note | null;
  editorRef: RefObject<MarkdownNoteEditorHandle | null>;
  handleContentChange: (content: string) => void;
  refreshSelectedNote: (noteId: string) => Promise<void>;
  fetchNotes: (search?: string, filter?: ProcessedFilter) => Promise<void>;
  searchQuery: string;
  processedFilter: ProcessedFilter;
  setSelectedNote: Dispatch<SetStateAction<Note | null>>;
  contentBeforeEditRef: MutableRefObject<string>;
  titleBeforeEditRef: MutableRefObject<string>;
  setIsSaving: Dispatch<SetStateAction<boolean>>;
  refreshVaultFiles: () => Promise<void>;
};

export function useNoteMedia({
  currentKB,
  selectedNote,
  editorRef,
  handleContentChange,
  refreshSelectedNote,
  fetchNotes,
  searchQuery,
  processedFilter,
  setSelectedNote,
  contentBeforeEditRef,
  titleBeforeEditRef,
  setIsSaving,
  refreshVaultFiles,
}: UseNoteMediaArgs) {
  const [isUploading, setIsUploading] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [filePreview, setFilePreview] = useState<FilePreview | null>(null);
  const [showDatePicker, setShowDatePicker] = useState(false);
  const [pendingDateChange, setPendingDateChange] = useState<string | null>(
    null,
  );
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  const handleDeleteFile = useCallback(
    async (fileUrl: string, _markdownText: string) => {
      if (!selectedNote) return;
      if (
        !confirm(
          "Delete this attachment from the vault and remove it from this note?",
        )
      ) {
        return;
      }
      try {
        // /vault/delete takes a decoded vault-relative path.
        const resolved = resolveFileUrl(fileUrl, currentKB);
        const relPath = resolved.startsWith("/vault-files/")
          ? resolved
              .split("/")
              .slice(3) // '', 'vault-files', kb, ...path
              .map((p) => {
                try {
                  return decodeURIComponent(p);
                } catch {
                  return p;
                }
              })
              .join("/")
          : fileUrl;

        await api.deleteVaultFile(relPath, currentKB);
        // Server already stripped markdown links across notes
        await refreshSelectedNote(selectedNote.id);
        await fetchNotes(searchQuery, processedFilter);
      } catch (error) {
        console.error("Error deleting file:", error);
        alert("Failed to delete attachment.");
      }
    },
    [
      selectedNote,
      currentKB,
      refreshSelectedNote,
      fetchNotes,
      searchQuery,
      processedFilter,
    ],
  );

  const attachFiles = useCallback(
    async (files: FileList | File[]) => {
      if (!selectedNote) return;
      const list = Array.from(files);
      if (list.length === 0) return;

      try {
        setIsUploading(true);
        const chunks: string[] = [];
        for (const file of list) {
          const response = await api.upload(file, currentKB);
          const linkUrl = encodeFileUrl(response.url);
          if (!linkUrl) throw new Error("Upload returned no URL");

          const lower = file.name.toLowerCase();
          const isImage =
            file.type.startsWith("image/") ||
            /\.(jpg|jpeg|png|gif|webp|svg)$/i.test(lower);

          // Images stay as markdown image embeds so the editor previews them;
          // ingestion also discovers ![alt](/vault-files/...) for description.
          chunks.push(
            isImage
              ? `![${file.name}](${linkUrl})`
              : `[📎 ${file.name}](${linkUrl})`,
          );
        }
        const markdownLink = `\n${chunks.join("\n")}\n`;
        if (editorRef.current) {
          editorRef.current.insertAtCursor(markdownLink);
        } else {
          handleContentChange((selectedNote?.content ?? "") + markdownLink);
        }
        await refreshVaultFiles();
      } catch (error) {
        console.error("Error uploading file:", error);
        alert(errMessage(error, "Failed to upload file"));
      } finally {
        setIsUploading(false);
      }
    },
    [
      selectedNote,
      currentKB,
      editorRef,
      handleContentChange,
      refreshVaultFiles,
    ],
  );

  const handleFileAttach = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files?.length) return;
      await attachFiles(files);
      e.target.value = "";
    },
    [attachFiles],
  );

  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

      const mimeType = pickSupportedAudioMimeType();

      const mediaRecorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];

      mediaRecorder.ondataavailable = (event) => {
        audioChunksRef.current.push(event.data);
      };

      mediaRecorder.onstop = async () => {
        const actualMime = mediaRecorder.mimeType || mimeType || "audio/webm";
        const ext = actualMime.includes("mp4") ? "m4a" : "webm";
        const audioBlob = new Blob(audioChunksRef.current, {
          type: actualMime,
        });
        const audioFile = new File(
          [audioBlob],
          `recording-${Date.now()}.${ext}`,
          { type: actualMime },
        );

        try {
          setIsUploading(true);
          const response = await api.upload(audioFile, currentKB);

          const markdownLink = `[🎤 Voice Recording](${encodeFileUrl(response.url)})`;
          if (editorRef.current) {
            editorRef.current.insertAtCursor(markdownLink);
          } else if (selectedNote) {
            handleContentChange(selectedNote.content + markdownLink);
          }
        } catch (error) {
          console.error("Error uploading recording:", error);
          alert("Failed to upload recording");
        } finally {
          setIsUploading(false);
        }

        stream.getTracks().forEach((track) => track.stop());
      };

      mediaRecorder.start();
      setIsRecording(true);
    } catch (error) {
      console.error("Error starting recording:", error);
      alert("Failed to access microphone");
    }
  }, [currentKB, editorRef, selectedNote, handleContentChange]);

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
    }
  }, [isRecording]);

  const handleFileClick = useCallback(
    (url: string, filename: string) => {
      const resolvedUrl = resolveFileUrl(url, currentKB);
      let type: FilePreview["type"] = "other";

      if (isImageUrl(resolvedUrl) || isImageUrl(filename)) {
        type = "image";
      } else if (isPdfUrl(resolvedUrl) || isPdfUrl(filename)) {
        type = "pdf";
      } else if (isVideoUrl(resolvedUrl) || isVideoUrl(filename)) {
        type = "video";
      } else if (isAudioUrl(resolvedUrl) || isAudioUrl(filename)) {
        type = "audio";
      }

      setFilePreview({ url: resolvedUrl, filename, type });
    },
    [currentKB],
  );

  const handleRevealPreviewFile = useCallback(async () => {
    if (!filePreview) return;
    try {
      const { local_path } = await api.resolveVaultLocalPath(
        filePreview.url,
        currentKB,
      );
      const ok = await revealInFolder(local_path);
      if (!ok) {
        // Browser / no desktop bridge — fall back to opening the file URL
        window.open(filePreview.url, "_blank", "noopener,noreferrer");
      }
    } catch (error) {
      console.error("Reveal failed:", error);
      alert(`Could not reveal this file on disk: ${error instanceof Error ? error.message : "unknown error"}`);
    }
  }, [filePreview, currentKB]);

  const handleDateChange = useCallback(
    async (dateString: string) => {
      if (!selectedNote) return;

      try {
        setIsSaving(true);

        // Save to backend with the new date
        await api.updateNote(
          selectedNote.id,
          selectedNote.content,
          dateString,
          currentKB,
          selectedNote.title || undefined,
        );

        // Refresh the note from backend to get the updated data
        const updatedNote = await api.getNote(selectedNote.id, currentKB);
        setSelectedNote(updatedNote);
        contentBeforeEditRef.current = updatedNote.content;
        titleBeforeEditRef.current = updatedNote.title || "";

        // Refresh notes list to reflect the new date in sidebar
        await fetchNotes(searchQuery, processedFilter);
      } catch (error) {
        console.error("Error updating date:", error);
        alert("Failed to update note date. Please try again.");
      } finally {
        setIsSaving(false);
      }
    },
    [
      selectedNote,
      setIsSaving,
      currentKB,
      setSelectedNote,
      contentBeforeEditRef,
      titleBeforeEditRef,
      fetchNotes,
      searchQuery,
      processedFilter,
    ],
  );

  const handleCloseDatePicker = useCallback(async () => {
    // Save the date if it was changed
    if (pendingDateChange && selectedNote) {
      await handleDateChange(pendingDateChange);
    }
    setPendingDateChange(null);
    setShowDatePicker(false);
  }, [pendingDateChange, selectedNote, handleDateChange]);

  return {
    isUploading,
    isRecording,
    filePreview,
    setFilePreview,
    showDatePicker,
    setShowDatePicker,
    pendingDateChange,
    setPendingDateChange,
    handleDeleteFile,
    attachFiles,
    handleFileAttach,
    startRecording,
    stopRecording,
    handleFileClick,
    handleRevealPreviewFile,
    handleDateChange,
    handleCloseDatePicker,
  };
}
