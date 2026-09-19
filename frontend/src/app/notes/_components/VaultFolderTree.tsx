import { useMemo, useState, type DragEvent, type RefObject } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  ChevronRight,
  FileText,
  Folder,
  FolderPlus,
  Paperclip,
  Plus,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { Note } from "@/lib/types";
import { buildFolderTree } from "../_lib/folder-tree";
import type { FolderTreeNode, VaultFileEntry } from "../_lib/types";
import { NoteStatusDot, noteStatus } from "./NoteStatusBadge";
import { VaultFileRow } from "./VaultFileRow";

type VaultFolderTreeProps = {
  notes: Note[];
  vaultFolders: string[];
  vaultName: string;
  mediaFiles: VaultFileEntry[];
  attachmentFiles: VaultFileEntry[];
  expandedFolders: Set<string>;
  selectedFolder: string;
  selectedNoteId: string | null;
  selectedNoteIds: Set<string>;
  dragNoteId: string | null;
  dragFileRel: string | null;
  selectedFileRels: Set<string>;
  onToggleFileSelected: (relPath: string) => void;
  onMoveVaultFiles: (rels: string[], folder: string) => void;
  /** Scrollable ancestor (the sidebar body) used to window the tree rows. */
  scrollRef: RefObject<HTMLDivElement | null>;
  onToggleFolder: (path: string) => void;
  onSelectFolder: (path: string) => void;
  onNoteSelect: (note: Note) => void;
  onNoteContextMenu?: (note: Note, x: number, y: number) => void;
  onFolderContextMenu?: (path: string, x: number, y: number) => void;
  onMoveVaultFolder: (fromPath: string, folder: string) => void;
  onDeleteVaultFolder: (path: string) => void;
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
  | { key: string; kind: "folder"; node: FolderTreeNode; depth: number; count: number }
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

function countNotes(node: FolderTreeNode): number {
  if (node.note) return 1;
  return node.children.reduce((n, c) => n + countNotes(c), 0);
}

function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function VaultFolderTree({
  notes,
  vaultFolders,
  vaultName,
  mediaFiles,
  attachmentFiles,
  expandedFolders,
  selectedFolder,
  selectedNoteId,
  selectedNoteIds,
  dragNoteId,
  dragFileRel,
  selectedFileRels,
  onToggleFileSelected,
  onMoveVaultFiles,
  scrollRef,
  onToggleFolder,
  onSelectFolder,
  onNoteSelect,
  onNoteContextMenu,
  onFolderContextMenu,
  onMoveVaultFolder,
  onDeleteVaultFolder,
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
  const attachmentsOpen = expandedFolders.has("attachments");
  // Folder being dragged (vault-relative path); the tree owns this one.
  const [dragFolder, setDragFolder] = useState<string | null>(null);
  const dragging = Boolean(dragNoteId || dragFileRel || dragFolder);

  const rows = useMemo<TreeRow[]>(() => {
    const noteFolders = vaultFolders.filter(
      (f) => f !== "attachments" && !f.startsWith("attachments/"),
    );
    const tree = buildFolderTree(notes, noteFolders);

    const mediaByFolder = new Map<string, VaultFileEntry[]>();
    const rootMedia: VaultFileEntry[] = [];
    for (const f of mediaFiles) {
      const rel = f.rel_path.replace(/\\/g, "/");
      if (rel === "attachments" || rel.startsWith("attachments/")) continue;
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
        out.push({
          key: `folder:${node.path}`,
          kind: "folder",
          node,
          depth,
          count: countNotes(node),
        });
        if (!expandedFolders.has(node.path)) return;
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
      out.push({ key: `media:${f.rel_path}`, kind: "media", file: f, depth: 0, dropTarget: "" });
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
        let walkUp = parent;
        while (walkUp && walkUp !== "attachments") {
          folders.add(walkUp);
          walkUp = walkUp.slice(0, walkUp.lastIndexOf("/"));
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
          if (!expandedFolders.has(child)) continue;
          emit(child, depth + 1);
          for (const f of own) {
            out.push({ key: `attachment:${f.rel_path}`, kind: "attachment", file: f, depth: depth + 1 });
          }
        }
      };

      emit("attachments", 0);
      for (const f of filesByFolder.get("attachments") || []) {
        out.push({ key: `attachment:${f.rel_path}`, kind: "attachment", file: f, depth: 0 });
      }
      if (attachmentFiles.length === 0 && folders.size === 0) {
        out.push({ key: "__attachments-empty__", kind: "attachments-empty" });
      }
    }
    return out;
  }, [notes, vaultFolders, mediaFiles, attachmentFiles, expandedFolders, attachmentsOpen]);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    // Sizes are cached per row, not per index: opening a folder shifts every
    // row below it, and an index-keyed cache then paints note rows at the
    // height of whatever used to sit there.
    getItemKey: (index) => rows[index].key,
    // Note rows carry a second line; the rest are single-line.
    estimateSize: (index) => (rows[index].kind === "note" ? 50 : 30),
    overscan: 12,
  });

  const acceptVaultDrop = (e: DragEvent, folderPath: string) => {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = "move";
    const noteId = e.dataTransfer.getData("text/note-id") || dragNoteId;
    const folderRel = e.dataTransfer.getData("text/vault-folder") || dragFolder;
    const fileRel = e.dataTransfer.getData("text/vault-file") || dragFileRel;
    let fileRels: string[] = [];
    try {
      fileRels = JSON.parse(e.dataTransfer.getData("text/vault-files") || "[]");
    } catch {
      fileRels = [];
    }
    onDragNoteEnd();
    onDragFileEnd();
    setDragFolder(null);
    if (folderRel) void onMoveVaultFolder(folderRel, folderPath);
    else if (noteId) void onMoveNoteToFolder(noteId, folderPath);
    else if (fileRels.length > 1) void onMoveVaultFiles(fileRels, folderPath);
    else if (fileRel) void onMoveVaultFile(fileRel, folderPath);
  };

  const allowVaultDragOver = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = "move";
  };

  const isRootFolder = (folderPath: string) => folderPath === "" || folderPath === "attachments";

  const folderActionButtons = (folderPath: string) => (
    <div className="flex shrink-0 items-center gap-0.5 opacity-0 group-hover/folder:opacity-100">
      {!isRootFolder(folderPath) && (
        <button
          type="button"
          title="Delete folder"
          onClick={(e) => {
            e.stopPropagation();
            onDeleteVaultFolder(folderPath);
          }}
          className="grid h-5 w-5 place-items-center rounded text-n-500 hover:bg-n-800 hover:text-danger-text"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      )}
      <button
        type="button"
        title="New folder"
        onClick={(e) => {
          e.stopPropagation();
          onOpenFolderDialog(folderPath);
        }}
        className="grid h-5 w-5 place-items-center rounded text-n-500 hover:bg-n-800 hover:text-text"
      >
        <FolderPlus className="h-3 w-3" />
      </button>
      <button
        type="button"
        title="New note"
        onClick={(e) => {
          e.stopPropagation();
          onSelectFolder(folderPath);
          void onCreateNote(folderPath);
        }}
        className="grid h-5 w-5 place-items-center rounded text-n-500 hover:bg-n-800 hover:text-text"
      >
        <Plus className="h-3 w-3" />
      </button>
    </div>
  );

  const folderHeader = (
    label: string,
    path: string,
    depth: number,
    opts: { open?: boolean; selected: boolean; count?: number; icon: React.ReactNode; onClick: () => void },
  ) => (
    <div
      draggable={!isRootFolder(path)}
      onDragStart={(e) => {
        if (isRootFolder(path)) return;
        e.stopPropagation();
        setDragFolder(path);
        e.dataTransfer.setData("text/vault-folder", path);
        e.dataTransfer.effectAllowed = "move";
      }}
      onDragEnd={() => setDragFolder(null)}
      onContextMenu={(e) => {
        if (!onFolderContextMenu || isRootFolder(path)) return;
        e.preventDefault();
        onFolderContextMenu(path, e.clientX, e.clientY);
      }}
      className={cn(
        "group/folder flex w-full items-center gap-1 rounded-[6px] pr-1 kicker",
        opts.selected ? "text-accent" : "hover:text-n-300",
        dragging && "ring-1 ring-accent/30",
        dragFolder === path && "opacity-50",
      )}
      style={{ paddingLeft: 6 + depth * 12 }}
    >
      <button
        type="button"
        onClick={opts.onClick}
        className="flex min-w-0 flex-1 items-center gap-1.5 px-1 pb-1 pt-2.5 text-left"
        title={path || vaultName}
      >
        {opts.open !== undefined && (
          <ChevronRight
            className={cn("h-3 w-3 shrink-0 text-n-600 transition-transform", opts.open && "rotate-90")}
          />
        )}
        {opts.icon}
        <span className="truncate">{label}</span>
        {opts.count !== undefined && (
          <span className="ml-auto text-[10px] normal-case tracking-normal text-n-600">{opts.count}</span>
        )}
      </button>
      {folderActionButtons(path)}
    </div>
  );

  const renderRow = (row: TreeRow) => {
    switch (row.kind) {
      case "vault-header":
        return (
          <div onDragOver={allowVaultDragOver} onDrop={(e) => acceptVaultDrop(e, "")}>
            {folderHeader(vaultName, "", 0, {
              selected: selectedFolder === "",
              count: notes.length,
              icon: <Folder className="h-3 w-3 shrink-0" />,
              onClick: () => onSelectFolder(""),
            })}
          </div>
        );

      case "folder": {
        const { node, depth } = row;
        return (
          <div onDragOver={allowVaultDragOver} onDrop={(e) => acceptVaultDrop(e, node.path)}>
            {folderHeader(node.name, node.path, depth, {
              open: expandedFolders.has(node.path),
              selected: selectedFolder === node.path,
              count: row.count,
              icon: <Folder className="h-3 w-3 shrink-0" />,
              onClick: () => {
                onToggleFolder(node.path);
                onSelectFolder(node.path);
              },
            })}
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
        const selected = selectedNoteId === note.id;
        const status = noteStatus(note);
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
            onClick={() => onNoteSelect(note)}
            onContextMenu={(e) => {
              if (!onNoteContextMenu) return;
              e.preventDefault();
              onNoteContextMenu(note, e.clientX, e.clientY);
            }}
            className={cn(
              "group/note flex w-full cursor-default items-start gap-2 rounded-md px-2 py-1.5 text-left",
              selected ? "bg-surface text-text shadow-sm" : "text-n-300 hover:bg-n-900",
              dragNoteId === note.id && "opacity-50",
              isChecked && "ring-1 ring-danger/40",
            )}
            style={{ paddingLeft: 8 + depth * 12 }}
          >
            <input
              type="checkbox"
              checked={isChecked}
              onChange={() => onToggleNoteSelected(note.id)}
              onClick={(e) => e.stopPropagation()}
              className={cn(
                "mt-1 h-3 w-3 shrink-0 accent-accent",
                !isChecked && "opacity-0 group-hover/note:opacity-100",
              )}
              aria-label={`Select ${note.title || "note"}`}
            />
            <FileText className="mt-0.5 h-[15px] w-[15px] shrink-0 text-n-500" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px]">{note.title || node.name || "Untitled"}</div>
              <div className="truncate text-[11px] text-n-500">
                {shortDate(note.created_at)} · {status.label}
              </div>
            </div>
            <span className="mt-1.5 shrink-0">
              <NoteStatusDot note={note} />
            </span>
          </div>
        );
      }

      case "media":
        return (
          <div onDragOver={allowVaultDragOver} onDrop={(e) => acceptVaultDrop(e, row.dropTarget)}>
            <VaultFileRow
              name={row.file.name}
              relPath={row.file.rel_path}
              depth={row.depth}
              isDragging={dragFileRel === row.file.rel_path}
              isSelected={selectedFileRels.has(row.file.rel_path)}
              selectedRels={selectedFileRels}
              onToggleSelected={onToggleFileSelected}
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
            className="mt-1 border-t border-n-900"
            onDragOver={allowVaultDragOver}
            onDrop={(e) => acceptVaultDrop(e, "attachments")}
          >
            {folderHeader("Attachments", "attachments", 0, {
              open: attachmentsOpen,
              selected: selectedFolder === "attachments",
              count: attachmentFiles.length,
              icon: <Paperclip className="h-3 w-3 shrink-0" />,
              onClick: () => {
                onSelectFolder("attachments");
                onToggleFolder("attachments");
              },
            })}
          </div>
        );

      case "attachments-empty":
        return (
          <p className="px-7 py-1 text-[11px] text-n-600">
            Default upload folder — drag files into other folders anytime
          </p>
        );

      case "attachment-folder":
        return (
          <div onDragOver={allowVaultDragOver} onDrop={(e) => acceptVaultDrop(e, row.path)}>
            {folderHeader(row.name, row.path, row.depth, {
              open: expandedFolders.has(row.path),
              selected: selectedFolder === row.path,
              count: row.count,
              icon: <Paperclip className="h-3 w-3 shrink-0" />,
              onClick: () => {
                onSelectFolder(row.path);
                onToggleFolder(row.path);
              },
            })}
          </div>
        );

      case "attachment":
        return (
          <div
            onDragOver={allowVaultDragOver}
            onDrop={(e) =>
              acceptVaultDrop(e, row.file.rel_path.slice(0, row.file.rel_path.lastIndexOf("/")))
            }
          >
            <VaultFileRow
              name={row.file.name}
              relPath={row.file.rel_path}
              depth={row.depth}
              isDragging={dragFileRel === row.file.rel_path}
              isSelected={selectedFileRels.has(row.file.rel_path)}
              selectedRels={selectedFileRels}
              onToggleSelected={onToggleFileSelected}
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
