import { describe, expect, it } from "vitest";
import type { Note } from "@/lib/types";
import {
  WikilinkResolver,
  normalizeLink,
  parseWikilinkCreateTarget,
  suggestWikilinkNotes,
  wikilinkInsertTarget,
} from "./wikilinks";

const note = (id: string, rel_path: string, title = ""): Note => ({
  id,
  title,
  content: "",
  created_at: "",
  rel_path,
});

describe("normalizeLink", () => {
  it("lowercases, trims slashes and ./, and drops .md", () => {
    expect(normalizeLink(" /Life/Daily.MD/ ")).toBe("life/daily");
    expect(normalizeLink("./Life/Daily.md")).toBe("life/daily");
    expect(normalizeLink(null)).toBe("");
  });
});

describe("parseWikilinkCreateTarget", () => {
  it("splits folder/title and normalises backslashes", () => {
    expect(parseWikilinkCreateTarget("Life\\Daily\\note.md")).toEqual({ folder: "Life/Daily", title: "note" });
    expect(parseWikilinkCreateTarget("/solo/")).toEqual({ folder: "", title: "solo" });
    expect(parseWikilinkCreateTarget("  ")).toEqual({ folder: "", title: "Untitled" });
  });
});

describe("WikilinkResolver", () => {
  const root = note("a", "photosynthesis.md");
  const nested = note("b", "meow/photosynthesis.md");
  const other = note("c", "meow/other.md");
  const retitled = note("d", "x/Untitled 3.md", "Real Title");
  const notes = [root, nested, other, retitled];
  const r = new WikilinkResolver(notes);

  it("exact vault path wins over proximity", () => {
    expect(r.resolve("Photosynthesis", other)).toBe(root);
    expect(r.resolve("meow/photosynthesis")).toBe(nested);
  });

  it("falls back to same-folder, then title, then nothing", () => {
    expect(r.resolve("other", nested)).toBe(other);
    expect(r.resolve("real title")).toBe(retitled);
    expect(r.resolve("missing")).toBeUndefined();
  });

  it("prefers the closest basename match when several folders share a name", () => {
    const a = note("1", "a/n.md");
    const b = note("2", "b/deep/n.md");
    const src = note("3", "b/src.md");
    expect(new WikilinkResolver([a, b]).resolve("n", src)).toBe(b);
  });

  it("wikilinkInsertTarget uses the bare name unless it collides", () => {
    expect(wikilinkInsertTarget(other, notes)).toBe("other");
    expect(wikilinkInsertTarget(nested, notes)).toBe("meow/photosynthesis");
    expect(wikilinkInsertTarget(retitled, notes)).toBe("Real Title");
  });

  it("suggestWikilinkNotes keeps only matches, ties broken by insert text", () => {
    const inserts = suggestWikilinkNotes(notes, "photo").map((s) => s.insert);
    expect(inserts).toEqual(["meow/photosynthesis", "photosynthesis"]);
    expect(suggestWikilinkNotes(notes, "zzz")).toEqual([]);
  });
});
