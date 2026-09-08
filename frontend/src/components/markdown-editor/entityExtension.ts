import {
  Decoration,
  EditorView,
  ViewPlugin,
  type DecorationSet,
  type ViewUpdate,
  keymap,
} from "@codemirror/view";
import {
  autocompletion,
  completionKeymap,
  type CompletionContext,
  type CompletionResult,
} from "@codemirror/autocomplete";
import { Prec } from "@codemirror/state";
import { api } from "@/lib/api";
import { visibleLineChunks } from "./visibleLineChunks";
import { wikilinkQueryAt } from "./wikilinkExtension";

export interface EntitySuggestion {
  node_id: string;
  name: string;
  node_type: string;
}

function escapeRegex(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function makeEntityRegex(name: string): RegExp {
  const escaped = escapeRegex(name);
  const firstChar = name[0];
  const lastChar = name[name.length - 1];
  const startsWithWord = /[\p{L}\p{N}]/u.test(firstChar);
  const endsWithWord = /[\p{L}\p{N}]/u.test(lastChar);

  const prefix = startsWithWord ? "(?<![\\p{L}\\p{N}])" : "";
  const suffix = endsWithWord ? "(?![\\p{L}\\p{N}])" : "";
  return new RegExp(`${prefix}${escaped}${suffix}`, "giu");
}

/** Find word-boundary ranges for known entity names in document text. */
export function findEntityRanges(
  content: string,
  entities: EntitySuggestion[],
): { from: number; to: number; entity: EntitySuggestion }[] {
  const ranges: { from: number; to: number; entity: EntitySuggestion }[] = [];
  for (const entity of entities) {
    if (!entity.name) continue;
    try {
      const re = makeEntityRegex(entity.name);
      let m: RegExpExecArray | null;
      while ((m = re.exec(content)) !== null) {
        ranges.push({
          from: m.index,
          to: m.index + entity.name.length,
          entity,
        });
      }
    } catch {
      continue;
    }
  }
  ranges.sort((a, b) => a.from - b.from);
  const out: typeof ranges = [];
  let last = 0;
  for (const r of ranges) {
    if (r.from < last) continue;
    out.push(r);
    last = r.to;
  }
  return out;
}

/**
 * Decorations for scanned entity mentions. Rebuild when the entity list changes
 * by reconfiguring the extension via a Compartment (caller handles that).
 */
export function createEntityDecorations(entities: EntitySuggestion[]) {
  return ViewPlugin.fromClass(
    class {
      decorations: DecorationSet;

      constructor(view: EditorView) {
        this.decorations = this.build(view);
      }

      update(update: ViewUpdate) {
        if (update.docChanged || update.viewportChanged) {
          this.decorations = this.build(update.view);
        }
      }

      build(view: EditorView): DecorationSet {
        const marks: ReturnType<Decoration["range"]>[] = [];
        // Only scan the visible viewport — entity names are single-line.
        for (const chunk of visibleLineChunks(view)) {
          for (const r of findEntityRanges(chunk.text, entities)) {
            marks.push(
              Decoration.mark({
                class: "cm-entity-mention",
                attributes: {
                  "data-node-id": r.entity.node_id,
                  "data-name": r.entity.name,
                },
              }).range(chunk.offset + r.from, chunk.offset + r.to),
            );
          }
        }
        return Decoration.set(marks, true);
      }
    },
    {
      decorations: (v) => v.decorations,
    },
  );
}

/**
 * Click handler: if the user clicks an entity decoration, fire onEntityClick.
 */
export function entityClickHandler(
  onEntityClick?: (nodeId: string, name: string) => void,
) {
  return EditorView.domEventHandlers({
    click(event, view) {
      if (!onEntityClick) return false;
      const target = event.target as HTMLElement | null;
      const el = target?.closest?.("[data-node-id]") as HTMLElement | null;
      if (!el || !view.dom.contains(el)) return false;
      const nodeId = el.getAttribute("data-node-id");
      const name = el.getAttribute("data-name");
      if (nodeId && name) {
        onEntityClick(nodeId, name);
        return true;
      }
      return false;
    },
  });
}

function getWordBefore(
  text: string,
  pos: number,
  minLength = 3,
): { word: string; from: number; to: number } | null {
  let start = pos;
  while (start > 0 && /\S/.test(text[start - 1])) start--;
  const word = text.slice(start, pos);
  if (word.length < minLength) return null;
  return { word, from: start, to: pos };
}

/**
 * CM6 autocomplete source that searches graph entities as the user types.
 */
export function entityCompletionSource(kb: string) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let lastReq = 0;

  return async (
    context: CompletionContext,
  ): Promise<CompletionResult | null> => {
    // Words never span lines, so only the cursor's line needs inspecting
    // (doc.toString() on every keystroke is wasteful for large notes).
    const line = context.state.doc.lineAt(context.pos);
    // Defer to wikilink completions while typing inside `[[...`.
    if (wikilinkQueryAt(line.text, context.pos - line.from)) return null;
    const local = getWordBefore(line.text, context.pos - line.from);
    const wordInfo = local && {
      word: local.word,
      from: line.from + local.from,
      to: line.from + local.to,
    };
    if (!wordInfo) return null;
    if (!context.explicit && wordInfo.word.length < 3) return null;

    const reqId = ++lastReq;
    const results = await new Promise<EntitySuggestion[]>((resolve) => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(async () => {
        try {
          const found = await api.searchEntities(wordInfo.word, kb, 6);
          resolve(found);
        } catch {
          resolve([]);
        }
      }, 250);
    });

    if (reqId !== lastReq) return null;
    if (!results.length) return null;

    return {
      from: wordInfo.from,
      options: results.map((e) => ({
        label: e.name,
        detail: e.node_type || "entity",
        type: "text",
        apply: e.name,
      })),
      filter: false,
    };
  };
}

export function entityAutocomplete(kb: string) {
  return [
    autocompletion({
      override: [entityCompletionSource(kb)],
      closeOnBlur: true,
      activateOnTyping: true,
      maxRenderedOptions: 8,
    }),
    Prec.highest(keymap.of(completionKeymap)),
  ];
}
