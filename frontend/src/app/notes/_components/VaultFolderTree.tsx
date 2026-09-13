"use client";

import { useMemo, type DragEvent, type RefObject } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  ChevronRight,
  FileText,
  Folder,
  FolderPlus,
  Paperclip,
  Plus,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { Note } from "@/lib/types";
import { buildFolderTree } from "../_lib/folder-tree";
import type { FolderTreeNode, VaultFileEntry } from "../_lib/types";
import { NoteStatusDot } from "./NoteStatusBadge";
import { VaultFileRow } from "./VaultFileRow";

type VaultFolderTreeProps = {
  notes: Note[];
  vaultFolders: string[];
  vaultName: string;
  mediaFiles: VaultFileEntry[];
  attachmentFiles: VaultFileEntry[];
  collapsedFolders: Set<string>;
  selectedFolder: string;
  selectedNoteId: string | null;
  selectedNoteIds: Set<string>;
  dragNoteId: string | null;
  dragFileRel: string | null;
  /** Scrollable ancestor (the sidebar body) used to window the tree rows. */
  scrollRef: RefObject<HTMLDivElement | null>;
  onToggleFolder: (path: string) => void;
  onSelectFolder: (path: string) => void;
  onNoteSelect: (note: Note) => void;
  onToggleNoteSelected: (noteId: string) => void;
  onCreateNote: (folderPath: string) => void;
  onOpenFolderDialog: (parent: string) => void;
  onMoveNoteToFolder: (noteId: string, folder: string) => void;
  onMoveVaultFile: (fromRel: string, folder: string) => void;
  onDragNoteStart: (noteId: string) => void;
  onDragNoteEnd: () => void;
  onDragFileStart: (relPath: string) => void;
  onDragFileEnd: () => void;
  onFileClick: (relPath: string, name: string) => void;
  onRenameFile: (relPath: string) => void;
  onDeleteVaultAttachment: (relPath: string, name: string) => void;
};

/**
 * Flattened row model. The nested folder tree is linearized (respecting
 * collapsed state) so the list can be virtualized — large vaults previously
 * rendered one DOM node per note and re-built the whole tree per keystroke.
 */
type TreeRow =
  | { key: string; kind: "vault-header" }
  | { key: string; kind: "folder"; node: FolderTreeNode; depth: number }
  | { key: string; kind: "note"; node: FolderTreeNode; depth: number }
  | { key: string; kind: "media"; file: VaultFileEntry; depth: number; dropTarget: string }
  | { key: string; kind: "attachments-header" }
  | { key: string; kind: "attachments-empty" }
  | { key: string; kind: "attachment"; file: VaultFileEntry; depth: number }
  | {
      key: string;
      kind: "attachment-folder";
      path: string;
      name: string;
      depth: number;
      count: number;
    };

export function VaultFolderTree({
  notes,
  vaultFolders,
  vaultName,
  mediaFiles,
  attachmentFiles,
  collapsedFolders,
  selectedFolder,
  selectedNoteId,
  selectedNoteIds,
  dragNoteId,
  dragFileRel,
  scrollRef,
  onToggleFolder,
  onSelectFolder,
  onNoteSelect,
  onToggleNoteSelected,
  onCreateNote,
  onOpenFolderDialog,
  onMoveNoteToFolder,
  onMoveVaultFile,
  onDragNoteStart,
  onDragNoteEnd,
  onDragFileStart,
  onDragFileEnd,
  onFileClick,
  onRenameFile,
  onDeleteVaultAttachment,
}: VaultFolderTreeProps) {
  const attachmentsOpen = !collapsedFolders.has("attachments");

  const rows = useMemo<TreeRow[]>(() => {
    const noteFolders = vaultFolders.filter(
      (f) => f !== "attachments" && !f.startsWith("attachments/"),
    );
    const tree = buildFolderTree(notes, noteFolders);

    const mediaByFolder = new Map<string, VaultFileEntry[]>();
    const rootMedia: VaultFileEntry[] = [];
    for (const f of mediaFiles) {
      const rel = f.rel_path.replace(/\\/g, "/");
      if (rel === "attachments" || rel.startsWith("attachments/")) {
        continue;
      }
      const slash = rel.lastIndexOf("/");
      if (slash < 0) {
        rootMedia.push(f);
        continue;
      }
      const parent = rel.slice(0, slash);
      const list = mediaByFolder.get(parent) || [];
      list.push(f);
      mediaByFolder.set(parent, list);
    }

    const out: TreeRow[] = [{ key: "__vault__", kind: "vault-header" }];

    const walk = (node: FolderTreeNode, depth: number) => {
      if (!node.note) {
        out.push({ key: `folder:${node.path}`, kind: "folder", node, depth });
        if (collapsedFolders.has(node.path)) return;
        for (const child of node.children) walk(child, depth + 1);
        for (const f of mediaByFolder.get(node.path) || []) {
          out.push({
            key: `media:${f.rel_path}`,
            kind: "media",
            file: f,
            depth: depth + 1,
            dropTarget: node.path,
          });
        }
        return;
      }
      out.push({ key: `note:${node.note.id}`, kind: "note", node, depth });
    };

    for (const n of tree) walk(n, 0);
    for (const f of rootMedia) {
      out.push({
        key: `media:${f.rel_path}`,
        kind: "media",
        file: f,
        depth: 0,
        dropTarget: "",
      });
    }

    out.push({ key: "__attachments__", kind: "attachments-header" });
    if (attachmentsOpen) {
      // Attachments nest like note folders: files keyed by the folder holding
      // them, plus any folder that exists but is still empty.
      const filesByFolder = new Map<string, VaultFileEntry[]>();
      const folders = new Set<string>();
      for (const f of attachmentFiles) {
        const rel = f.rel_path.replace(/\\/g, "/");
        const parent = rel.slice(0, rel.lastIndexOf("/"));
        const list = filesByFolder.get(parent) || [];
        list.push(f);
        filesByFolder.set(parent, list);
        let walk = parent;
        while (walk && walk !== "attachments") {
          folders.add(walk);
          walk = walk.slice(0, walk.lastIndexOf("/"));
        }
      }
      for (const f of vaultFolders) {
        if (f.startsWith("attachments/")) folders.add(f);
      }

      const emit = (parent: string, depth: number) => {
        const children = [...folders]
          .filter((f) => f.slice(0, f.lastIndexOf("/")) === parent)
          .sort();
        for (const child of children) {
          const own = filesByFolder.get(child) || [];
          out.push({
            key: `attachment-folder:${child}`,
            kind: "attachment-folder",
            path: child,
            name: child.slice(child.lastIndexOf("/") + 1),
            depth,
            count: own.length,
          });
          if (collapsedFolders.has(child)) continue;
          emit(child, depth + 1);
          for (const f of own) {
            out.push({
              key: `attachment:${f.rel_path}`,
              kind: "attachment",
              file: f,
              depth: depth + 1,
            });
          }
        }
      };

      emit("attachments", 0);
      for (const f of filesByFolder.get("attachments") || []) {
        out.push({
          key: `attachment:${f.rel_path}`,
          kind: "attachment",
          file: f,
          depth: 0,
        });
      }
      if (attachmentFiles.length === 0 && folders.size === 0) {
        out.push({ key: "__attachments-empty__", kind: "attachments-empty" });
      }
    }
    return out;
  }, [
    notes,
    vaultFolders,
    mediaFiles,
    attachmentFiles,
    collapsedFolders,
    attachmentsOpen,
  ]);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 28,
    overscan: 12,
  });

  const acceptVaultDrop = (e: DragEvent, folderPath: string) => {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = "move";
    const noteId = e.dataTransfer.getData("text/note-id") || dragNoteId;
    const fileRel = e.dataTransfer.getData("text/vault-file") || dragFileRel;
    onDragNoteEnd();
    onDragFileEnd();
    if (noteId) void onMoveNoteToFolder(noteId, folderPath);
    else if (fileRel) void onMoveVaultFile(fileRel, folderPath);
  };

  const allowVaultDragOver = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = "move";
  };

  const folderActionButtons = (folderPath: string) => (
    <div className="flex shrink-0 items-center gap-0.5">
      <button
        type="button"
        title="New folder"
        onClick={(e) => {
          e.stopPropagation();
          onOpenFolderDialog(folderPath);
        }}
        className="flex h-6 w-6 items-center justify-center rounded text-white/45 hover:bg-white/10 hover:text-white/90"
      >
        <FolderPlus className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        title="New note"
        onClick={(e) => {
          e.stopPropagation();
          onSelectFolder(folderPath);
          void onCreateNote(folderPath);
        }}
        className="flex h-6 w-6 items-center justify-center rounded text-white/45 hover:bg-white/10 hover:text-white/90"
      >
        <Plus className="h-3.5 w-3.5" />
      </button>
    </div>
  );

  /** Ancestor indent guides, one vertical segment per depth level. */
  const indentGuides = (depth: number) =>
    depth > 0 ? (
      <>
        {Array.from({ length: depth }, (_, i) => (
          <div
            key={i}
            className="pointer-events-none absolute bottom-0 top-0 w-px bg-white/10"
            style={{ left: 10 + i * 12 }}
          />
        ))}
      </>
    ) : null;

  const renderRow = (row: TreeRow) => {
    switch (row.kind) {
      case "vault-header":
        return (
          <div
            className={cn(
              "group/folder rounded-md pb-1",
              (dragNoteId || dragFileRel) && "ring-1 ring-teal-500/25",
            )}
            onDragOver={allowVaultDragOver}
            onDrop={(e) => acceptVaultDrop(e, "")}
          >
            <div
              className={cn(
                "flex w-full items-center gap-0.5 rounded-md pr-1 text-[12px]",
                selectedFolder === ""
                  ? "bg-teal-500/15 text-teal-200"
                  : "text-white/55 hover:bg-white/5 hover:text-white/80",
              )}
            >
              <button
                type="button"
                onClick={() => onSelectFolder("")}
                className="flex min-w-0 flex-1 items-center gap-1.5 px-2 py-1 text-left"
              >
                <Folder className="h-3.5 w-3.5 shrink-0 text-teal-300/90" />
                <span className="truncate font-medium">{vaultName}</span>
                {selectedFolder === "" && (
                  <span className="ml-1 truncate text-[10px] text-teal-300/70">
                    new notes here
                  </span>
                )}
              </button>
              {folderActionButtons("")}
            </div>
          </div>
        );

      case "folder": {
        const { node, depth } = row;
        const isOpen = !collapsedFolders.has(node.path);
        const isSelected = selectedFolder === node.path;
        return (
          <div
            className={cn(
              "relative group/folder rounded-md",
              (dragNoteId || dragFileRel) && "ring-1 ring-teal-500/25",
            )}
            onDragOver={allowVaultDragOver}
            onDrop={(e) => acceptVaultDrop(e, node.path)}
          >
            {indentGuides(depth)}
            <div
              className={cn(
                "flex w-full items-center gap-0.5 rounded-md pr-1 text-[13px]",
                isSelected
                  ? "bg-teal-500/15 text-teal-100"
                  : "text-white/65 hover:bg-white/5 hover:text-white/90",
              )}
              style={{ paddingLeft: 6 + depth * 12 }}
            >
              <button
                type="button"
                onClick={() => {
                  onToggleFolder(node.path);
                  onSelectFolder(node.path);
                }}
                className="flex min-w-0 flex-1 items-center gap-1 px-1.5 py-1 text-left"
              >
                <ChevronRight
                  className={cn(
                    "h-3 w-3 shrink-0 text-white/40 transition-transform",
                    isOpen && "rotate-90",
                  )}
                />
                <Folder className="h-3.5 w-3.5 shrink-0 text-amber-300/80" />
                <span className="truncate font-medium">{node.name}</span>
              </button>
              {folderActionButtons(node.path)}
            </div>
          </div>
        );
      }

      case "note": {
        const { node, depth } = row;
        const note = node.note!;
        const parentFolder = node.path.includes("/")
          ? node.path.slice(0, node.path.lastIndexOf("/"))
          : "";
        const isChecked = selectedNoteIds.has(note.id);
        return (
          <div
            draggable
            onDragStart={(e) => {
              onDragNoteStart(note.id);
              e.dataTransfer.setData("text/note-id", note.id);
              e.dataTransfer.effectAllowed = "move";
            }}
            onDragEnd={onDragNoteEnd}
            onDragOver={allowVaultDragOver}
            onDrop={(e) => acceptVaultDrop(e, parentFolder)}
            className={cn(
              "relative flex w-full items-center gap-1 rounded-md px-1 py-1 text-left text-[13px] transition-colors",
              selectedNoteId === note.id
                ? "bg-white/10 text-white"
                : "text-white/70 hover:bg-white/5 hover:text-white/90",
              dragNoteId === note.id && "opacity-50",
              isChecked && "ring-1 ring-red-400/30",
            )}
            style={{ paddingLeft: 10 + depth * 12 }}
          >
            {indentGuides(depth)}
            <input
              type="checkbox"
              checked={isChecked}
              onChange={() => onToggleNoteSelected(note.id)}
              onClick={(e) => e.stopPropagation()}
              className="h-3.5 w-3.5 shrink-0 rounded border-white/30 bg-black/40"
              aria-label={`Select ${note.title || "note"}`}
            />
            <button
              type="button"
              onClick={() => onNoteSelect(note)}
              className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
            >
              <FileText className="h-3.5 w-3.5 shrink-0 text-white/35" />
              <span className="min-w-0 flex-1 truncate">
                {note.title || node.name || "Untitled"}
              </span>
              <span className="shrink-0">
                <NoteStatusDot note={note} />
              </span>
            </button>
          </div>
        );
      }

      case "media":
        return (
          <div
            className="relative"
            onDragOver={allowVaultDragOver}
            onDrop={(e) => acceptVaultDrop(e, row.dropTarget)}
          >
            {indentGuides(row.depth)}
            <VaultFileRow
              name={row.file.name}
              relPath={row.file.rel_path}
              depth={row.depth}
              isDragging={dragFileRel === row.file.rel_path}
              onDragStart={onDragFileStart}
              onDragEnd={onDragFileEnd}
              onClick={onFileClick}
              onRename={onRenameFile}
              onDelete={onDeleteVaultAttachment}
            />
          </div>
        );

      case "attachments-header":
        return (
          <div
            className={cn(
              "group/folder mt-2 border-t border-white/5 pt-2",
              (dragNoteId || dragFileRel) && "ring-1 ring-sky-500/25 rounded-md",
            )}
            onDragOver={allowVaultDragOver}
            onDrop={(e) => acceptVaultDrop(e, "attachments")}
          >
            <div className="flex w-full items-center gap-0.5 rounded-md pr-1 text-[13px] text-white/65 hover:bg-white/5 hover:text-white/90">
              <button
                type="button"
                onClick={() => {
                  onSelectFolder("attachments");
                  onToggleFolder("attachments");
                }}
                className="flex min-w-0 flex-1 items-center gap-1 px-1.5 py-1 text-left"
              >
                <ChevronRight
                  className={cn(
                    "h-3 w-3 shrink-0 text-white/40 transition-transform",
                    attachmentsOpen && "rotate-90",
                  )}
                />
                <Paperclip className="h-3.5 w-3.5 shrink-0 text-sky-300/80" />
                <span className="truncate font-medium">Attachments</span>
                <span className="text-[10px] text-white/35">
                  {attachmentFiles.length}
                </span>
              </button>
            </div>
          </div>
        );

      case "attachments-empty":
        return (
          <p className="px-8 py-1 text-[11px] text-white/30">
            Default upload folder — drag files into other folders anytime
          </p>
        );

      case "attachment-folder":
        return (
          <div
            className={cn(
              (dragNoteId || dragFileRel) && "ring-1 ring-sky-500/25 rounded-md",
            )}
            onDragOver={allowVaultDragOver}
            onDrop={(e) => acceptVaultDrop(e, row.path)}
          >
            <button
              type="button"
              onClick={() => {
                onSelectFolder(row.path);
                onToggleFolder(row.path);
              }}
              style={{ paddingLeft: 8 + row.depth * 12 }}
              className="flex w-full min-w-0 items-center gap-1 rounded-md py-1 pr-1 text-left text-[13px] text-white/65 hover:bg-white/5 hover:text-white/90"
            >
              <ChevronRight
                className={cn(
                  "h-3 w-3 shrink-0 text-white/40 transition-transform",
                  !collapsedFolders.has(row.path) && "rotate-90",
                )}
              />
              <Paperclip className="h-3.5 w-3.5 shrink-0 text-sky-300/60" />
              <span className="truncate">{row.name}</span>
              <span className="text-[10px] text-white/35">{row.count}</span>
            </button>
          </div>
        );

      case "attachment":
        return (
          <div
            onDragOver={allowVaultDragOver}
            onDrop={(e) =>
              acceptVaultDrop(
                e,
                row.file.rel_path.slice(0, row.file.rel_path.lastIndexOf("/")),
              )
            }
          >
            <VaultFileRow
              name={row.file.name}
              relPath={row.file.rel_path}
              depth={row.depth}
              isDragging={dragFileRel === row.file.rel_path}
              onDragStart={onDragFileStart}
              onDragEnd={onDragFileEnd}
              onClick={onFileClick}
              onRename={onRenameFile}
              onDelete={onDeleteVaultAttachment}
            />
          </div>
        );
    }
  };

  return (
    <div
      className="relative w-full"
      style={{ height: virtualizer.getTotalSize(), minHeight: 40 }}
      onDragOver={allowVaultDragOver}
      onDrop={(e) => acceptVaultDrop(e, "")}
    >
      {virtualizer.getVirtualItems().map((vi) => {
        const row = rows[vi.index];
        return (
          <div
            key={row.key}
            data-index={vi.index}
            ref={virtualizer.measureElement}
            className="absolute left-0 top-0 w-full"
            style={{ transform: `translateY(${vi.start}px)` }}
          >
            {renderRow(row)}
          </div>
        );
      })}
    </div>
  );
}
