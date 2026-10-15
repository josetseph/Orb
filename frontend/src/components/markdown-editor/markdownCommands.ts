import { EditorSelection, type EditorState, type TransactionSpec } from "@codemirror/state";
import { EditorView, type EditorView as EditorViewType } from "@codemirror/view";

const PAIRS: Record<string, string> = { "[": "]", "(": ")", "{": "}", "*": "*", _: "_", "`": "`", "~": "~", "=": "=", '"': '"' };

/** The edit that wraps every selected range in `text` and its closing mark; null when typing should go ahead as usual. */
export function wrapOnType(state: EditorState, text: string): TransactionSpec | null {
  const close = PAIRS[text];
  if (!close || state.selection.ranges.every((r) => r.empty)) return null;
  return {
    ...state.changeByRange((range) =>
      range.empty
        ? { changes: { from: range.from, insert: text }, range: EditorSelection.cursor(range.from + 1) }
        : {
            changes: [
              { from: range.from, insert: text },
              { from: range.to, insert: close },
            ],
            range: EditorSelection.range(range.from + 1, range.to + 1),
          },
    ),
    // Not "input.type": a wrap should not open the completion popup.
    userEvent: "input.wrap",
    scrollIntoView: true,
  };
}

/**
 * Typing an opening mark over a selection wraps it instead of replacing it, as
 * in Obsidian; the inner text stays selected, so `[` twice makes `[[link]]`.
 */
export const wrapSelectionOnType = EditorView.inputHandler.of((view, _from, _to, text) => {
  const spec = wrapOnType(view.state, text);
  if (spec) view.dispatch(spec);
  return Boolean(spec);
});

function wrapSelection(
  view: EditorViewType,
  before: string,
  after: string = before,
  placeholder = "text",
) {
  const { state } = view;
  const changes = state.changeByRange((range) => {
    const selected = state.sliceDoc(range.from, range.to);
    const text = selected || placeholder;
    const from = range.from;
    const insert = before + text + after;
    const innerFrom = from + before.length;
    const innerTo = innerFrom + text.length;
    return {
      changes: { from: range.from, to: range.to, insert },
      range: selected
        ? EditorSelection.range(from, from + insert.length)
        : EditorSelection.range(innerFrom, innerTo),
    };
  });
  view.dispatch(changes);
  view.focus();
}

function toggleLinePrefix(view: EditorViewType, prefix: string) {
  const { state } = view;
  const changes = state.changeByRange((range) => {
    const line = state.doc.lineAt(range.from);
    const already = line.text.startsWith(prefix);
    if (already) {
      return {
        changes: {
          from: line.from,
          to: line.from + prefix.length,
          insert: "",
        },
        range: EditorSelection.range(
          Math.max(line.from, range.from - prefix.length),
          Math.max(line.from, range.to - prefix.length),
        ),
      };
    }
    return {
      changes: { from: line.from, insert: prefix },
      range: EditorSelection.range(
        range.from + prefix.length,
        range.to + prefix.length,
      ),
    };
  });
  view.dispatch(changes);
  view.focus();
}

function setHeading(view: EditorViewType, level: 1 | 2 | 3) {
  const { state } = view;
  const hashes = "#".repeat(level) + " ";
  const changes = state.changeByRange((range) => {
    const line = state.doc.lineAt(range.from);
    const stripped = line.text.replace(/^#{1,6}\s+/, "");
    const insert = hashes + stripped;
    return {
      changes: { from: line.from, to: line.to, insert },
      range: EditorSelection.cursor(line.from + insert.length),
    };
  });
  view.dispatch(changes);
  view.focus();
}

function insertBlock(view: EditorViewType, text: string, cursorOffset?: number) {
  const { state } = view;
  const pos = state.selection.main.head;
  const line = state.doc.lineAt(pos);
  const needsNewlineBefore = line.text.trim().length > 0;
  const insert = (needsNewlineBefore ? "\n" : "") + text;
  const from = needsNewlineBefore ? line.to : pos;
  view.dispatch({
    changes: { from, insert },
    selection: EditorSelection.cursor(
      from + (cursorOffset ?? insert.length),
    ),
  });
  view.focus();
}

function insertAtCursor(view: EditorViewType, text: string) {
  const { state } = view;
  const { from, to } = state.selection.main;
  view.dispatch({
    changes: { from, to, insert: text },
    selection: EditorSelection.cursor(from + text.length),
  });
  view.focus();
}

export type MarkdownAction =
  | "bold"
  | "italic"
  | "strikethrough"
  | "inlineCode"
  | "link"
  | "codeBlock"
  | "quote"
  | "h1"
  | "h2"
  | "h3"
  | "bulletList"
  | "numberedList"
  | "taskList"
  | "horizontalRule";

export function runMarkdownAction(
  view: EditorViewType | null,
  action: MarkdownAction,
) {
  if (!view) return;

  switch (action) {
    case "bold":
      wrapSelection(view, "**");
      break;
    case "italic":
      wrapSelection(view, "*");
      break;
    case "strikethrough":
      wrapSelection(view, "~~");
      break;
    case "inlineCode":
      wrapSelection(view, "`");
      break;
    case "link": {
      const { state } = view;
      const selected = state.sliceDoc(
        state.selection.main.from,
        state.selection.main.to,
      );
      const label = selected || "link text";
      wrapSelection(view, "[", "](url)", label);
      break;
    }
    case "codeBlock": {
      const { state } = view;
      const pos = state.selection.main.head;
      const line = state.doc.lineAt(pos);
      const needsNewline = line.text.trim().length > 0;
      const block = "```\ncode\n```";
      const insert = (needsNewline ? "\n" : "") + block;
      const from = needsNewline ? line.to : pos;
      const codeStart = from + (needsNewline ? 1 : 0) + 4; // after ```\n
      view.dispatch({
        changes: { from, insert },
        selection: EditorSelection.range(codeStart, codeStart + 4),
      });
      view.focus();
      break;
    }
    case "quote":
      toggleLinePrefix(view, "> ");
      break;
    case "h1":
      setHeading(view, 1);
      break;
    case "h2":
      setHeading(view, 2);
      break;
    case "h3":
      setHeading(view, 3);
      break;
    case "bulletList":
      toggleLinePrefix(view, "- ");
      break;
    case "numberedList":
      toggleLinePrefix(view, "1. ");
      break;
    case "taskList":
      toggleLinePrefix(view, "- [ ] ");
      break;
    case "horizontalRule":
      insertBlock(view, "\n---\n");
      break;
  }
}

export { insertAtCursor };
