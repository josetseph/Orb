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

  it("parses wikilinks with heading and alias", () => {
    expect(nodes("[[Note#Sec|Alias]]")).toEqual([
      "WikiLink(0,18)",
      "WikiLinkMark(0,2)",
      "WikiLinkTarget(2,6)",
      "WikiLinkHeading(7,10)",
      "WikiLinkAlias(11,16)",
      "WikiLinkMark(16,18)",
    ]);
    expect(nodes("[[Note]]")).toEqual(["WikiLink(0,8)", "WikiLinkMark(0,2)", "WikiLinkTarget(2,6)", "WikiLinkMark(6,8)"]);
    expect(nodes("[[#Heading]]", /WikiLink/)).toEqual(["WikiLink(0,12)", "WikiLinkMark(0,2)", "WikiLinkHeading(3,10)", "WikiLinkMark(10,12)"]);
  });

  it("does not parse wikilinks inside code", () => {
    expect(nodes("`[[x]]`", /WikiLink/)).toEqual([]);
    expect(nodes("```\n[[x]]\n```", /WikiLink/)).toEqual([]);
  });

  it("parses embeds", () => {
    expect(nodes("![[Note#Sec]]")).toEqual([
      "Embed(0,13)",
      "WikiLinkMark(0,3)",
      "WikiLinkTarget(3,7)",
      "WikiLinkHeading(8,11)",
      "WikiLinkMark(11,13)",
    ]);
  });
});
