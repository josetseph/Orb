import type { Note } from "@/lib/types";
import type { FolderTreeNode } from "./types";

export type NoteSort = "name-asc" | "name-desc" | "modified-new" | "modified-old" | "created-new" | "created-old";

export const NOTE_SORTS: Array<{ key: NoteSort; label: string }> = [
  { key: "name-asc", label: "File name (A to Z)" },
  { key: "name-desc", label: "File name (Z to A)" },
  { key: "modified-new", label: "Modified time (new to old)" },
  { key: "modified-old", label: "Modified time (old to new)" },
  { key: "created-new", label: "Created time (new to old)" },
  { key: "created-old", label: "Created time (old to new)" },
];

const time = (iso?: string | null) => (iso ? Date.parse(iso) || 0 : 0);

/** Folders first; folders by name (reversed only for Z to A), notes by `sort`. */
function compareNodes(sort: NoteSort) {
  return (a: FolderTreeNode, b: FolderTreeNode) => {
    const aFolder = !a.note;
    const bFolder = !b.note;
    if (aFolder !== bFolder) return aFolder ? -1 : 1;
    const byName = a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" });
    if (sort === "name-desc") return -byName;
    if (aFolder || sort === "name-asc") return byName;
    const field = sort.startsWith("modified") ? "updated_at" : "created_at";
    const at = time(a.note![field] ?? a.note!.created_at);
    const bt = time(b.note![field] ?? b.note!.created_at);
    const diff = sort.endsWith("new") ? bt - at : at - bt;
    return diff || byName;
  };
}

export function buildFolderTree(
  notes: Note[],
  extraFolders: string[] = [],
  sort: NoteSort = "name-asc",
): FolderTreeNode[] {
  const root: FolderTreeNode[] = [];

  const ensureFolder = (parts: string[]): FolderTreeNode => {
    let level = root;
    let path = "";
    let node: FolderTreeNode | undefined;
    for (const part of parts) {
      path = path ? `${path}/${part}` : part;
      node = level.find((c) => c.name === part && !c.note);
      if (!node) {
        node = { name: part, path, children: [] };
        level.push(node);
      }
      level = node.children;
    }
    return node!;
  };

  for (const folder of extraFolders) {
    const parts = folder.split("/").filter(Boolean);
    if (parts.length) ensureFolder(parts);
  }

  for (const note of notes) {
    const rel = note.rel_path || "";
    if (!rel) {
      root.push({
        name: note.title || "Untitled",
        path: note.id,
        note,
        children: [],
      });
      continue;
    }
    const parts = rel.replace(/\.md$/i, "").split("/").filter(Boolean);
    const fileName = parts.pop() || note.title || "Untitled";
    if (parts.length === 0) {
      root.push({ name: fileName, path: rel, note, children: [] });
    } else {
      const folder = ensureFolder(parts);
      folder.children.push({
        name: fileName,
        path: rel,
        note,
        children: [],
      });
    }
  }

  const sortTree = (nodes: FolderTreeNode[]) => {
    nodes.sort(compareNodes(sort));
    for (const n of nodes) sortTree(n.children);
  };
  sortTree(root);
  return root;
}
