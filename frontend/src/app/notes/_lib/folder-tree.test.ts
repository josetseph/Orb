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

it("sorts notes by the chosen order, folders stay first", () => {
  const n = (id: string, rel: string, created: string, updated?: string): Note => ({
    ...note(id, rel),
    created_at: created,
    updated_at: updated,
  });
  const notes = [
    n("1", "b.md", "2026-01-02", "2026-03-01"),
    n("2", "a.md", "2026-01-03"),
    n("3", "c.md", "2026-01-01", "2026-02-01"),
  ];
  const names = (sort: Parameters<typeof buildFolderTree>[2]) =>
    buildFolderTree(notes, ["z"], sort).map((x) => x.name);
  expect(names("name-desc")).toEqual(["z", "c", "b", "a"]);
  expect(names("created-new")).toEqual(["z", "a", "b", "c"]);
  expect(names("modified-new")).toEqual(["z", "b", "c", "a"]);
  expect(names("modified-old")).toEqual(["z", "a", "c", "b"]);
});
