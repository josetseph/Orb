import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorView } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { obsidianTags } from "./obsidianMarkdown";

// Nocturne palette — the same tokens globals.css exposes to Tailwind. CodeMirror
// themes are plain objects, so the values are repeated here rather than read
// from CSS variables at runtime.
const TEXT = "#e9e9ed";
const MUTED = "#9397ab";
const N300 = "#cfd3e5";
const N700 = "#595d6c";
const N800 = "#3f424d";
const N900 = "#292b31";
const SURFACE = "#232532";
const ACCENT = "#9184d9";
const ACCENT200 = "#e7e5fe";
const ACCENT300 = "#d2cefd";
const ACCENT700 = "#5d5294";
const DIVIDER = "rgba(233,233,237,0.16)";
const DANGER = "oklch(0.72 0.14 25)";
const HIGHLIGHT_BG = "color-mix(in srgb, oklch(0.85 0.16 90) 30%, transparent)";
const MONO = "ui-monospace, Menlo, monospace";

/** Live-markdown highlighting: the note reads as a document, syntax stays quiet. */
const markdownHighlightStyle = HighlightStyle.define([
  { tag: tags.heading1, color: TEXT, fontSize: "1.5em", fontWeight: "500", letterSpacing: "-0.015em" },
  { tag: tags.heading2, color: TEXT, fontSize: "1.35em", fontWeight: "500", letterSpacing: "-0.01em" },
  { tag: tags.heading3, color: ACCENT200, fontSize: "1.2em", fontWeight: "500" },
  { tag: tags.heading4, color: TEXT, fontSize: "1.1em", fontWeight: "500" },
  { tag: tags.heading5, color: TEXT, fontSize: "1.05em", fontWeight: "500" },
  { tag: tags.heading6, color: MUTED, fontWeight: "500", letterSpacing: "0.08em" },
  { tag: tags.strong, color: TEXT, fontWeight: "600" },
  { tag: tags.emphasis, color: N300, fontStyle: "italic" },
  { tag: tags.strikethrough, color: MUTED, textDecoration: "line-through" },
  { tag: tags.link, color: ACCENT300, textDecoration: "underline", textUnderlineOffset: "3px" },
  { tag: tags.url, color: ACCENT },
  {
    tag: tags.monospace,
    color: ACCENT200,
    backgroundColor: N900,
    borderRadius: "5px",
    padding: "1px 6px",
    fontSize: "0.88em",
  },
  { tag: tags.quote, color: N300, fontStyle: "italic" },
  { tag: tags.list, color: TEXT },
  { tag: tags.meta, color: ACCENT700 },
  { tag: tags.processingInstruction, color: ACCENT700 },
  { tag: tags.contentSeparator, color: N700 },
  { tag: tags.atom, color: ACCENT300 },
  { tag: tags.bool, color: ACCENT300 },
  { tag: tags.labelName, color: ACCENT300 },
  { tag: tags.string, color: N300 },
  /* Obsidian syntax (obsidianMarkdown.ts) — styled here so Source mode has it too */
  { tag: obsidianTags.highlight, color: TEXT, backgroundColor: HIGHLIGHT_BG, borderRadius: "3px", padding: "1px 2px" },
  { tag: obsidianTags.math, color: ACCENT200, fontFamily: MONO, fontSize: "0.9em" },
  // TODO: clicking a tag does nothing yet (search by tag when that exists).
  {
    tag: obsidianTags.tag,
    color: ACCENT300,
    backgroundColor: "color-mix(in srgb, " + ACCENT + " 14%, transparent)",
    borderRadius: "999px",
    padding: "1px 7px",
    fontSize: "0.85em",
  },
  { tag: obsidianTags.blockId, color: N700 },
  { tag: obsidianTags.frontmatter, color: MUTED, fontFamily: MONO, fontSize: "0.88em" },
  { tag: obsidianTags.footnoteRef, color: ACCENT300, verticalAlign: "super", fontSize: "0.72em" },
  { tag: obsidianTags.footnoteDef, color: MUTED, fontSize: "0.92em" },
]);

/* Callout accents: the palette has no warning/success, so those are oklch in the danger idiom. */
const CALLOUT_COLORS: Record<string, string> = {
  note: "var(--color-accent)",
  abstract: "var(--color-accent-300)",
  info: "var(--color-accent-400)",
  example: "var(--color-accent-600)",
  quote: "var(--color-n-500)",
  tip: "oklch(0.78 0.14 160)",
  question: "oklch(0.8 0.13 80)",
  warning: "oklch(0.78 0.15 60)",
  danger: "var(--color-danger)",
};
const calloutTheme = Object.fromEntries(
  Object.entries(CALLOUT_COLORS).flatMap(([type, c]) => [
    [`.cm-md-callout-${type}`, { borderLeftColor: c, background: `color-mix(in srgb, ${c} 7%, transparent)` }],
    [`.cm-md-callout-${type} .cm-md-callout-label`, { color: c }],
  ]),
);

const pill = {
  height: "24px",
  padding: "0 8px",
  borderRadius: "6px",
  border: `1px solid ${DIVIDER}`,
  background: "transparent",
  color: N300,
  fontSize: "11.5px",
  fontFamily: "inherit",
  cursor: "pointer",
  whiteSpace: "nowrap",
};

const editorTheme = EditorView.theme(
  {
    "&": {
      color: TEXT,
      backgroundColor: "transparent",
      fontSize: "15px",
      height: "100%",
    },
    ".cm-scroller": {
      fontFamily:
        "var(--font-inter), Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      lineHeight: "1.7",
      overflow: "auto",
    },
    ".cm-content": {
      caretColor: ACCENT,
      padding: "0",
      minHeight: "100%",
      maxWidth: "720px",
    },
    ".cm-line": {
      padding: "0 12px",
    },
    ".cm-focused": { outline: "none" },
    ".cm-gutters": {
      display: "none",
    },
    ".cm-activeLine": {
      backgroundColor: "color-mix(in srgb, " + ACCENT + " 6%, transparent)",
      boxShadow: `inset 2px 0 0 ${ACCENT700}`,
      borderRadius: "6px",
    },
    ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": {
      backgroundColor: "rgba(145, 132, 217, 0.3) !important",
    },
    ".cm-cursor": { borderLeftColor: ACCENT },
    ".cm-placeholder": { color: N700 },
    "&.cm-editor": { height: "100%" },
    "&.cm-editor.cm-focused": { outline: "none" },
    ".cm-tooltip": {
      backgroundColor: SURFACE,
      border: "none",
      borderRadius: "8px",
      color: TEXT,
      boxShadow: `0 0 0 1px ${N700}, 0 6px 18px rgba(0,0,0,0.55)`,
    },
    ".cm-tooltip.cm-tooltip-autocomplete > ul": {
      fontFamily: "inherit",
      maxHeight: "240px",
    },
    ".cm-tooltip.cm-tooltip-autocomplete > ul > li": {
      padding: "7px 10px",
      borderRadius: "6px",
      fontSize: "13px",
    },
    ".cm-tooltip.cm-tooltip-autocomplete > ul > li[aria-selected]": {
      backgroundColor: N900,
      color: TEXT,
    },
    ".cm-completionLabel": { color: TEXT, fontWeight: "500" },
    ".cm-completionDetail": {
      color: MUTED,
      fontStyle: "normal",
      marginLeft: "8px",
      fontSize: "0.7em",
      textTransform: "uppercase",
    },
    /* Entity mentions — a dotted accent underline, as in the design */
    ".cm-entity-mention": {
      color: ACCENT200,
      textDecoration: "underline dotted",
      textUnderlineOffset: "3px",
      textDecorationColor: "#796cbf",
      cursor: "pointer",
    },
    /* [label](url) with the syntax hidden — the label is the link */
    ".cm-md-link": {
      color: ACCENT300,
      textDecoration: "none",
      borderBottom: `1px solid ${ACCENT700}`,
      cursor: "pointer",
    },
    ".cm-md-link:hover": { color: ACCENT200, borderBottomColor: ACCENT },
    /* Lists: bullets as dots (design), ordinals quiet and tabular */
    ".cm-md-bullet": {
      display: "inline-block",
      width: "5px",
      height: "5px",
      borderRadius: "50%",
      background: "#796cbf",
      margin: "0 12px 3px 4px",
      verticalAlign: "middle",
    },
    ".cm-md-ordinal": { color: MUTED, fontVariantNumeric: "tabular-nums" },
    ".cm-md-task": {
      appearance: "none",
      width: "14px",
      height: "14px",
      margin: "0 8px -2px 0",
      borderRadius: "4px",
      border: `1.5px solid ${DIVIDER}`,
      background: "transparent",
      cursor: "pointer",
      verticalAlign: "baseline",
    },
    ".cm-md-task:checked": {
      background: ACCENT,
      borderColor: ACCENT,
      boxShadow: `inset 0 0 0 3px ${SURFACE}`,
    },
    /* Horizontal rule: the Nocturne fading line */
    ".cm-md-hr": {
      display: "block",
      height: "1px",
      margin: "10px 0",
      background: `linear-gradient(90deg, transparent, ${N700} 48px, ${N700} calc(100% - 48px), transparent)`,
    },
    /* Blockquote and fenced code: framed per line so the block reads as one */
    ".cm-md-quote-line": {
      borderLeft: `2px solid ${ACCENT700}`,
      paddingLeft: "12px",
      color: N300,
    },
    ".cm-md-code-line": {
      background: N900,
      fontFamily: "ui-monospace, Menlo, monospace",
      fontSize: "13px",
      paddingLeft: "12px",
      paddingRight: "12px",
    },
    /* Callout: a typed quote — `> [!warning] Title` */
    // A callout is a box, not a quotation: upright text, normal colour.
    ".cm-md-callout": { borderLeftWidth: "3px", borderRadius: "0 6px 6px 0" },
    ".cm-md-callout .cm-md-quote-text, .cm-md-callout .tok-quote, .cm-md-callout span": { fontStyle: "normal", color: TEXT },
    ".cm-md-callout-title": { fontWeight: "500", paddingTop: "4px" },
    ".cm-md-callout-label": {
      marginRight: "8px",
      fontSize: "11px",
      letterSpacing: "0.08em",
      textTransform: "uppercase",
      fontStyle: "normal",
    },
    ...calloutTheme,
    /* YAML front matter: data, kept raw */
    ".cm-md-frontmatter": {
      background: N900,
      paddingLeft: "12px",
      paddingRight: "12px",
    },
    /* KaTeX output; the widget replaces `$…$` off the active element */
    ".cm-md-math": { cursor: "text" },
    ".cm-md-math-block": { padding: "6px 12px", overflowX: "auto", cursor: "text" },
    /* `![[note]]` embed: bordered box, note title as caption, body as plain paragraphs */
    ".cm-md-embed": {
      margin: "6px 0",
      padding: "8px 14px 4px",
      borderRadius: "8px",
      boxShadow: `0 0 0 1px ${N800}`,
      borderLeft: `2px solid ${ACCENT700}`,
      cursor: "text",
    },
    ".cm-md-embed-title": {
      fontSize: "11px",
      letterSpacing: "0.08em",
      textTransform: "uppercase",
      fontWeight: "500",
      color: MUTED,
      marginBottom: "4px",
    },
    ".cm-md-embed-body": { fontSize: "14px", color: N300, maxHeight: "320px", overflow: "auto" },
    ".cm-md-embed-body p": { margin: "0 0 8px", whiteSpace: "pre-wrap" },
    /* Table: the design's .table — quiet uppercase headers, fading row rules */
    ".cm-md-table": {
      margin: "6px 0",
      borderRadius: "8px",
      boxShadow: `0 0 0 1px ${N800}`,
      overflow: "auto",
      cursor: "text",
    },
    ".cm-md-table table": { width: "100%", borderCollapse: "collapse", fontSize: "13.5px" },
    ".cm-md-table th": {
      textAlign: "left",
      fontSize: "11px",
      letterSpacing: "0.08em",
      textTransform: "uppercase",
      fontWeight: "500",
      color: MUTED,
      padding: "8px 12px",
      borderBottom: `1px solid ${DIVIDER}`,
    },
    ".cm-md-table td": {
      padding: "7px 12px",
      borderBottom: `1px solid ${N900}`,
      verticalAlign: "top",
    },
    ".cm-md-table tbody tr:last-child td": { borderBottom: "none" },
    ".cm-md-table tbody tr:hover td": { background: "rgba(233,233,237,0.03)" },
    /* Note wikilinks — same idiom, lighter accent */
    ".cm-wikilink": {
      color: ACCENT300,
      textDecoration: "underline dotted",
      textUnderlineOffset: "3px",
      textDecorationColor: ACCENT700,
      cursor: "pointer",
    },
    /* Generated-extraction delimiters when expanded: a rule with a quiet label */
    ".cm-extract-marker": {
      display: "block",
      borderTop: `1px solid ${DIVIDER}`,
      marginTop: "6px",
      paddingTop: "3px",
      fontSize: "11px",
      letterSpacing: "0.02em",
      color: MUTED,
      userSelect: "none",
      cursor: "pointer",
    },
    ".cm-extract-marker-end": {
      borderTop: `1px dashed ${DIVIDER}`,
      marginTop: "3px",
      paddingTop: "0",
      height: "0",
      cursor: "default",
    },
    /* Collapsed extraction: one chip standing in for the whole block */
    ".cm-extract-collapsed": {
      display: "inline-flex",
      alignItems: "center",
      gap: "6px",
      margin: "4px 12px 8px",
      padding: "4px 10px",
      borderRadius: "6px",
      background: SURFACE,
      boxShadow: `0 0 0 1px ${N800}`,
      fontSize: "12px",
      color: "#b2b6ca",
      cursor: "pointer",
      userSelect: "none",
    },
    ".cm-extract-collapsed:hover": { background: N900 },
    ".cm-extract-collapsed::before": {
      content: "'≡'",
      color: ACCENT300,
    },
    /* Inline media embeds (images / video / audio / documents) */
    ".cm-media-embed": {
      display: "block",
      margin: "8px 0",
      maxWidth: "min(100%, 640px)",
      borderRadius: "8px",
      background: SURFACE,
      boxShadow: `0 0 0 1px ${N800}`,
      overflow: "hidden",
    },
    ".cm-media-embed-img": {
      display: "block",
      maxWidth: "100%",
      maxHeight: "360px",
      objectFit: "contain",
      background: "linear-gradient(160deg, #292b31, #0e0f18)",
      margin: "0 auto",
    },
    ".cm-media-embed-video": {
      display: "block",
      width: "100%",
      minHeight: "200px",
      maxHeight: "360px",
      background: "#0e0f18",
      aspectRatio: "16 / 9",
      objectFit: "contain",
    },
    ".cm-media-embed-iframe": {
      display: "block",
      width: "100%",
      aspectRatio: "16 / 9",
      height: "auto",
      minHeight: "200px",
      border: "none",
      background: "#0e0f18",
    },
    ".cm-media-embed-pdf": {
      display: "block",
      width: "100%",
      height: "480px",
      maxHeight: "70vh",
      border: "none",
      background: "#111",
    },
    ".cm-media-embed-audio": {
      display: "block",
      width: "100%",
      padding: "8px 12px 4px",
    },
    ".cm-media-embed-text": {
      maxHeight: "320px",
      overflow: "auto",
      padding: "10px 12px",
    },
    ".cm-media-embed-pre": {
      margin: "0",
      fontSize: "12px",
      lineHeight: "1.5",
      whiteSpace: "pre-wrap",
      wordBreak: "break-word",
      color: N300,
      fontFamily: "ui-monospace, Menlo, monospace",
    },
    ".cm-media-embed-table": {
      borderCollapse: "collapse",
      fontSize: "12.5px",
      width: "100%",
      fontVariantNumeric: "tabular-nums",
    },
    ".cm-media-embed-table th, .cm-media-embed-table td": {
      borderBottom: `1px solid ${N900}`,
      padding: "5px 8px",
      textAlign: "left",
      color: N300,
      whiteSpace: "nowrap",
      fontFamily: "ui-monospace, Menlo, monospace",
      fontSize: "12px",
    },
    ".cm-media-embed-table th": {
      fontWeight: "500",
      fontFamily: "inherit",
      fontSize: "11px",
      letterSpacing: "0.08em",
      textTransform: "uppercase",
      color: MUTED,
    },
    ".cm-media-embed-more": {
      padding: "6px 8px",
      fontSize: "11px",
      color: MUTED,
    },
    ".cm-media-embed-error": {
      fontSize: "12px",
      color: DANGER,
      padding: "8px 10px",
    },
    /* Footer bar: filename · status · [noun ▾] [verb] */
    ".cm-media-embed-bar": {
      display: "flex",
      alignItems: "center",
      gap: "8px",
      padding: "7px 12px",
      fontSize: "12px",
      color: "#b2b6ca",
      borderTop: `1px solid ${N900}`,
    },
    ".cm-media-embed-bar-name": {
      flex: "1",
      minWidth: "0",
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    },
    ".cm-media-embed-bar-status": {
      color: MUTED,
      whiteSpace: "nowrap",
      maxWidth: "260px",
      overflow: "hidden",
      textOverflow: "ellipsis",
    },
    ".cm-media-embed-bar-ready": { color: ACCENT300 },
    ".cm-media-embed-bar-failed": { color: DANGER },
    ".cm-media-embed-bar-busy": { color: ACCENT300 },
    ".cm-media-embed-bar-busy::before": {
      content: "''",
      display: "inline-block",
      width: "10px",
      height: "10px",
      marginRight: "6px",
      verticalAlign: "-1px",
      borderRadius: "50%",
      border: `1.5px solid ${ACCENT300}`,
      borderTopColor: "transparent",
      animation: "cm-orb-spin 1s linear infinite",
    },
    "@keyframes cm-orb-spin": { to: { transform: "rotate(360deg)" } },
    ".cm-media-embed-btn": pill,
    ".cm-media-embed-btn:hover": { background: "rgba(233,233,237,0.07)" },
    ".cm-media-embed-btn:disabled": { opacity: "0.45", cursor: "not-allowed" },
    ".cm-media-embed-btn-primary": { borderColor: ACCENT, color: ACCENT },
    ".cm-media-embed-btn-primary:hover": {
      background: "color-mix(in srgb, " + ACCENT + " 12%, transparent)",
    },
  },
  { dark: true },
);

export const liveMarkdownExtensions = [
  editorTheme,
  syntaxHighlighting(markdownHighlightStyle),
];
