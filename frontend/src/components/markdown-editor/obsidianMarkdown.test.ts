import { describe, expect, it } from "vitest";
import { markdownLanguage } from "@codemirror/lang-markdown";
import type { MarkdownParser } from "@lezer/markdown";
import { obsidianMarkdown } from "./obsidianMarkdown";

const parser = (markdownLanguage.parser as MarkdownParser).configure(obsidianMarkdown);

/** `Name(from,to)` for every node except the containers we do not care about. */
function nodes(text: string, keep?: RegExp): string[] {
  const out: string[] = [];
  parser.parse(text).iterate({
    enter: (n) => {
      if (n.name === "Document" || n.name === "Paragraph") return;
      if (!keep || keep.test(n.name)) out.push(`${n.name}(${n.from},${n.to})`);
    },
  });
  return out;
}

describe("obsidianMarkdown", () => {
  it("parses ==highlight==", () => {
    expect(nodes("a ==hi== b")).toEqual(["Highlight(2,8)", "HighlightMark(2,4)", "HighlightMark(6,8)"]);
  });
});
