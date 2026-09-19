import type { Note } from "@/lib/types";
import type { FolderTreeNode } from "./types";

export function buildFolderTree(
  notes: Note[],
  extraFolders: string[] = [],
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
    nodes.sort((a, b) => {
      const aFolder = !a.note;
      const bFolder = !b.note;
      if (aFolder !== bFolder) return aFolder ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    for (const n of nodes) sortTree(n.children);
  };
  sortTree(root);
  return root;
}
