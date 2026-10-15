import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { Note } from "@/lib/types";
import { useNotesPageController } from "@/app/notes/_hooks/useNotesPageController";
import type { NoteSort } from "@/app/notes/_lib/folder-tree";

/**
 * The notes workspace lives above the routes: the file explorer stays on
 * every page, and the open note, tabs and history survive a trip to Ask or
 * Graph. The notes page is a view onto this.
 */

type Tabs = { ids: string[]; active: number };
type Nav = { current: string | null; back: string[]; forward: string[]; expect: string | null };
export type CtxMenu =
  | { kind: "note"; note: Note; x: number; y: number }
  | { kind: "folder"; path: string; x: number; y: number };

const HISTORY = 10;

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage off — layout just isn't remembered */
  }
}

const tabsKey = (kb: string) => `orb:tabs:${kb || "default"}`;
const sortKey = (kb: string) => `orb:note-sort:${kb || "default"}`;

function useNotesWorkspace() {
  const c = useNotesPageController();
  const { currentKB, selection, list, autosave, handleNoteSelect, handleCreateNote } = c;
  const navigate = useNavigate();
  const location = useLocation();
  const selectedId = selection.selectedNote?.id ?? null;

  // ── per-workspace layout state ─────────────────────────────────────────
  const [kbFor, setKbFor] = useState(currentKB);
  const [tabs, setTabs] = useState<Tabs>(() => readJson(tabsKey(currentKB), { ids: [], active: 0 }));
  const [sort, setSortState] = useState<NoteSort>(() => readJson(sortKey(currentKB), "name-asc"));
  const [nav, setNav] = useState<Nav>({ current: selectedId, back: [], forward: [], expect: null });
  if (kbFor !== currentKB) {
    setKbFor(currentKB);
    setTabs(readJson(tabsKey(currentKB), { ids: [], active: 0 }));
    setSortState(readJson(sortKey(currentKB), "name-asc"));
    setNav({ current: null, back: [], forward: [], expect: null });
  }
  useEffect(() => writeJson(tabsKey(currentKB), tabs), [tabs, currentKB]);
  const setSort = useCallback(
    (next: NoteSort) => {
      setSortState(next);
      writeJson(sortKey(currentKB), next);
    },
    [currentKB],
  );

  // ── history: back/forward over the last ten notes ─────────────────────
  if (nav.current !== selectedId) {
    if (nav.expect !== null && nav.expect === selectedId) {
      setNav({ ...nav, current: selectedId, expect: null });
    } else {
      const back =
        nav.current && selectedId
          ? [...nav.back.filter((id) => id !== nav.current), nav.current].slice(-HISTORY)
          : nav.back;
      setNav({ current: selectedId, back, forward: selectedId ? [] : nav.forward, expect: null });
    }
  }

  // ── tabs: the active tab follows whatever note is open ────────────────
  const [pendingNewTab, setPendingNewTab] = useState(false);
  const [tabsFor, setTabsFor] = useState<string | null>(selectedId);
  if (tabsFor !== selectedId) {
    setTabsFor(selectedId);
    if (selectedId) {
      const at = tabs.ids.indexOf(selectedId);
      if (at >= 0) {
        if (at !== tabs.active) setTabs({ ...tabs, active: at });
      } else if (pendingNewTab || tabs.ids.length === 0) {
        setTabs({ ids: [...tabs.ids, selectedId], active: tabs.ids.length });
      } else {
        const ids = [...tabs.ids];
        ids[Math.min(tabs.active, ids.length - 1)] = selectedId;
        setTabs({ ids, active: Math.min(tabs.active, ids.length - 1) });
      }
    }
    if (pendingNewTab) setPendingNewTab(false);
  }

  const { selectedNoteRef, contentBeforeEditRef, titleBeforeEditRef } = selection;
  const { handleSaveNote } = autosave;
  const saveIfDirty = useCallback(async () => {
    const open = selectedNoteRef.current;
    if (
      open &&
      (contentBeforeEditRef.current !== open.content || titleBeforeEditRef.current !== (open.title || ""))
    ) {
      await handleSaveNote(open);
    }
  }, [selectedNoteRef, contentBeforeEditRef, titleBeforeEditRef, handleSaveNote]);

  const notesById = useMemo(() => new Map(list.notes.map((n) => [n.id, n])), [list.notes]);

  /** Open a note by id (saving the open one first); false when it no longer exists. */
  const openId = useCallback(
    async (id: string) => {
      const known = notesById.get(id);
      if (known) {
        await handleNoteSelect(known);
        return true;
      }
      await saveIfDirty();
      return selection.openNoteById(id);
    },
    [notesById, handleNoteSelect, saveIfDirty, selection],
  );

  /** Open from anywhere in the app; `newTab` keeps the current tab. */
  const openNote = useCallback(
    async (id: string, opts?: { newTab?: boolean }) => {
      if (location.pathname !== "/notes") navigate("/notes");
      if (opts?.newTab && !tabs.ids.includes(id)) setPendingNewTab(true);
      const ok = await openId(id);
      if (!ok) setPendingNewTab(false);
      return ok;
    },
    [location.pathname, navigate, openId, tabs.ids],
  );

  const closeEditor = useCallback(async () => {
    await saveIfDirty();
    selection.resetBeforeEdit();
    selection.setSelectedNote(null);
  }, [saveIfDirty, selection]);

  const activateTab = useCallback(
    async (index: number) => {
      const id = tabs.ids[index];
      if (!id || id === selectedId) return;
      if (!(await openId(id))) {
        // Deleted since: the tab goes.
        setTabs((t) => ({ ids: t.ids.filter((x) => x !== id), active: Math.min(t.active, t.ids.length - 2) }));
      }
    },
    [tabs.ids, selectedId, openId],
  );

  const closeTab = useCallback(
    async (index: number) => {
      const ids = tabs.ids.filter((_, i) => i !== index);
      const closingActive = index === tabs.active;
      const active = Math.max(0, Math.min(index < tabs.active ? tabs.active - 1 : tabs.active, ids.length - 1));
      setTabs({ ids, active });
      if (!closingActive) return;
      if (ids.length === 0) await closeEditor();
      else await openId(ids[active]);
    },
    [tabs, closeEditor, openId],
  );

  const newTab = useCallback(async () => {
    setPendingNewTab(true);
    if (location.pathname !== "/notes") navigate("/notes");
    await handleCreateNote();
  }, [handleCreateNote, location.pathname, navigate]);

  const backId = [...nav.back].reverse().find((id) => id !== selectedId) ?? null;
  const forwardId = nav.forward.find((id) => id !== selectedId) ?? null;
  const goBack = useCallback(async () => {
    if (!backId || !selectedId) return;
    setNav((n) => ({
      ...n,
      back: n.back.slice(0, n.back.lastIndexOf(backId)),
      forward: [selectedId, ...n.forward].slice(0, HISTORY),
      expect: backId,
    }));
    if (!(await openId(backId))) setNav((n) => ({ ...n, expect: null }));
  }, [backId, selectedId, openId]);
  const goForward = useCallback(async () => {
    if (!forwardId || !selectedId) return;
    setNav((n) => ({
      ...n,
      back: [...n.back, selectedId].slice(-HISTORY),
      forward: n.forward.slice(n.forward.indexOf(forwardId) + 1),
      expect: forwardId,
    }));
    if (!(await openId(forwardId))) setNav((n) => ({ ...n, expect: null }));
  }, [forwardId, selectedId, openId]);

  // ── links into a note from other pages: /notes?note=<id>, /notes?new=1 ─
  const params = new URLSearchParams(location.search);
  const noteParam = location.pathname === "/notes" ? params.get("note") : null;
  const wantsNew = location.pathname === "/notes" && params.get("new") === "1";
  useEffect(() => {
    if (!noteParam) return;
    navigate("/notes", { replace: true });
    void openId(noteParam);
  }, [noteParam, navigate, openId]);
  useEffect(() => {
    if (!wantsNew || list.isLoading) return;
    navigate("/notes", { replace: true });
    void handleCreateNote();
  }, [wantsNew, list.isLoading, navigate, handleCreateNote]);

  // ── keys: ⌘N new note, ⌘⌥←/→ history, ⌘T new tab ──────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return;
      if (e.altKey && e.key === "ArrowLeft") {
        e.preventDefault();
        void goBack();
      } else if (e.altKey && e.key === "ArrowRight") {
        e.preventDefault();
        void goForward();
      } else if (!e.altKey && !e.shiftKey && e.key.toLowerCase() === "n") {
        e.preventDefault();
        if (location.pathname !== "/notes") navigate("/notes");
        void handleCreateNote();
      } else if (!e.altKey && !e.shiftKey && e.key.toLowerCase() === "t") {
        e.preventDefault();
        void newTab();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goBack, goForward, handleCreateNote, newTab, location.pathname, navigate]);

  const [ctxMenu, setCtxMenu] = useState<CtxMenu | null>(null);

  return {
    ...c,
    notesById,
    sort,
    setSort,
    openNote,
    ctxMenu,
    setCtxMenu,
    tabs: { ids: tabs.ids, active: tabs.active, activate: activateTab, close: closeTab, create: newTab },
    history: {
      back: backId ? (notesById.get(backId)?.title || "Untitled") : null,
      forward: forwardId ? (notesById.get(forwardId)?.title || "Untitled") : null,
      goBack,
      goForward,
    },
  };
}

export type NotesWorkspace = ReturnType<typeof useNotesWorkspace>;

const NotesContext = createContext<NotesWorkspace | null>(null);

export function NotesProvider({ children }: { children: React.ReactNode }) {
  const value = useNotesWorkspace();
  return <NotesContext.Provider value={value}>{children}</NotesContext.Provider>;
}

export function useNotes(): NotesWorkspace {
  const value = useContext(NotesContext);
  if (!value) throw new Error("useNotes outside NotesProvider");
  return value;
}
