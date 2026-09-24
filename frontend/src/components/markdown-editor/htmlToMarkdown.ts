import TurndownService from "turndown";
import { gfm } from "turndown-plugin-gfm";

// One converter for the app: a paste from a browser, a Google Doc or Word
// keeps its headings, emphasis, links, lists, tables and code. Options match
// what the editor writes itself (`-` bullets, `**` bold, fenced code).
const turndown = new TurndownService({
  headingStyle: "atx",
  bulletListMarker: "-",
  codeBlockStyle: "fenced",
  emDelimiter: "*",
  strongDelimiter: "**",
});
turndown.use(gfm);
// Presentation-only tags have no Markdown; keep their text, drop the tags.
turndown.keep([]);
turndown.remove(["style", "script", "meta", "title"]);

// Tags that carry structure Markdown can express. HTML without any of them
// (VS Code, terminals: coloured `div`/`span`/`br` around the same text as
// `text/plain`) has nothing to convert, and converting it would escape the
// Markdown punctuation the text already contains.
const SEMANTIC_TAG = /<(h[1-6]|p|ul|ol|li|a|b|strong|i|em|table|blockquote|pre|code|img|hr|del|s|u|sup|sub)\b/i;

/** Markdown for pasted HTML, or "" when the HTML has nothing worth keeping. */
export function htmlToMarkdown(html: string): string {
  if (!SEMANTIC_TAG.test(html)) return "";
  const out = turndown
    .turndown(html)
    .replace(/\u00a0/g, " ")
    // turndown pads list items to a tab stop (`-   x`); the editor writes `- x`.
    .replace(/^(\s*(?:[-*+]|\d+\.))[ \t]{2,}/gm, "$1 ")
    .trim();
  // A paste that is only whitespace or a lone line break is not worth a
  // custom insert; let the plain-text path handle it.
  return out.replace(/\s+/g, "").length === 0 ? "" : out;
}
