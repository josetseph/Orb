import {
  Decoration,
  ViewPlugin,
  WidgetType,
  type DecorationSet,
  type ViewUpdate,
  EditorView,
} from "@codemirror/view";
import { StateEffect, StateField, type EditorState, type Text } from "@codemirror/state";
import { syntaxTree } from "@codemirror/language";
import type { SyntaxNode, SyntaxNodeRef } from "@lezer/common";
import { api } from "@/lib/api";
import type { Note } from "@/lib/types";
import { resolveFileUrl } from "@/lib/utils";
import { WikilinkResolver } from "@/app/notes/_lib/wikilinks";
import { mediaEmbedClaimsLink } from "./mediaEmbedExtension";
import { wikilinkParts } from "./obsidianMarkdown";

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
  /** Vault notes, for resolving `![[embeds]]`. */
  getNotes?: () => Note[];
  /** The note being edited; folder proximity when resolving embeds. */
  noteId?: string;
};

// Marks that simply vanish: `#`, `**`, `~~`, `==`, backticks, `>`.
const HIDE_NODE_TYPES = new Set([
  "HeaderMark",
  "EmphasisMark",
  "StrikethroughMark",
  "CodeMark",
  "CodeInfo",
  "QuoteMark",
  "HighlightMark",
]);
// Nodes handled as a whole element rather than by their marks.
const ELEMENT_NODE_TYPES = new Set([
  "Link", "Autolink", "URL", "ListMark", "TaskMarker", "HorizontalRule", "Escape", "HardBreak",
]);
// Marks whose "element" is the enclosing inline/heading node, not the line.
const MARK_PARENT = new Set([
  "Emphasis", "StrongEmphasis", "Strikethrough", "InlineCode", "ATXHeading1", "ATXHeading2",
  "ATXHeading3", "ATXHeading4", "ATXHeading5", "ATXHeading6", "SetextHeading1", "SetextHeading2",
  "FencedCode", "Blockquote", "Link", "Autolink", "Image", "Highlight",
]);

const CALLOUT_TYPES = new Set([
  "note", "tip", "info", "warning", "danger", "question", "quote", "example", "abstract",
]);
const CALLOUT_RE = /^\[!(\w+)\][-+]?[ \t]*/;

/** The element a node belongs to for reveal purposes. */
function elementOf(node: SyntaxNode): SyntaxNode | null {
  if (ELEMENT_NODE_TYPES.has(node.name)) return node;
  for (let p = node.parent; p; p = p.parent) if (MARK_PARENT.has(p.name)) return p;
  return node;
}

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
  const resolved = resolveFileUrl(href, kbId);
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
  constructor(
    readonly text: string,
    readonly cls = "",
  ) {
    super();
  }
  eq(other: TextWidget) {
    return other.text === this.text && other.cls === this.cls;
  }
  toDOM() {
    // CodeMirror expects an element it can mark non-editable; a bare text
    // node has no attributes to set.
    const el = document.createElement("span");
    el.textContent = this.text;
    if (this.cls) el.className = this.cls;
    return el;
  }
}

/** `{from, to}` covering whole lines when nothing else shares them, else null. */
export function soleLines(doc: Text, from: number, to: number): { from: number; to: number } | null {
  const first = doc.lineAt(from);
  const last = doc.lineAt(to);
  if (doc.sliceString(first.from, from).trim() || doc.sliceString(to, last.to).trim()) return null;
  return { from: first.from, to: last.to };
}

/** Lines from `## heading` to the next heading of the same or a higher level. */
function headingSection(content: string, heading: string): string {
  if (!heading) return content;
  const lines = content.split("\n");
  const want = heading.toLowerCase();
  let start = -1;
  let level = 0;
  for (let i = 0; i < lines.length; i++) {
    const m = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(lines[i]);
    if (!m) continue;
    if (start < 0) {
      if (m[2].toLowerCase() === want) {
        start = i;
        level = m[1].length;
      }
    } else if (m[1].length <= level) {
      return lines.slice(start, i).join("\n");
    }
  }
  return start < 0 ? `Heading "${heading}" not found.` : lines.slice(start).join("\n");
}

// Embedded note bodies by id: rendered at once on the next visit, refreshed
// in the background whenever the widget is rebuilt.
const embedBodies = new Map<string, string>();

/** `![[note#heading]]` as a read-only box: title caption, body as plain paragraphs. */
class EmbedWidget extends WidgetType {
  constructor(
    readonly target: string,
    readonly heading: string,
    readonly kb: string,
    readonly getNotes: () => Note[],
    readonly noteId?: string,
  ) {
    super();
  }
  get estimatedHeight() {
    return 120;
  }
  eq(other: EmbedWidget) {
    return other.target === this.target && other.heading === this.heading;
  }
  toDOM() {
    const box = document.createElement("div");
    box.className = "cm-md-embed";
    box.title = "Click to edit";
    const caption = document.createElement("div");
    caption.className = "cm-md-embed-title";
    const body = document.createElement("div");
    body.className = "cm-md-embed-body";
    box.append(caption, body);

    const notes = this.getNotes();
    const source = notes.find((n) => n.id === this.noteId);
    const note = this.target
      ? new WikilinkResolver(notes).resolve(this.target, source)
      : source;
    if (!note) {
      caption.textContent = this.target;
      body.textContent = "Note not found.";
      return box;
    }
    caption.textContent = (note.title || this.target) + (this.heading ? ` › ${this.heading}` : "");
    const render = (content: string) => {
      body.replaceChildren(
        ...headingSection(content, this.heading)
          .split(/\n\s*\n/)
          .map((t) => t.trim())
          .filter(Boolean)
          .map((t) => {
            const p = document.createElement("p");
            p.textContent = t;
            return p;
          }),
      );
    };
    render(embedBodies.get(note.id) ?? note.content ?? "");
    void api
      .getNote(note.id, this.kb)
      .then((fresh: Note) => {
        embedBodies.set(note.id, fresh.content);
        if (box.isConnected) render(fresh.content);
      })
      .catch(() => {});
    return box;
  }
  ignoreEvent() {
    return false;
  }
}

// ── Reveal policy ──────────────────────────────────────────────────────────
//
// Syntax is revealed per element, not per line: with the cursor inside a bold
// run only that run's `**` come back, while the link and code span next to it
// stay rendered. A block element (table, fenced code, quote) reveals whole.
//
// The reveal follows the selection as it stood at the last mouse-up. While a
// button is held the previous reveal set stays frozen, so text does not shift
// under the pointer between press and release and a click or drag lands where
// it was aimed — the detail Obsidian gets right and most clones miss.

const setPointerDown = StateEffect.define<boolean>();

const pointerHeld = StateField.define<boolean>({
  create: () => false,
  update(value, tr) {
    for (const e of tr.effects) if (e.is(setPointerDown)) return e.value;
    return value;
  },
});

/** Selection ranges the reveal is computed from: frozen while the pointer is held. */
export const revealSelection = StateField.define<readonly { from: number; to: number }[]>({
  create: (state) => state.selection.ranges.map((r) => ({ from: r.from, to: r.to })),
  update(value, tr) {
    const held = tr.state.field(pointerHeld);
    // Typing moves the cursor, so a doc change always follows the live selection.
    if (held && !tr.docChanged) return value.map((r) => ({ from: tr.changes.mapPos(r.from), to: tr.changes.mapPos(r.to) }));
    return tr.state.selection.ranges.map((r) => ({ from: r.from, to: r.to }));
  },
});

export function touchesActive(state: EditorState, from: number, to: number): boolean {
  // Inclusive on both ends: a cursor at the edge of `**bold**` is editing it.
  // Source mode has no reveal field; the live selection stands in.
  const ranges = state.field(revealSelection, false) ?? state.selection.ranges;
  return ranges.some((r) => r.to >= from && r.from <= to);
}

/** Block elements reveal whole; the cursor anywhere on their lines counts. */
function touchesActiveLines(state: EditorState, from: number, to: number): boolean {
  const first = state.doc.lineAt(from).from;
  const last = state.doc.lineAt(to).to;
  return touchesActive(state, first, last);
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
          let cls = name === "Blockquote" ? "cm-md-quote-line" : "cm-md-code-line";
          const first = doc.lineAt(node.from).number;
          const last = doc.lineAt(node.to).number;
          // `> [!type] Title` makes the quote a callout: typed frame, label
          // in place of the marker, first line as its title.
          let callout: RegExpExecArray | null = null;
          if (name === "Blockquote") {
            const afterMark = doc.sliceString(node.from + 1, doc.line(first).to);
            callout = CALLOUT_RE.exec(afterMark.trimStart());
            if (callout) {
              const type = callout[1].toLowerCase();
              cls += ` cm-md-callout cm-md-callout-${CALLOUT_TYPES.has(type) ? type : "note"}`;
              if (!touchesActiveLines(state, node.from, node.to)) {
                const from = node.from + 1 + (afterMark.length - afterMark.trimStart().length);
                marks.push(
                  Decoration.replace({ widget: new TextWidget(type, "cm-md-callout-label") })
                    .range(from, from + callout[0].length),
                );
              }
            }
          }
          for (let n = first; n <= last; n++) {
            const lineCls = callout && n === first ? `${cls} cm-md-callout-title` : cls;
            lineMarks.push(Decoration.line({ class: lineCls }).range(doc.line(n).from));
          }
          return;
        }

        // Container nodes (paragraphs, list items, headings) are never hidden
        // themselves; deciding at the mark level is what makes the reveal
        // per element. Descend.
        if (!HIDE_NODE_TYPES.has(name) && !ELEMENT_NODE_TYPES.has(name)) return;
        const element = elementOf(node.node);
        if (element && touchesActive(state, element.from, element.to)) {
          // The element being edited shows its syntax; descend so a nested
          // element inside it (a link in a heading) still decides for itself.
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
          if (touchesActiveLines(state, node.from, node.to)) return;
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
          if (touchesActiveLines(state, node.from, node.to)) return;
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
        // `>` frames a quote; it reveals with the quoted lines, not by itself.
        if (name === "QuoteMark" && touchesActiveLines(state, node.from, node.to)) return;
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
      if (
        update.docChanged ||
        update.viewportChanged ||
        update.state.field(revealSelection) !== update.startState.field(revealSelection) ||
        syntaxTree(update.state) !== syntaxTree(update.startState)
      ) {
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

  get estimatedHeight() {
    return 34 * (this.rows.length + 1) + 12;
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
function buildBlocks(
  state: EditorState,
  ranges: readonly { from: number; to: number }[],
  kb: string,
  options: LivePreviewOptions,
): DecorationSet {
  const marks: Range[] = [];
  const doc = state.doc;
  for (const { from, to } of ranges) syntaxTree(state).iterate({
    from,
    to,
    enter: (node) => {
      const name = node.name;
      // `![[note]]` alone on its line replaces that line.
      if (name === "Embed") {
        const lines = soleLines(doc, node.from, node.to);
        if (!lines || touchesActiveLines(state, node.from, node.to)) return false;
        const { name: target, heading } = wikilinkParts(doc, node.node);
        const widget = new EmbedWidget(target, heading, kb, options.getNotes ?? (() => []), options.noteId);
        marks.push(Decoration.replace({ widget, block: true }).range(lines.from, lines.to));
        return false;
      }
      if (name !== "Table") return;
      if (touchesActiveLines(state, node.from, node.to)) return false;
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

// Block widgets must come from a state field, which cannot see the viewport.
// The view plugin below reports the visible ranges into it instead, so a table
// far off screen in a long note is never rendered.
const setTableRanges = StateEffect.define<readonly { from: number; to: number }[]>();

const tableRanges = StateField.define<readonly { from: number; to: number }[]>({
  create: (state) => [{ from: 0, to: state.doc.length }],
  update(value, tr) {
    for (const e of tr.effects) if (e.is(setTableRanges)) return e.value;
    return tr.docChanged ? value.map((r) => ({ from: tr.changes.mapPos(r.from), to: tr.changes.mapPos(r.to, 1) })) : value;
  },
});

const blockField = (kb: string, options: LivePreviewOptions) =>
  StateField.define<DecorationSet>({
    create: (state) => buildBlocks(state, state.field(tableRanges), kb, options),
    update(value, tr) {
      // The parser runs async, so the Table node may not exist yet when the
      // field is created; rebuild once the tree advances.
      const treeChanged = syntaxTree(tr.state) !== syntaxTree(tr.startState);
      const revealChanged = tr.state.field(revealSelection) !== tr.startState.field(revealSelection);
      const rangesChanged = tr.state.field(tableRanges) !== tr.startState.field(tableRanges);
      return tr.docChanged || revealChanged || treeChanged || rangesChanged
        ? buildBlocks(tr.state, tr.state.field(tableRanges), kb, options)
        : value;
    },
    provide: (f) => EditorView.decorations.from(f),
  });

const tableViewport = ViewPlugin.fromClass(
  class {
    constructor(readonly view: EditorView) {
      queueMicrotask(() => this.report());
    }
    update(u: ViewUpdate) {
      if (u.viewportChanged) this.report();
    }
    report() {
      const ranges = this.view.visibleRanges.map((r) => ({ from: r.from, to: r.to }));
      this.view.dispatch({ effects: setTableRanges.of(ranges) });
    }
  },
);

const pointerTracking = EditorView.domEventHandlers({
  mousedown(event, view) {
    if (event.button === 0) view.dispatch({ effects: setPointerDown.of(true) });
    return false;
  },
  mouseup(_event, view) {
    if (view.state.field(pointerHeld)) view.dispatch({ effects: setPointerDown.of(false) });
    return false;
  },
});

export function createLivePreviewHideMarks(
  kbId = "default",
  options: LivePreviewOptions = {},
) {
  return [
    pointerHeld,
    revealSelection,
    tableRanges,
    tableViewport,
    inlinePlugin,
    blockField(kbId, options),
    pointerTracking,
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
