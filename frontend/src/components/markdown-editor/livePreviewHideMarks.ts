import {
  Decoration,
  ViewPlugin,
  WidgetType,
  type DecorationSet,
  type ViewUpdate,
  EditorView,
} from "@codemirror/view";
import { StateField, type EditorState } from "@codemirror/state";
import { syntaxTree } from "@codemirror/language";
import type { SyntaxNode, SyntaxNodeRef } from "@lezer/common";
import { encodeFileUrl, resolveFileUrl } from "@/lib/utils";
import { mediaEmbedClaimsLink } from "./mediaEmbedExtension";

/*
 * Obsidian-style live preview. Every construct the markdown parser emits is
 * handled here (see the syntax-tree dump in the commit): syntax is hidden or
 * swapped for a widget on lines that do not hold the cursor, and left raw on
 * the line being edited so nothing is ever hidden from someone changing it.
 *
 * Inline work lives in a view plugin; the table replaces several lines at
 * once, and multi-line block widgets must come from a state field.
 */

type Range = ReturnType<Decoration["range"]>;

export type LivePreviewOptions = {
  /** Open a vault attachment in the app (preview modal) instead of a new tab. */
  onOpenFile?: (url: string, filename: string) => void;
};

// Marks that simply vanish: `#`, `**`, `~~`, backticks, `>`, setext underlines.
const HIDE_NODE_TYPES = new Set([
  "HeaderMark",
  "EmphasisMark",
  "StrikethroughMark",
  "CodeMark",
  "CodeInfo",
  "QuoteMark",
]);

function decodeSafe(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

/** Follow a link's href: the web goes out, a vault file opens in the app. */
function openHref(
  rawHref: string,
  kbId: string,
  onOpenFile?: (url: string, filename: string) => void,
) {
  const href = rawHref.trim();
  if (!href) return;
  if (/^(https?|mailto):/i.test(href)) {
    // The desktop shell denies in-app windows and hands http(s)/mailto to the
    // OS browser; in a plain browser this is a new tab.
    window.open(href, "_blank", "noopener,noreferrer");
    return;
  }
  const resolved = encodeFileUrl(resolveFileUrl(href, kbId));
  const filename = decodeSafe(resolved.split("/").pop() || href);
  if (onOpenFile) onOpenFile(resolved, filename);
  else window.open(resolved, "_blank", "noopener,noreferrer");
}

function linkMark(href: string): Decoration {
  return Decoration.mark({
    class: "cm-md-link",
    attributes: { "data-href": href, title: `${href}\nClick to open · ⌥-click to edit` },
  });
}

class BulletWidget extends WidgetType {
  eq() {
    return true;
  }
  toDOM() {
    const el = document.createElement("span");
    el.className = "cm-md-bullet";
    return el;
  }
}

class TaskWidget extends WidgetType {
  constructor(
    readonly checked: boolean,
    readonly from: number,
    readonly to: number,
  ) {
    super();
  }
  eq(other: TaskWidget) {
    return other.checked === this.checked && other.from === this.from && other.to === this.to;
  }
  toDOM(view: EditorView) {
    const el = document.createElement("input");
    el.type = "checkbox";
    el.checked = this.checked;
    el.className = "cm-md-task";
    el.setAttribute("aria-label", this.checked ? "Mark task not done" : "Mark task done");
    el.addEventListener("mousedown", (e) => {
      e.preventDefault();
      view.dispatch({
        changes: { from: this.from, to: this.to, insert: this.checked ? "[ ]" : "[x]" },
      });
    });
    return el;
  }
  ignoreEvent() {
    return true;
  }
}

class RuleWidget extends WidgetType {
  eq() {
    return true;
  }
  toDOM() {
    const el = document.createElement("span");
    el.className = "cm-md-hr";
    return el;
  }
}

class TextWidget extends WidgetType {
  constructor(readonly text: string) {
    super();
  }
  eq(other: TextWidget) {
    return other.text === this.text;
  }
  toDOM() {
    // CodeMirror expects an element it can mark non-editable; a bare text
    // node has no attributes to set.
    const el = document.createElement("span");
    el.textContent = this.text;
    return el;
  }
}

/** Lines the cursor (or a selection) touches keep their raw markdown. */
function activeLines(state: EditorState): { from: number; to: number }[] {
  return state.selection.ranges.map((r) => ({
    from: state.doc.lineAt(r.from).number,
    to: state.doc.lineAt(r.to).number,
  }));
}

function touchesActive(state: EditorState, from: number, to: number): boolean {
  const first = state.doc.lineAt(from).number;
  const last = state.doc.lineAt(to).number;
  return activeLines(state).some((a) => a.to >= first && a.from <= last);
}

/**
 * `[label](url)` → just the label, styled and clickable.
 *
 * Hiding only the brackets would glue the label to the URL, so the URL (and
 * any link title) goes too, and the label carries the href for the click
 * handler. Links the media plugin turns into a player are left alone — one
 * range, one decoration.
 */
function hideLink(state: EditorState, node: SyntaxNode, marks: Range[]) {
  const linkMarks: { from: number; to: number }[] = [];
  let url: { from: number; to: number } | null = null;
  let title: { from: number; to: number } | null = null;
  for (let c = node.firstChild; c; c = c.nextSibling) {
    if (c.name === "LinkMark") linkMarks.push({ from: c.from, to: c.to });
    else if (c.name === "URL") url = { from: c.from, to: c.to };
    else if (c.name === "LinkTitle") title = { from: c.from, to: c.to };
  }
  // Reference links (`[a][b]`) and anything half-typed keep their syntax.
  if (linkMarks.length < 2 || !url) return;
  const labelFrom = linkMarks[0].to;
  const labelTo = linkMarks[1].from;
  if (labelTo <= labelFrom) return;

  const label = state.doc.sliceString(labelFrom, labelTo);
  const rawUrl = state.doc.sliceString(url.from, url.to).trim();
  if (!rawUrl || mediaEmbedClaimsLink(false, label, rawUrl)) return;

  for (const lm of linkMarks) marks.push(Decoration.replace({}).range(lm.from, lm.to));
  marks.push(Decoration.replace({}).range(url.from, url.to));
  if (title) marks.push(Decoration.replace({}).range(title.from, title.to));
  marks.push(linkMark(rawUrl).range(labelFrom, labelTo));
}

/** `<https://x>` → `https://x`, clickable. */
function hideAutolink(state: EditorState, node: SyntaxNode, marks: Range[]) {
  for (let c = node.firstChild; c; c = c.nextSibling) {
    if (c.name === "LinkMark") marks.push(Decoration.replace({}).range(c.from, c.to));
    else if (c.name === "URL") {
      const href = state.doc.sliceString(c.from, c.to);
      marks.push(linkMark(href).range(c.from, c.to));
    }
  }
}

function buildInline(view: EditorView): DecorationSet {
  const { state } = view;
  const marks: Range[] = [];
  const lineMarks: Range[] = [];
  const doc = state.doc;

  for (const { from, to } of view.visibleRanges) {
    syntaxTree(state).iterate({
      from,
      to,
      enter: (node: SyntaxNodeRef) => {
        const name = node.name;

        // Block framing applies whether or not the cursor is inside: the
        // frame is what tells you where the block starts and ends.
        if (name === "Blockquote" || name === "FencedCode") {
          const cls = name === "Blockquote" ? "cm-md-quote-line" : "cm-md-code-line";
          const first = doc.lineAt(node.from).number;
          const last = doc.lineAt(node.to).number;
          for (let n = first; n <= last; n++) {
            lineMarks.push(Decoration.line({ class: cls }).range(doc.line(n).from));
          }
          return;
        }

        if (touchesActive(state, node.from, node.to)) {
          // Raw for editing; descend so nested block framing still applies.
          return;
        }

        if (name === "Link") {
          hideLink(state, node.node, marks);
          return false;
        }
        if (name === "Autolink") {
          hideAutolink(state, node.node, marks);
          return false;
        }
        if (name === "URL") {
          // A bare URL outside any link syntax.
          marks.push(linkMark(doc.sliceString(node.from, node.to)).range(node.from, node.to));
          return;
        }
        if (name === "ListMark") {
          const mark = doc.sliceString(node.from, node.to);
          // Ordered numbers stay (they carry meaning); bullets become dots.
          // The space after the mark is swallowed so text sits at the margin.
          if (!/^\d/.test(mark)) {
            const to = doc.sliceString(node.to, node.to + 1) === " " ? node.to + 1 : node.to;
            marks.push(Decoration.replace({ widget: new BulletWidget() }).range(node.from, to));
          } else {
            marks.push(Decoration.mark({ class: "cm-md-ordinal" }).range(node.from, node.to));
          }
          return;
        }
        if (name === "TaskMarker") {
          const checked = /x/i.test(doc.sliceString(node.from, node.to));
          const to = doc.sliceString(node.to, node.to + 1) === " " ? node.to + 1 : node.to;
          marks.push(
            Decoration.replace({ widget: new TaskWidget(checked, node.from, node.to) }).range(
              node.from,
              to,
            ),
          );
          return;
        }
        if (name === "HorizontalRule") {
          marks.push(Decoration.replace({ widget: new RuleWidget() }).range(node.from, node.to));
          return;
        }
        if (name === "Escape") {
          // `\*` shows as `*`.
          marks.push(
            Decoration.replace({ widget: new TextWidget(doc.sliceString(node.from + 1, node.to)) })
              .range(node.from, node.to),
          );
          return;
        }
        if (name === "HardBreak") {
          // The `\` form; two trailing spaces are already invisible.
          if (doc.sliceString(node.from, node.from + 1) === "\\") {
            marks.push(Decoration.replace({}).range(node.from, node.from + 1));
          }
          return;
        }
        if (!HIDE_NODE_TYPES.has(name) || node.to <= node.from) return;
        let to = node.to;
        // `#`/`>` are followed by one space that would otherwise indent the text.
        if ((name === "HeaderMark" || name === "QuoteMark") && doc.sliceString(to, to + 1) === " ") {
          to += 1;
        }
        marks.push(Decoration.replace({}).range(node.from, to));
      },
    });
  }
  return Decoration.set([...lineMarks, ...marks], true);
}

const inlinePlugin = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet;
    constructor(view: EditorView) {
      this.decorations = buildInline(view);
    }
    update(update: ViewUpdate) {
      if (update.docChanged || update.selectionSet || update.viewportChanged) {
        this.decorations = buildInline(update.view);
      }
    }
  },
  { decorations: (v) => v.decorations },
);

// ── Tables ─────────────────────────────────────────────────────────────────

class TableWidget extends WidgetType {
  constructor(
    readonly head: string[],
    readonly rows: string[][],
    readonly from: number,
  ) {
    super();
  }
  eq(other: TableWidget) {
    return (
      other.from === this.from &&
      other.head.join("\0") === this.head.join("\0") &&
      other.rows.map((r) => r.join("\0")).join("\n") ===
        this.rows.map((r) => r.join("\0")).join("\n")
    );
  }
  toDOM(view: EditorView) {
    const wrap = document.createElement("div");
    wrap.className = "cm-md-table";
    wrap.title = "Click to edit this table";
    const table = document.createElement("table");
    const thead = document.createElement("thead");
    const hr = document.createElement("tr");
    for (const h of this.head) {
      const th = document.createElement("th");
      th.textContent = h;
      hr.appendChild(th);
    }
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = document.createElement("tbody");
    for (const row of this.rows) {
      const tr = document.createElement("tr");
      for (let i = 0; i < this.head.length; i++) {
        const td = document.createElement("td");
        td.textContent = row[i] ?? "";
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    wrap.addEventListener("mousedown", (e) => {
      e.preventDefault();
      view.dispatch({ selection: { anchor: this.from } });
      view.focus();
    });
    return wrap;
  }
  ignoreEvent() {
    return true;
  }
}

function cellsOf(state: EditorState, row: SyntaxNode): string[] {
  const out: string[] = [];
  for (let c = row.firstChild; c; c = c.nextSibling) {
    if (c.name === "TableCell") out.push(state.doc.sliceString(c.from, c.to).trim());
  }
  return out;
}

// ponytail: cell text is shown raw (no bold/links inside cells); render
// cells through the inline pass if tables ever carry more than plain values.
function buildTables(state: EditorState): DecorationSet {
  const marks: Range[] = [];
  syntaxTree(state).iterate({
    enter: (node) => {
      if (node.name !== "Table") return;
      if (touchesActive(state, node.from, node.to)) return false;
      let head: string[] = [];
      const rows: string[][] = [];
      for (let c = node.node.firstChild; c; c = c.nextSibling) {
        if (c.name === "TableHeader") head = cellsOf(state, c);
        else if (c.name === "TableRow") rows.push(cellsOf(state, c));
      }
      if (head.length === 0) return false;
      marks.push(
        Decoration.replace({ widget: new TableWidget(head, rows, node.from), block: true }).range(
          node.from,
          node.to,
        ),
      );
      return false;
    },
  });
  return Decoration.set(marks, true);
}

const tableField = StateField.define<DecorationSet>({
  create: buildTables,
  update(value, tr) {
    return tr.docChanged || tr.selection ? buildTables(tr.state) : value;
  },
  provide: (f) => EditorView.decorations.from(f),
});

export function createLivePreviewHideMarks(
  kbId = "default",
  options: LivePreviewOptions = {},
) {
  return [
    inlinePlugin,
    tableField,
    EditorView.domEventHandlers({
      mousedown(event) {
        // ⌥-click places the cursor instead, so link text stays editable.
        if (event.button !== 0 || event.altKey) return false;
        const target = event.target as Element | null;
        const el = target?.closest?.(".cm-md-link") as HTMLElement | null;
        const href = el?.dataset.href;
        if (!href) return false;
        event.preventDefault();
        openHref(href, kbId, options.onOpenFile);
        return true;
      },
    }),
  ];
}
