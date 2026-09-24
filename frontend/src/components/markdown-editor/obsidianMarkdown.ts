import type { MarkdownConfig } from "@lezer/markdown";
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

export const obsidianMarkdown: MarkdownConfig[] = [
  highlight,
];
