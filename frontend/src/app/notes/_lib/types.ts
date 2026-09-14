import type { Note } from "@/lib/types";

/** "needs" = everything not yet in the graph (saved, failed, or mid-ingest). */
export type ProcessedFilter = "all" | "needs" | "ingested" | "ingesting" | "saved" | "failed";

export type FolderTreeNode = {
  name: string;
  path: string;
  note?: Note;
  children: FolderTreeNode[];
};

export type VaultFileEntry = {
  name: string;
  rel_path: string;
};

export type WikilinkPreviewState = {
  title: string;
  content: string;
  x: number;
  y: number;
  missing?: boolean;
};

export type FolderDialogState = {
  parent: string;
  name: string;
};

export type RenameDialogState = {
  rel_path: string;
  name: string;
};

export type NoteAttachment = {
  label: string;
  url: string;
  raw: string;
};
