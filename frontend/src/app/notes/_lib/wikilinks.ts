import type { Note } from "@/lib/types";

export function normalizeLink(value: string | null | undefined): string {
  let text = (value || "").trim().toLowerCase();
  text = text.replace(/^\/+|\/+$/g, "");
  while (text.startsWith("./")) text = text.slice(2);
  return text.endsWith(".md") ? text.slice(0, -3) : text;
}

export function folderOf(relPath: string): string {
  const cut = relPath.lastIndexOf("/");
  return cut === -1 ? "" : relPath.slice(0, cut);
}

/** Vault path without `.md`, preserving original casing for inserts. */
export function noteVaultPath(note: Note): string {
  const rel = (note.rel_path || "").trim();
  if (rel) {
    return rel.replace(/\.md$/i, "").replace(/^\/+|\/+$/g, "");
  }
  return (note.title || "").trim();
}

/** Name shown in the editor and suggestion list.
 *
 * Prefers the display title: vault filenames can lag behind a retitle
 * (e.g. still ``Untitled 3.md``), and the title is what the user knows. */
export function noteDisplayName(note: Note): string {
  const title = (note.title || "").trim();
  if (title) return title;
  const path = noteVaultPath(note);
  if (!path) return "Untitled";
  return path.split("/").pop() || path;
}

/**
 * What to write inside `[[...]]` for this note (Obsidian-style):
 * bare name when unique, full vault-relative path when the basename collides.
 */
export function wikilinkInsertTarget(note: Note, notes: Note[]): string {
  const path = noteVaultPath(note);
  const base = noteDisplayName(note);
  if (!path) return base;
  const key = normalizeLink(base);
  let collisions = 0;
  for (const other of notes) {
    if (normalizeLink(noteDisplayName(other)) === key) {
      collisions += 1;
      if (collisions > 1) break;
    }
  }
  if (collisions <= 1) return base;
  // Names collide, so link by exact vault path. When the filename still lags
  // behind the title (pre-rename vaults), alias so the editor shows the title.
  const stem = path.split("/").pop() || path;
  return normalizeLink(stem) === key ? path : `${path}|${base}`;
}

export type WikilinkSuggestion = {
  note: Note;
  /** Text shown as the primary label. */
  label: string;
  /** Folder / path hint (empty for unique root notes). */
  detail: string;
  /** Exact text to place inside `[[...]]`. */
  insert: string;
};

/** Filter notes for `[[` autocomplete, preferring prefix matches then includes. */
export function suggestWikilinkNotes(
  notes: Note[],
  query: string,
  limit = 12,
): WikilinkSuggestion[] {
  const q = normalizeLink(query);
  const scored: { score: number; suggestion: WikilinkSuggestion }[] = [];

  for (const note of notes) {
    const path = noteVaultPath(note);
    const label = noteDisplayName(note);
    const pathKey = normalizeLink(path);
    const labelKey = normalizeLink(label);
    if (!pathKey && !labelKey) continue;

    let score = -1;
    if (!q) {
      score = 0;
    } else if (labelKey === q || pathKey === q) {
      score = 0;
    } else if (labelKey.startsWith(q) || pathKey.startsWith(q)) {
      score = 1;
    } else if (labelKey.includes(q) || pathKey.includes(q)) {
      score = 2;
    } else {
      continue;
    }

    const folder = folderOf(path);
    const insert = wikilinkInsertTarget(note, notes);
    const baseKey = normalizeLink(label);
    let sameName = 0;
    for (const other of notes) {
      if (normalizeLink(noteDisplayName(other)) === baseKey) {
        sameName += 1;
        if (sameName > 1) break;
      }
    }
    // When names collide, always show where the note lives (Obsidian-style).
    const detail = folder || (sameName > 1 ? "vault root" : "");

    scored.push({
      score,
      suggestion: { note, label, detail, insert },
    });
  }

  scored.sort((a, b) => {
    if (a.score !== b.score) return a.score - b.score;
    const byLabel = a.suggestion.label.localeCompare(b.suggestion.label);
    if (byLabel !== 0) return byLabel;
    return a.suggestion.insert.localeCompare(b.suggestion.insert);
  });

  return scored.slice(0, limit).map((s) => s.suggestion);
}

/** Split a wikilink target into `{ folder, title }` for creating a missing note. */
export function parseWikilinkCreateTarget(target: string): {
  folder: string;
  title: string;
} {
  const path = (target || "").replace(/\\/g, "/").trim().replace(/\.md$/i, "");
  const cleaned = path.replace(/^\/+|\/+$/g, "");
  if (!cleaned) return { folder: "", title: "Untitled" };
  const slash = cleaned.lastIndexOf("/");
  if (slash === -1) return { folder: "", title: cleaned };
  return {
    folder: cleaned.slice(0, slash),
    title: cleaned.slice(slash + 1) || "Untitled",
  };
}

/**
 * Closeness of two vault folders as `[steps, -sharedDepth]`, lower is nearer.
 *
 * Steps are tree hops (same folder 0, parent/child 1, sibling 2); shared depth
 * breaks ties so a sibling folder beats an unrelated one the same distance away.
 */
function folderProximity(sourceDir: string, targetDir: string): [number, number] {
  const a = sourceDir ? sourceDir.split("/") : [];
  const b = targetDir ? targetDir.split("/") : [];
  let shared = 0;
  while (shared < a.length && shared < b.length && a[shared] === b[shared]) shared += 1;
  return [a.length - shared + (b.length - shared), -shared];
}

type Candidate = { note: Note; relPath: string };

function pickClosest(candidates: Candidate[], sourceDir: string): Note | undefined {
  if (candidates.length === 0) return undefined;
  if (candidates.length === 1) return candidates[0].note;
  // Nearest folder wins; shallower and then alphabetical keep it deterministic.
  return [...candidates].sort((left, right) => {
    const [stepsA, sharedA] = folderProximity(sourceDir, folderOf(left.relPath));
    const [stepsB, sharedB] = folderProximity(sourceDir, folderOf(right.relPath));
    if (stepsA !== stepsB) return stepsA - stepsB;
    if (sharedA !== sharedB) return sharedA - sharedB;
    const byDepth =
      left.relPath.split("/").length - right.relPath.split("/").length;
    if (byDepth !== 0) return byDepth;
    return left.relPath.localeCompare(right.relPath);
  })[0].note;
}

/**
 * Indexed resolver for `[[targets]]`. Build once per notes list, then resolve
 * many times (click / hover) without re-scanning the vault.
 */
export class WikilinkResolver {
  private readonly byPath = new Map<string, Note>();
  private readonly byName = new Map<string, Candidate[]>();
  private readonly candidates: Candidate[] = [];

  constructor(notes: Note[]) {
    for (const note of notes) {
      const relPath = normalizeLink(note.rel_path);
      const candidate: Candidate = { note, relPath };
      this.candidates.push(candidate);
      if (relPath && !this.byPath.has(relPath)) this.byPath.set(relPath, note);
      const names = new Set([
        relPath ? relPath.split("/").pop() || "" : "",
        normalizeLink(note.title),
      ]);
      for (const name of names) {
        if (!name) continue;
        const bucket = this.byName.get(name);
        if (bucket) bucket.push(candidate);
        else this.byName.set(name, [candidate]);
      }
    }
  }

  resolve(target: string, sourceNote?: Note | null): Note | undefined {
    const key = normalizeLink(target);
    if (!key) return undefined;
    const sourceDir = folderOf(normalizeLink(sourceNote?.rel_path));

    // Exact vault path (incl. root-level basename) always wins. That lets
    // `[[photosynthesis]]` and `[[meow/photosynthesis]]` address two different
    // notes when both exist — matching what autocomplete inserts.
    const exact = this.byPath.get(key);
    if (exact) return exact;

    if (key.includes("/")) {
      const suffix = `/${key}`;
      const partial = this.candidates.filter((c) => c.relPath.endsWith(suffix));
      const best = pickClosest(partial, sourceDir);
      if (best) return best;
    }

    if (sourceDir) {
      const nested = this.byPath.get(`${sourceDir}/${key}`);
      if (nested) return nested;
    }

    const base = key.split("/").pop() || key;
    return pickClosest(this.byName.get(base) || [], sourceDir);
  }
}
