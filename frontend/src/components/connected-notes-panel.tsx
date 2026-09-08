"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Link2, Loader2, Network, X } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { NotesGraphPayload } from "@/lib/types";

type GraphMode = "note" | "nodes";

interface ConnectedNotesPanelProps {
  noteId: string;
  noteContent: string;
  kb: string;
  onClose: () => void;
  onSelectNote?: (noteId: string) => void;
  onSelectEntity?: (nodeId: string, name: string) => void;
  className?: string;
}

interface SimNode {
  id: string;
  title: string;
  type: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
}

function seededRandom(seed: number) {
  let s = seed % 2147483647;
  if (s <= 0) s += 2147483646;
  return () => {
    s = (s * 16807) % 2147483647;
    return (s - 1) / 2147483646;
  };
}

function hashSeed(input: string): number {
  let h = 2166136261;
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h);
}

export function ConnectedNotesPanel({
  noteId,
  noteContent,
  kb,
  onClose,
  onSelectNote,
  onSelectEntity,
  className,
}: ConnectedNotesPanelProps) {
  const [mode, setMode] = useState<GraphMode>("note");
  const [data, setData] = useState<NotesGraphPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nodes, setNodes] = useState<SimNode[]>([]);
  const [layoutNonce, setLayoutNonce] = useState(0);
  const [prevParams, setPrevParams] = useState({ mode, noteId, kb });
  const rafRef = useRef<number | null>(null);
  const w = 320;
  const h = 420;

  if (
    prevParams.mode !== mode ||
    prevParams.noteId !== noteId ||
    prevParams.kb !== kb
  ) {
    setPrevParams({ mode, noteId, kb });
    setLoading(true);
    setError(null);
    setData(null);
    setNodes([]);
  }

  // "note" mode keys on the note id only — depending on noteContent here
  // would refetch the neighbor graph on every keystroke.
  useEffect(() => {
    if (mode !== "note") return;
    let cancelled = false;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    api
      .getNoteNeighbors(noteId, kb, { signal: controller.signal })
      .then((payload) => {
        if (cancelled) return;
        setData(payload);
        setLayoutNonce((n) => n + 1);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load graph.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [mode, noteId, kb]);

  // "nodes" mode derives from the note text — debounced while typing.
  useEffect(() => {
    if (mode !== "nodes") return;
    let cancelled = false;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    const timer = setTimeout(() => {
      setLoading(true);
      setError(null);
      api
        .getNoteEntitySubgraph(noteContent, kb, { signal: controller.signal })
        .then((payload) => {
          if (cancelled) return;
          setData(payload);
          setLayoutNonce((n) => n + 1);
        })
        .catch(() => {
          if (!cancelled) setError("Could not load graph.");
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 500);
    return () => {
      cancelled = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [mode, noteContent, kb]);

  useEffect(() => {
    if (!data) {
      setNodes([]);
      return;
    }
    if (!data || data.nodes.length === 0) return;
    const n = data.nodes.length || 1;
    const cx = w / 2;
    const cy = h / 2;
    const rand = seededRandom(hashSeed(`${noteId}:${mode}:${layoutNonce}`));
    const angleOffset = rand() * Math.PI * 2;
    const r = Math.min(110, 28 + n * 10);
    const initial: SimNode[] = data.nodes.map((node, i) => {
      const angle = angleOffset + (2 * Math.PI * i) / n + (rand() - 0.5) * 0.9;
      const radius = r * (0.55 + rand() * 0.7);
      return {
        ...node,
        x: cx + radius * Math.cos(angle) + (rand() - 0.5) * 40,
        y: cy + radius * Math.sin(angle) + (rand() - 0.5) * 40,
        vx: (rand() - 0.5) * 2,
        vy: (rand() - 0.5) * 2,
      };
    });
    setNodes(initial);
  }, [data, noteId, mode, layoutNonce]);

  useEffect(() => {
    if (!data || nodes.length === 0) return;
    let currentNodes = nodes;
    const edges = data.edges;
    let alive = true;
    let frames = 0;
    const maxFrames = 180;

    const tick = () => {
      if (!alive) return;
      frames += 1;
      let maxSpeed = 0;
      const next = currentNodes.map((nItem) => ({ ...nItem }));
      const byIdMap = new Map(next.map((nItem) => [nItem.id, nItem]));
      for (let i = 0; i < next.length; i++) {
        for (let j = i + 1; j < next.length; j++) {
          const a = next[i];
          const b = next[j];
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          const dist = Math.hypot(dx, dy) || 0.01;
          const force = 700 / (dist * dist);
          dx = (dx / dist) * force;
          dy = (dy / dist) * force;
          a.vx += dx;
          a.vy += dy;
          b.vx -= dx;
          b.vy -= dy;
        }
      }
      for (const e of edges) {
        const a = byIdMap.get(e.source);
        const b = byIdMap.get(e.target);
        if (!a || !b) continue;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.hypot(dx, dy) || 0.01;
        const ideal = 70 + (hashSeed(e.source + e.target) % 50);
        const force = (dist - ideal) * 0.025;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      }
      for (const nItem of next) {
        nItem.vx += (w / 2 - nItem.x) * 0.008;
        nItem.vy += (h / 2 - nItem.y) * 0.008;
        const damp = frames < 90 ? 0.86 : 0.92;
        nItem.vx *= damp;
        nItem.vy *= damp;
        nItem.x = Math.max(18, Math.min(w - 18, nItem.x + nItem.vx));
        nItem.y = Math.max(18, Math.min(h - 28, nItem.y + nItem.vy));
        maxSpeed = Math.max(maxSpeed, Math.hypot(nItem.vx, nItem.vy));
      }
      currentNodes = next;
      setNodes(next);
      if (frames < maxFrames && maxSpeed > 0.05) {
        rafRef.current = requestAnimationFrame(tick);
      } else {
        rafRef.current = null;
      }
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      alive = false;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [data, layoutNonce, w, h, nodes.length]);

  const byId = useMemo(() => {
    const m = new Map<string, SimNode>();
    for (const n of nodes) m.set(n.id, n);
    return m;
  }, [nodes]);

  return (
    <aside
      className={cn(
        "flex h-full w-[340px] shrink-0 flex-col border-l border-white/10 bg-black/60 backdrop-blur-xl",
        className,
      )}
    >
      <div className="flex items-center justify-between border-b border-white/10 px-3 py-3">
        <div className="flex items-center gap-2">
          <Network className="h-4 w-4 text-teal-300" />
          <div>
            <p className="text-sm font-medium text-white">Connected</p>
            <p className="text-[11px] text-white/40">
              {mode === "note" ? "Wikilink notes" : "Entities in this note"}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg p-1.5 text-white/50 hover:bg-white/10 hover:text-white"
          title="Close"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex gap-1 border-b border-white/10 p-2">
        <button
          type="button"
          onClick={() => setMode("note")}
          className={cn(
            "flex-1 rounded-lg px-2 py-1.5 text-xs font-medium",
            mode === "note"
              ? "bg-teal-500/20 text-teal-300 border border-teal-500/30"
              : "bg-white/5 text-white/50 hover:bg-white/10",
          )}
        >
          Note
        </button>
        <button
          type="button"
          onClick={() => setMode("nodes")}
          className={cn(
            "flex-1 rounded-lg px-2 py-1.5 text-xs font-medium",
            mode === "nodes"
              ? "bg-purple-500/20 text-purple-300 border border-purple-500/30"
              : "bg-white/5 text-white/50 hover:bg-white/10",
          )}
        >
          Nodes
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-hidden p-2">
        {loading && (
          <div className="flex h-full items-center justify-center gap-2 text-white/40">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading…
          </div>
        )}
        {error && <p className="p-3 text-sm text-red-300">{error}</p>}
        {!loading && data && data.nodes.length === 0 && (
          <p className="p-4 text-center text-xs text-white/40">
            {mode === "note"
              ? "No wikilinks in this note yet."
              : "No knowledge-graph entities found in this note."}
          </p>
        )}
        {!loading && data && data.nodes.length > 0 && (
          <svg viewBox={`0 0 ${w} ${h}`} className="h-full w-full rounded-xl bg-black/30">
            {(data.edges || []).map((e, i) => {
              const s = byId.get(e.source);
              const t = byId.get(e.target);
              if (!s || !t) return null;
              return (
                <line
                  key={`${e.source}-${e.target}-${i}`}
                  x1={s.x}
                  y1={s.y}
                  x2={t.x}
                  y2={t.y}
                  stroke={
                    mode === "nodes"
                      ? "rgba(196,181,253,0.35)"
                      : "rgba(94,234,212,0.35)"
                  }
                  strokeWidth={1.2}
                />
              );
            })}
            {nodes.map((n) => {
              const isCenter = mode === "note" && n.id === noteId;
              const isMissing = n.type === "missing";
              const usePurple =
                mode === "nodes" || isCenter || (!isMissing && n.type !== "note");
              return (
                <g
                  key={n.id}
                  className="cursor-pointer"
                  onClick={() => {
                    if (mode === "note" && onSelectNote && !n.id.startsWith("missing:")) {
                      onSelectNote(n.id);
                    } else if (mode === "nodes" && onSelectEntity) {
                      onSelectEntity(n.id, n.title);
                    }
                  }}
                >
                  <circle
                    cx={n.x}
                    cy={n.y}
                    r={isCenter ? 12 : isMissing ? 8 : 10}
                    fill={
                      isMissing
                        ? "rgba(248,113,113,0.35)"
                        : usePurple
                          ? "rgba(167,139,250,0.35)"
                          : "rgba(45,212,191,0.35)"
                    }
                    stroke={
                      isMissing
                        ? "#f87171"
                        : usePurple
                          ? "#c4b5fd"
                          : "#5eead4"
                    }
                    strokeWidth={isCenter ? 2 : 1.2}
                  />
                  <text
                    x={n.x}
                    y={n.y + 22}
                    textAnchor="middle"
                    fill="rgba(255,255,255,0.75)"
                    fontSize={9}
                  >
                    {(n.title || "").slice(0, 18)}
                  </text>
                </g>
              );
            })}
          </svg>
        )}
      </div>

      <div className="flex items-center justify-between border-t border-white/10 px-3 py-2 text-[11px] text-white/40">
        <div className="flex items-center gap-1.5">
          <Link2 className="h-3 w-3" />
          {data
            ? `${data.nodes.length} ${mode === "note" ? "notes" : "nodes"} · ${data.edges.length} links`
            : "—"}
        </div>
        <button
          type="button"
          onClick={() => setLayoutNonce((n) => n + 1)}
          className="rounded px-1.5 py-0.5 text-white/50 hover:bg-white/10 hover:text-white/80"
          title="Shuffle layout"
        >
          Shuffle
        </button>
      </div>
    </aside>
  );
}
