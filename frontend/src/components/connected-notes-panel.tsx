"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
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

/** Right-column "Connections": Links (wikilink neighbours) or Entities (graph subgraph). */
export function ConnectedNotesPanel({
  noteId,
  noteContent,
  kb,
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

  if (prevParams.mode !== mode || prevParams.noteId !== noteId || prevParams.kb !== kb) {
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
    if (!data || data.nodes.length === 0) {
      setNodes([]);
      return;
    }
    const n = data.nodes.length || 1;
    const cx = w / 2;
    const cy = h / 2;
    const rand = seededRandom(hashSeed(`${noteId}:${mode}:${layoutNonce}`));
    const angleOffset = rand() * Math.PI * 2;
    const r = Math.min(110, 28 + n * 10);
    setNodes(
      data.nodes.map((node, i) => {
        const angle = angleOffset + (2 * Math.PI * i) / n + (rand() - 0.5) * 0.9;
        const radius = r * (0.55 + rand() * 0.7);
        return {
          ...node,
          x: cx + radius * Math.cos(angle) + (rand() - 0.5) * 40,
          y: cy + radius * Math.sin(angle) + (rand() - 0.5) * 40,
          vx: (rand() - 0.5) * 2,
          vy: (rand() - 0.5) * 2,
        };
      }),
    );
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

  const tab = (id: GraphMode, label: string) => (
    <button
      type="button"
      onClick={() => setMode(id)}
      className={cn(
        "h-7 flex-1 rounded-[6px] text-[12px] font-medium",
        mode === id ? "bg-surface text-text" : "text-n-500 hover:text-n-300",
      )}
    >
      {label}
    </button>
  );

  return (
    <div className={cn("flex h-full min-h-0 flex-col", className)}>
      <div className="flex gap-0.5 px-3 pb-1.5 pt-3">
        {tab("note", "Links")}
        {tab("nodes", "Entities")}
      </div>

      <div className="kicker px-4 pb-1.5 pt-2">
        {mode === "note" ? "Notes linked with this one" : "Entities in this note"}
      </div>

      <div className="min-h-0 flex-1 overflow-hidden px-2">
        {loading && (
          <div className="flex h-full items-center justify-center gap-2 text-[12px] text-n-500">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading…
          </div>
        )}
        {error && <p className="p-3 text-[12.5px] text-danger-text">{error}</p>}
        {!loading && data && data.nodes.length === 0 && (
          <p className="p-4 text-center text-[12px] text-n-500">
            {mode === "note"
              ? "No wikilinks in this note yet."
              : "No knowledge-graph entities found in this note."}
          </p>
        )}
        {!loading && data && data.nodes.length > 0 && (
          <svg viewBox={`0 0 ${w} ${h}`} className="h-full w-full rounded-md bg-bg-deep/50">
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
                  stroke="var(--color-n-700)"
                  strokeWidth={1.2}
                />
              );
            })}
            {nodes.map((n) => {
              const isCenter = mode === "note" && n.id === noteId;
              const isMissing = n.type === "missing";
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
                    fill={isMissing ? "var(--color-danger)" : "var(--color-accent-800)"}
                    fillOpacity={isMissing ? 0.35 : 1}
                    stroke={
                      isMissing
                        ? "var(--color-danger)"
                        : isCenter
                          ? "var(--color-accent-200)"
                          : "var(--color-accent-300)"
                    }
                    strokeWidth={isCenter ? 2 : 1.2}
                  />
                  <text
                    x={n.x}
                    y={n.y + 22}
                    textAnchor="middle"
                    fill="var(--color-n-300)"
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

      <div className="flex items-center justify-between px-4 py-2 text-[11px] text-n-500">
        <span>
          {data
            ? `${data.nodes.length} ${mode === "note" ? "notes" : "nodes"} · ${data.edges.length} links`
            : "—"}
        </span>
        <button
          type="button"
          onClick={() => setLayoutNonce((n) => n + 1)}
          className="rounded px-1.5 py-0.5 hover:bg-n-900 hover:text-n-300"
          title="Shuffle layout"
        >
          Shuffle
        </button>
      </div>
    </div>
  );
}
