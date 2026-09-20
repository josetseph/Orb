import { expect, it } from "vitest";
import type { Note } from "@/lib/types";
import { buildFolderTree } from "./folder-tree";

const note = (id: string, rel_path: string | null, title = ""): Note => ({
  id,
  title,
  content: "",
  created_at: "",
  rel_path,
});

it("groups notes by folder, folders first, sorted by name, and merges extra folders", () => {
  const tree = buildFolderTree(
    [note("1", "b/two.md"), note("2", "one.md"), note("3", "a/x/deep.md"), note("4", null, "Loose")],
    ["empty", "a"],
  );
  expect(tree.map((n) => [n.name, n.note ? "note" : "folder"])).toEqual([
    ["a", "folder"],
    ["b", "folder"],
    ["empty", "folder"],
    ["Loose", "note"],
    ["one", "note"],
  ]);
  const a = tree[0];
  expect(a.path).toBe("a");
  expect(a.children.map((n) => n.path)).toEqual(["a/x"]);
  expect(a.children[0].children[0]).toMatchObject({ name: "deep", path: "a/x/deep.md" });
  expect(tree[3].path).toBe("4"); // rootless note keyed by id
});
