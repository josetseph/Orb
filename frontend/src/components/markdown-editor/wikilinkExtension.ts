import {
  Decoration,
  ViewPlugin,
  type DecorationSet,
  type ViewUpdate,
  EditorView,
  WidgetType,
} from "@codemirror/view";
import {
  type Completion,
  type CompletionContext,
  type CompletionResult,
} from "@codemirror/autocomplete";
import type { Note } from "@/lib/types";
import {
  suggestWikilinkNotes,
  type WikilinkSuggestion,
} from "@/app/notes/_lib/wikilinks";
import { visibleLineChunks } from "./visibleLineChunks";

const WIKILINK_RE = /\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]/g;

/** True when the cursor is inside an unclosed `[[wikilink` (not the alias). */
export function wikilinkQueryAt(
  lineText: string,
  posInLine: number,
): { fromInLine: number; query: string } | null {
  const before = lineText.slice(0, posInLine);
  const open = before.lastIndexOf("[[");
  if (open === -1) return null;
  const inner = before.slice(open + 2);
  if (inner.includes("]]") || inner.includes("|")) return null;
  return { fromInLine: open + 2, query: inner };
}

function applyWikilinkInsert(insert: string): Completion["apply"] {
  return (view, _completion, from, to) => {
    const after = view.state.doc.sliceString(
      to,
      Math.min(to + 2, view.state.doc.length),
    );
    const needsClose = after !== "]]";
    const text = needsClose ? `${insert}]]` : insert;
    view.dispatch({
      changes: { from, to, insert: text },
      // After the target text; skip past auto-inserted `]]`.
      selection: { anchor: from + insert.length + (needsClose ? 2 : 0) },
    });
  };
}

function suggestionToCompletion(s: WikilinkSuggestion): Completion {
  return {
    label: s.label,
    detail: s.detail || undefined,
    type: "text",
    boost: s.detail ? -1 : 0,
    apply: applyWikilinkInsert(s.insert),
  };
}

/**
 * Completions for `[[...` — suggestions show the note name with a folder/path
 * detail when names collide, and insert a target that resolves to that note.
 */
export function wikilinkCompletionSource(getNotes: () => Note[]) {
  return (context: CompletionContext): CompletionResult | null => {
    const line = context.state.doc.lineAt(context.pos);
    const local = wikilinkQueryAt(line.text, context.pos - line.from);
    if (!local) return null;

    const from = line.from + local.fromInLine;
    const query = local.query;
    // Activate as soon as `[[` is typed (empty query still lists notes).
    if (!context.explicit && query.includes("\n")) return null;

    const notes = getNotes();
    const matches = suggestWikilinkNotes(notes, query, 12);
    const options: Completion[] = matches.map(suggestionToCompletion);

    const trimmed = query.trim();
    if (trimmed) {
      const exact = matches.some(
        (m) =>
          m.insert.toLowerCase() === trimmed.toLowerCase() ||
          m.label.toLowerCase() === trimmed.toLowerCase(),
      );
      if (!exact) {
        options.push({
          label: trimmed,
          detail: "Create note",
          type: "text",
          boost: -10,
          apply: applyWikilinkInsert(trimmed),
        });
      }
    }

    if (!options.length) return null;
    return { from, options, filter: false };
  };
}

class WikilinkWidget extends WidgetType {
  constructor(
    readonly target: string,
    readonly label: string,
  ) {
    super();
  }

  eq(other: WikilinkWidget) {
    return this.target === other.target && this.label === other.label;
  }

  toDOM() {
    const span = document.createElement("span");
    span.className = "cm-wikilink";
    span.textContent = this.label;
    span.setAttribute("data-wikilink-target", this.target);
    span.setAttribute("data-wikilink-alias", this.label === this.target ? "" : this.label);
    span.title = this.target;
    return span;
  }

  ignoreEvent() {
    return false;
  }
}

/**
 * Highlight Obsidian-style [[wikilinks]].
 * On inactive lines: hide brackets and show only the display text (colored + underlined).
 * On the active (editing) line: show full [[syntax]] with mark styling.
 */
export function createWikilinkDecorations() {
  return ViewPlugin.fromClass(
    class {
      decorations: DecorationSet;

      constructor(view: EditorView) {
        this.decorations = this.build(view);
      }

      update(update: ViewUpdate) {
        if (
          update.docChanged ||
          update.viewportChanged ||
          update.selectionSet
        ) {
          this.decorations = this.build(update.view);
        }
      }

      build(view: EditorView): DecorationSet {
        const marks: ReturnType<Decoration["range"]>[] = [];
        const activeLine = view.state.doc.lineAt(
          view.state.selection.main.head,
        ).number;

        // Only scan the visible viewport — wikilinks are single-line.
        for (const chunk of visibleLineChunks(view)) {
          WIKILINK_RE.lastIndex = 0;
          let m: RegExpExecArray | null;
          while ((m = WIKILINK_RE.exec(chunk.text)) !== null) {
            const from = chunk.offset + m.index;
            const to = from + m[0].length;
            const target = m[1].trim();
            const alias = m[2]?.trim() || "";
            const label = alias || target;
            const line = view.state.doc.lineAt(from).number;

            if (line === activeLine) {
              marks.push(
                Decoration.mark({
                  class: "cm-wikilink",
                  attributes: {
                    "data-wikilink-target": target,
                    "data-wikilink-alias": alias,
                    title: label,
                  },
                }).range(from, to),
              );
            } else {
              marks.push(
                Decoration.replace({
                  widget: new WikilinkWidget(target, label),
                }).range(from, to),
              );
            }
          }
        }
        return Decoration.set(marks, true);
      }
    },
    { decorations: (v) => v.decorations },
  );
}

export function wikilinkClickHandler(
  onWikilinkClick?: (target: string, alias?: string) => void,
) {
  return EditorView.domEventHandlers({
    click(event, view) {
      if (!onWikilinkClick) return false;
      const target = event.target as HTMLElement | null;
      const el = target?.closest?.("[data-wikilink-target]") as HTMLElement | null;
      if (!el || !view.dom.contains(el)) return false;
      const linkTarget = el.getAttribute("data-wikilink-target");
      const alias = el.getAttribute("data-wikilink-alias") || undefined;
      if (linkTarget) {
        event.preventDefault();
        onWikilinkClick(linkTarget, alias || undefined);
        return true;
      }
      return false;
    },
  });
}

export function wikilinkHoverHandler(
  onWikilinkHover?: (
    target: string,
    rect: DOMRect,
    alias?: string,
  ) => void,
  onWikilinkLeave?: () => void,
) {
  return EditorView.domEventHandlers({
    mouseover(event, view) {
      if (!onWikilinkHover) return false;
      const target = event.target as HTMLElement | null;
      const el = target?.closest?.("[data-wikilink-target]") as HTMLElement | null;
      if (!el || !view.dom.contains(el)) return false;
      const linkTarget = el.getAttribute("data-wikilink-target");
      const alias = el.getAttribute("data-wikilink-alias") || undefined;
      if (linkTarget) {
        onWikilinkHover(linkTarget, el.getBoundingClientRect(), alias || undefined);
      }
      return false;
    },
    mouseout(event, view) {
      if (!onWikilinkLeave) return false;
      const related = event.relatedTarget as HTMLElement | null;
      const leaving =
        related &&
        view.dom.contains(related) &&
        related.closest?.("[data-wikilink-target]");
      if (!leaving) {
        onWikilinkLeave();
      }
      return false;
    },
  });
}
