import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ChevronsUpDown,
  FileText,
  MessageCircle,
  Network,
  Plus,
  Search,
  Settings,
  Wallet,
} from "lucide-react";
import { api } from "@/lib/api";
import { kbSlug, useKB } from "@/lib/kb-context";
import { lastNoteStorageKey } from "@/app/notes/_lib/storage-keys";
import type { Note } from "@/lib/types";
import { cn } from "@/lib/utils";

const OPEN_EVENT = "orb:palette";

/** Open the ⌘K palette from anywhere (the sidebar search button uses this). */
export function openCommandPalette() {
  window.dispatchEvent(new Event(OPEN_EVENT));
}

type Item = {
  key: string;
  label: string;
  hint?: string;
  icon: React.ReactNode;
  run: () => void;
};

export function CommandPalette() {
  const navigate = useNavigate();
  const { currentKB, kbs, setCurrentKB } = useKB();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [notes, setNotes] = useState<Note[]>([]);
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    setNotes([]);
  }, []);

  useEffect(() => {
    const onOpen = () => setOpen(true);
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener(OPEN_EVENT, onOpen);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener(OPEN_EVENT, onOpen);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  // Notes come from the server search so the palette also finds body text.
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api
        .getNotes(query.trim() || undefined, undefined, undefined, currentKB, {
          signal: controller.signal,
        })
        .then((rows: Note[]) => setNotes(rows.slice(0, 6)))
        .catch(() => {});
    }, query ? 200 : 0);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [open, query, currentKB]);

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const go = (href: string) => () => {
      close();
      navigate(href);
    };
    const noteItems: Item[] = notes.map((n) => ({
      key: `note:${n.id}`,
      label: n.title || "Untitled",
      hint: n.rel_path?.split("/").slice(0, -1).join("/") || undefined,
      icon: <FileText className="h-[15px] w-[15px] text-n-400" />,
      run: () => {
        sessionStorage.setItem(lastNoteStorageKey(currentKB), n.id);
        close();
        navigate(`/notes?note=${encodeURIComponent(n.id)}`);
      },
    }));
    const wsItems: Item[] = kbs
      .filter((kb) => !q || kb.name.toLowerCase().includes(q))
      .map((kb) => ({
        key: `kb:${kb.id}`,
        label: kb.name,
        hint: kbSlug(kb) === currentKB ? "current" : "workspace",
        icon: <ChevronsUpDown className="h-[15px] w-[15px] text-accent-300" />,
        run: () => {
          setCurrentKB(kbSlug(kb), kb.name);
          close();
        },
      }));
    const commands: Item[] = [
      { key: "new", label: "New note", hint: "⌘N", icon: <Plus className="h-[15px] w-[15px] text-n-400" />, run: go("/notes?new=1") },
      { key: "ask", label: "Go to Ask", icon: <MessageCircle className="h-[15px] w-[15px] text-n-400" />, run: go("/chat") },
      { key: "graph", label: "Go to Graph", icon: <Network className="h-[15px] w-[15px] text-n-400" />, run: go("/graph-3d") },
      { key: "finance", label: "Go to Finance", icon: <Wallet className="h-[15px] w-[15px] text-n-400" />, run: go("/finance") },
      { key: "settings", label: "Open settings", hint: "⌘,", icon: <Settings className="h-[15px] w-[15px] text-n-400" />, run: go("/kb") },
    ].filter((c) => !q || c.label.toLowerCase().includes(q));
    return [
      { name: "Notes", items: noteItems },
      { name: "Workspaces", items: q ? wsItems : wsItems.slice(0, 3) },
      { name: "Commands", items: commands },
    ].filter((g) => g.items.length);
  }, [query, notes, kbs, currentKB, close, navigate, setCurrentKB]);

  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups]);

  useEffect(() => {
    setCursor(0);
  }, [query, notes.length]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[90] flex justify-center bg-n-900/45 pt-[12vh]"
      onMouseDown={close}
    >
      <div
        className="h-fit w-[600px] max-w-[calc(100vw-32px)] animate-rise overflow-hidden rounded-lg bg-surface shadow-lg"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2.5 border-b border-divider px-3.5 py-3">
          <Search className="h-4 w-4 text-n-500" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") close();
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setCursor((c) => Math.min(flat.length - 1, c + 1));
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                setCursor((c) => Math.max(0, c - 1));
              }
              if (e.key === "Enter") flat[cursor]?.run();
            }}
            placeholder="Search notes, workspaces, or type a command…"
            className="flex-1 bg-transparent text-[15px] outline-none placeholder:text-n-600"
          />
          <kbd className="rounded border border-n-800 px-1.5 py-px font-mono text-[10px] text-n-600">
            esc
          </kbd>
        </div>
        <div className="max-h-[380px] overflow-auto p-1.5">
          {flat.length === 0 && (
            <p className="px-3 py-6 text-center text-[12.5px] text-n-500">Nothing matches.</p>
          )}
          {groups.map((g) => (
            <div key={g.name}>
              <div className="kicker px-2.5 pb-1 pt-2">{g.name}</div>
              {g.items.map((it) => {
                const idx = flat.indexOf(it);
                return (
                  <button
                    key={it.key}
                    type="button"
                    onMouseEnter={() => setCursor(idx)}
                    onClick={it.run}
                    className={cn("menu-item py-2 text-[13px]", idx === cursor && "bg-n-900")}
                  >
                    <span className="w-4">{it.icon}</span>
                    <span className="flex-1 truncate">{it.label}</span>
                    {it.hint && <span className="text-[11px] text-n-600">{it.hint}</span>}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
