import { expect, it } from "vitest";
import { parseNoteAttachments } from "./parse-note-attachments";

it("finds image embeds and 📎/🎤 chips, keeping balanced parens in the url", () => {
  const md = [
    "intro ![diagram](attachments/d.png)",
    "[📎 Report](attachments/Report (2026).pdf)",
    "[🎤 memo](attachments/memo.m4a)",
    "[plain link](https://example.com) and ![](attachments/no-alt.jpg)",
  ].join("\n");
  const found = parseNoteAttachments(md);
  expect(found.map((a) => [a.label, a.url])).toEqual([
    ["diagram", "attachments/d.png"],
    ["Report", "attachments/Report (2026).pdf"],
    ["memo", "attachments/memo.m4a"],
    ["", "attachments/no-alt.jpg"], // empty alt stays empty; the "file" fallback only covers a missing group
  ]);
  // `raw` is the exact source text, so callers can splice it out of the note.
  for (const a of found) expect(md).toContain(a.raw);
  expect(found[1].raw).toBe("[📎 Report](attachments/Report (2026).pdf)");
});

it("returns nothing for prose and ordinary links", () => {
  expect(parseNoteAttachments("see [docs](https://x.y) or attachments/a.png")).toEqual([]);
});
