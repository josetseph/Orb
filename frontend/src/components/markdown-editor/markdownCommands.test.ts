import { describe, expect, it } from "vitest";
import { EditorSelection, EditorState } from "@codemirror/state";
import { wrapOnType } from "./markdownCommands";

function type(state: EditorState, text: string): EditorState {
  const spec = wrapOnType(state, text);
  if (spec) return state.update(spec).state;
  const { from, to } = state.selection.main;
  return state.update({ changes: { from, to, insert: text }, selection: { anchor: from + text.length } }).state;
}

describe("wrapOnType", () => {
  it("wraps a selection twice into a wikilink and keeps it selected", () => {
    const doc = "After the Sprint, we move on.";
    const at = doc.indexOf("Sprint");
    let state = EditorState.create({ doc, selection: EditorSelection.single(at, at + 6) });
    state = type(type(state, "["), "[");
    expect(state.doc.toString()).toBe("After the [[Sprint]], we move on.");
    const sel = state.selection.main;
    expect(state.sliceDoc(sel.from, sel.to)).toBe("Sprint");
  });

  it("leaves typing without a selection, and other characters, alone", () => {
    expect(wrapOnType(EditorState.create({ doc: "a" }), "[")).toBeNull();
    const selected = EditorState.create({ doc: "abc", selection: EditorSelection.single(0, 3) });
    expect(wrapOnType(selected, "x")).toBeNull();
  });
});
