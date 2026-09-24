import type { InlineContext, MarkdownConfig } from "@lezer/markdown";
import type { SyntaxNode } from "@lezer/common";
import type { Text } from "@codemirror/state";
import { Tag, tags } from "@lezer/highlight";

/*
 * Obsidian-flavoured Markdown as parser nodes, layered on top of
 * `markdownLanguage` (CommonMark + GFM). Live preview and Source mode both
 * read these nodes; nothing here touches the DOM.
 */

/** Tags for constructs @lezer/highlight has no name for; styled in markdownHighlight.ts. */
export const obsidianTags = {
  highlight: Tag.define(),
  tag: Tag.define(),
  blockId: Tag.define(),
  math: Tag.define(),
  frontmatter: Tag.define(),
  footnoteRef: Tag.define(),
  footnoteDef: Tag.define(),
};

export const HIGHLIGHT = "Highlight";
export const HIGHLIGHT_MARK = "HighlightMark";
export const WIKILINK = "WikiLink";
export const WIKILINK_MARK = "WikiLinkMark";
export const WIKILINK_TARGET = "WikiLinkTarget";
export const WIKILINK_HEADING = "WikiLinkHeading";
export const WIKILINK_ALIAS = "WikiLinkAlias";
export const EMBED = "Embed";
export const INLINE_MATH = "InlineMath";
export const BLOCK_MATH = "BlockMath";
export const MATH_MARK = "MathMark";
export const TAG = "Tag";
export const BLOCK_ID = "BlockId";
export const FRONTMATTER = "Frontmatter";
export const FRONTMATTER_MARK = "FrontmatterMark";
export const FOOTNOTE_REF = "FootnoteRef";
export const FOOTNOTE_DEF = "FootnoteDef";
export const FOOTNOTE_LABEL = "FootnoteLabel";
export const FOOTNOTE_MARK = "FootnoteMark";

const Punctuation = /[!-/:-@[-`{-~\xA1\xA7\xAB\xB6\xB7\xBB\xBF‐-‧]/;
const HighlightDelim = { resolve: HIGHLIGHT, mark: HIGHLIGHT_MARK };

// `==text==`; same open/close rules as GFM strikethrough, with `=` for `~`.
const highlight: MarkdownConfig = {
  defineNodes: [
    { name: HIGHLIGHT, style: { [`${HIGHLIGHT}/...`]: obsidianTags.highlight } },
    { name: HIGHLIGHT_MARK, style: tags.processingInstruction },
  ],
  parseInline: [
    {
      name: HIGHLIGHT,
      after: "Emphasis",
      parse(cx, next, pos) {
        if (next !== 61 /* = */ || cx.char(pos + 1) !== 61 || cx.char(pos + 2) === 61) return -1;
        const before = cx.slice(pos - 1, pos);
        const after = cx.slice(pos + 2, pos + 3);
        const sBefore = /\s|^$/.test(before);
        const sAfter = /\s|^$/.test(after);
        const pBefore = Punctuation.test(before);
        const pAfter = Punctuation.test(after);
        return cx.addDelimiter(
          HighlightDelim,
          pos,
          pos + 2,
          !sAfter && (!pAfter || sBefore || pBefore),
          !sBefore && (!pBefore || sAfter || pAfter),
        );
      },
    },
  ],
};

// `[[target#heading|alias]]` and `![[…]]`. The `#` and `|` separators sit
// between the child nodes; the decoration pass slices by child.
const WIKILINK_RE = /^\[\[([^[\]|#\n]*)(?:#([^[\]|\n]*))?(?:\|([^[\]\n]*))?\]\]/;

function parseWikilink(cx: InlineContext, pos: number, nodeName: string, openLen: number): number {
  const m = WIKILINK_RE.exec(cx.slice(pos + openLen - 2, cx.end));
  if (!m || (!m[1] && m[2] === undefined)) return -1;
  const start = pos + openLen; // after `[[`
  const children = [cx.elt(WIKILINK_MARK, pos, start)];
  let at = start;
  if (m[1]) children.push(cx.elt(WIKILINK_TARGET, at, at + m[1].length));
  at += m[1].length;
  if (m[2] !== undefined) {
    at += 1; // `#`
    if (m[2]) children.push(cx.elt(WIKILINK_HEADING, at, at + m[2].length));
    at += m[2].length;
  }
  if (m[3] !== undefined) {
    at += 1; // `|`
    if (m[3]) children.push(cx.elt(WIKILINK_ALIAS, at, at + m[3].length));
    at += m[3].length;
  }
  children.push(cx.elt(WIKILINK_MARK, at, at + 2));
  return cx.addElement(cx.elt(nodeName, pos, at + 2, children));
}

/** The pieces of a `WikiLink` / `Embed` node; `target` is `note#heading` when a heading is set. */
export function wikilinkParts(doc: Text, node: SyntaxNode) {
  let name = "", heading = "", alias = "";
  for (let c = node.firstChild; c; c = c.nextSibling) {
    const text = doc.sliceString(c.from, c.to).trim();
    if (c.name === WIKILINK_TARGET) name = text;
    else if (c.name === WIKILINK_HEADING) heading = text;
    else if (c.name === WIKILINK_ALIAS) alias = text;
  }
  return { name, heading, alias, target: heading ? `${name}#${heading}` : name };
}

const wikilink: MarkdownConfig = {
  defineNodes: [
    { name: WIKILINK, style: { [`${WIKILINK}/...`]: tags.link } },
    { name: EMBED, style: { [`${EMBED}/...`]: tags.link } },
    { name: WIKILINK_MARK, style: tags.processingInstruction },
    { name: WIKILINK_TARGET, style: tags.link },
    { name: WIKILINK_HEADING, style: tags.labelName },
    { name: WIKILINK_ALIAS, style: tags.string },
  ],
  parseInline: [
    {
      name: WIKILINK,
      before: "Link",
      parse(cx, next, pos) {
        if (next === 91 /* [ */ && cx.char(pos + 1) === 91) return parseWikilink(cx, pos, WIKILINK, 2);
        if (next === 33 /* ! */ && cx.char(pos + 1) === 91 && cx.char(pos + 2) === 91) {
          return parseWikilink(cx, pos, EMBED, 3);
        }
        return -1;
      },
    },
  ],
};

// `$x$` (no space just inside, closing `$` not followed by a digit — `$5 and
// $6` is money) and `$$…$$`, which may span lines within a paragraph.
const INLINE_MATH_RE = /^\$(?!\s)([^$\n]*[^$\s\\])\$(?!\d)/;

const math: MarkdownConfig = {
  defineNodes: [
    { name: INLINE_MATH, style: { [`${INLINE_MATH}/...`]: obsidianTags.math } },
    { name: BLOCK_MATH, style: { [`${BLOCK_MATH}/...`]: obsidianTags.math } },
    { name: MATH_MARK, style: tags.processingInstruction },
  ],
  parseInline: [
    {
      name: "Math",
      before: "Emphasis",
      parse(cx, next, pos) {
        if (next !== 36 /* $ */) return -1;
        if (cx.char(pos + 1) === 36) {
          const close = cx.slice(pos + 2, cx.end).indexOf("$$");
          if (close < 1) return -1;
          const end = pos + 2 + close + 2;
          return cx.addElement(
            cx.elt(BLOCK_MATH, pos, end, [cx.elt(MATH_MARK, pos, pos + 2), cx.elt(MATH_MARK, end - 2, end)]),
          );
        }
        const m = INLINE_MATH_RE.exec(cx.slice(pos, cx.end));
        if (!m) return -1;
        const end = pos + m[0].length;
        return cx.addElement(
          cx.elt(INLINE_MATH, pos, end, [cx.elt(MATH_MARK, pos, pos + 1), cx.elt(MATH_MARK, end - 1, end)]),
        );
      },
    },
  ],
};

// `#tag` after whitespace (a heading's `#` is consumed at block level, so
// only mid-line and space-less `#word` reach the inline parsers).
const TAG_RE = /^#[\p{L}\p{N}_/-]+/u;
// `^id` as the last thing on a line.
const BLOCK_ID_RE = /^\^[\p{L}\p{N}-]+(?=\n|$)/u;

const tagsAndIds: MarkdownConfig = {
  defineNodes: [
    { name: TAG, style: obsidianTags.tag },
    { name: BLOCK_ID, style: obsidianTags.blockId },
  ],
  parseInline: [
    {
      name: TAG,
      parse(cx, next, pos) {
        if (next !== 35 /* # */ || !/\s|^$/.test(cx.slice(pos - 1, pos))) return -1;
        const m = TAG_RE.exec(cx.slice(pos, cx.end));
        if (!m || /^#\d+$/.test(m[0])) return -1;
        return cx.addElement(cx.elt(TAG, pos, pos + m[0].length));
      },
    },
    {
      name: BLOCK_ID,
      parse(cx, next, pos) {
        if (next !== 94 /* ^ */ || !/\s/.test(cx.slice(pos - 1, pos))) return -1;
        const m = BLOCK_ID_RE.exec(cx.slice(pos, cx.end));
        if (!m) return -1;
        return cx.addElement(cx.elt(BLOCK_ID, pos, pos + m[0].length));
      },
    },
  ],
};

export const obsidianMarkdown: MarkdownConfig[] = [
  highlight,
  wikilink,
  math,
  tagsAndIds,
];
