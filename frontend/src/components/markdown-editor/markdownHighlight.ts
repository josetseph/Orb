import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorView } from "@codemirror/view";
import { tags } from "@lezer/highlight";

/**
 * Orb-dark live-markdown highlighting.
 * Syntax markers stay visible; content is color-styled (Alexandrie-style).
 */
const markdownHighlightStyle = HighlightStyle.define([
  { tag: tags.heading1, color: "#60a5fa", fontSize: "1.5em", fontWeight: "700" },
  { tag: tags.heading2, color: "#a78bfa", fontSize: "1.35em", fontWeight: "700" },
  { tag: tags.heading3, color: "#2dd4bf", fontSize: "1.2em", fontWeight: "600" },
  { tag: tags.heading4, color: "#f472b6", fontSize: "1.1em", fontWeight: "600" },
  { tag: tags.heading5, color: "#c4b5fd", fontSize: "1.05em", fontWeight: "600" },
  { tag: tags.heading6, color: "#94a3b8", fontWeight: "600" },
  { tag: tags.strong, color: "#fb923c", fontWeight: "700" },
  { tag: tags.emphasis, color: "#f9a8d4", fontStyle: "italic" },
  { tag: tags.strikethrough, color: "#86efac", textDecoration: "line-through" },
  { tag: tags.link, color: "#60a5fa", textDecoration: "underline" },
  { tag: tags.url, color: "#38bdf8" },
  { tag: tags.monospace, color: "#f472b6", backgroundColor: "rgba(255,255,255,0.08)" },
  { tag: tags.quote, color: "#94a3b8", fontStyle: "italic" },
  { tag: tags.list, color: "#e2e8f0" },
  { tag: tags.meta, color: "#818cf8" },
  { tag: tags.processingInstruction, color: "#818cf8" },
  { tag: tags.contentSeparator, color: "#64748b" },
  { tag: tags.atom, color: "#a78bfa" },
  { tag: tags.bool, color: "#a78bfa" },
  { tag: tags.labelName, color: "#c4b5fd" },
]);

const editorTheme = EditorView.theme(
  {
    "&": {
      color: "rgba(255,255,255,0.9)",
      backgroundColor: "transparent",
      fontSize: "0.875rem",
      height: "100%",
    },
    ".cm-scroller": {
      fontFamily:
        "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
      lineHeight: "1.7",
      overflow: "auto",
    },
    ".cm-content": {
      caretColor: "#fff",
      padding: "0",
      minHeight: "100%",
    },
    ".cm-focused": {
      outline: "none",
    },
    ".cm-gutters": {
      backgroundColor: "transparent",
      color: "rgba(255,255,255,0.25)",
      border: "none",
      borderRight: "1px solid rgba(255,255,255,0.06)",
      paddingRight: "8px",
    },
    ".cm-activeLineGutter": {
      backgroundColor: "rgba(255,255,255,0.04)",
      color: "rgba(255,255,255,0.5)",
    },
    ".cm-activeLine": {
      backgroundColor: "rgba(255,255,255,0.03)",
    },
    ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": {
      backgroundColor: "rgba(139, 92, 246, 0.35) !important",
    },
    ".cm-cursor": {
      borderLeftColor: "#fff",
    },
    ".cm-placeholder": {
      color: "rgba(255,255,255,0.3)",
    },
    "&.cm-editor": {
      height: "100%",
    },
    "&.cm-editor.cm-focused": {
      outline: "none",
    },
    ".cm-tooltip": {
      backgroundColor: "rgba(0,0,0,0.95)",
      border: "1px solid rgba(255,255,255,0.15)",
      borderRadius: "12px",
      color: "#fff",
      boxShadow: "0 25px 50px -12px rgba(0,0,0,0.5)",
    },
    ".cm-tooltip.cm-tooltip-autocomplete > ul": {
      fontFamily: "inherit",
      maxHeight: "240px",
    },
    ".cm-tooltip.cm-tooltip-autocomplete > ul > li": {
      padding: "8px 12px",
      borderRadius: "6px",
    },
    ".cm-tooltip.cm-tooltip-autocomplete > ul > li[aria-selected]": {
      backgroundColor: "rgba(59, 130, 246, 0.25)",
      color: "#fff",
    },
    ".cm-completionLabel": {
      color: "#fff",
      fontWeight: "500",
    },
    ".cm-completionDetail": {
      color: "rgba(255,255,255,0.4)",
      fontStyle: "normal",
      marginLeft: "8px",
      fontSize: "0.7em",
      textTransform: "uppercase",
    },
    /* Entity mention decorations (purple) */
    ".cm-entity-mention": {
      backgroundColor: "rgba(139, 92, 246, 0.15)",
      color: "#c4b5fd",
      borderRadius: "3px",
      boxShadow: "inset 0 -1px rgba(196, 181, 253, 0.4)",
      cursor: "pointer",
    },
    /* Note wikilink decorations (teal — distinct from entities) */
    ".cm-wikilink": {
      backgroundColor: "transparent",
      color: "#5eead4",
      borderRadius: "0",
      boxShadow: "none",
      textDecoration: "underline",
      textUnderlineOffset: "2px",
      textDecorationColor: "rgba(94, 234, 212, 0.7)",
      cursor: "pointer",
    },
    /* Inline media embeds (images / video / audio) */
    ".cm-media-embed": {
      display: "block",
      margin: "8px 0",
      maxWidth: "100%",
    },
    ".cm-media-embed-img": {
      display: "block",
      maxWidth: "min(100%, 520px)",
      maxHeight: "360px",
      borderRadius: "10px",
      border: "1px solid rgba(255,255,255,0.12)",
      objectFit: "contain",
      background: "rgba(255,255,255,0.03)",
    },
    ".cm-media-embed-video": {
      display: "block",
      width: "min(100%, 520px)",
      minHeight: "200px",
      maxHeight: "360px",
      borderRadius: "10px",
      border: "1px solid rgba(255,255,255,0.12)",
      background: "#000",
      aspectRatio: "16 / 9",
      objectFit: "contain",
    },
    ".cm-media-embed-iframe": {
      display: "block",
      width: "min(100%, 520px)",
      aspectRatio: "16 / 9",
      height: "auto",
      minHeight: "200px",
      border: "1px solid rgba(255,255,255,0.12)",
      borderRadius: "10px",
      background: "#000",
    },
    ".cm-media-embed-pdf": {
      display: "block",
      width: "min(100%, 640px)",
      height: "480px",
      maxHeight: "70vh",
      border: "1px solid rgba(255,255,255,0.12)",
      borderRadius: "10px",
      background: "#111",
    },
    ".cm-media-embed-audio": {
      display: "block",
      width: "min(100%, 420px)",
      marginTop: "4px",
    },
    ".cm-media-embed-caption": {
      fontSize: "12px",
      color: "rgba(255,255,255,0.45)",
      marginBottom: "2px",
    },
    ".cm-media-embed-error": {
      fontSize: "12px",
      color: "rgba(248,113,113,0.9)",
      padding: "8px 10px",
      borderRadius: "8px",
      border: "1px solid rgba(248,113,113,0.25)",
      background: "rgba(248,113,113,0.08)",
    },
  },
  { dark: true },
);

export const liveMarkdownExtensions = [
  editorTheme,
  syntaxHighlighting(markdownHighlightStyle),
];
