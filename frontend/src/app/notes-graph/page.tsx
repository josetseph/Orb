"use client";

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/lib/api";
import { useKB } from "@/lib/kb-context";
import { lastNoteStorageKey } from "@/app/notes/_lib/storage-keys";
import {
  Calendar,
  ExternalLink,
  FileText,
  Info,
  Loader2,
  Network,
  RotateCcw,
  Settings2,
  X,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { cn, youtubeEmbedUrl, vimeoEmbedUrl } from "@/lib/utils";
import { ShaderBackground } from "@/components/shader-background";
import type { Note, NotesGraphPayload } from "@/lib/types";
import type { ForceGraphMethods, NodeObject } from "react-force-graph-2d";

const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
});

type GraphNode = {
  id: string;
  title: string;
  name: string;
  group: "Note" | "Missing";
  uuid?: string;
  rel_path?: string | null;
  folder?: string;
  x?: number;
  y?: number;
};

type GraphLink = {
  source: string | GraphNode;
  target: string | GraphNode;
  type: string;
};

type GraphData = {
  nodes: GraphNode[];
  links: GraphLink[];
};

type ColorGroup = {
  id: string;
  query: string;
  color: string;
};

type Controls = {
  search: string;
  showMissing: boolean;
  showOrphans: boolean;
  showArrows: boolean;
  textFade: number;
  nodeSize: number;
  linkThickness: number;
  centerForce: number;
  repelForce: number;
  linkForce: number;
  linkDistance: number;
  groups: ColorGroup[];
};

const DEFAULT_CONTROLS: Controls = {
  search: "",
  showMissing: true,
  showOrphans: true,
  showArrows: true,
  textFade: 0.55,
  nodeSize: 2.2,
  linkThickness: 2.2,
  centerForce: 0.05,
  repelForce: -120,
  linkForce: 1,
  linkDistance: 60,
  groups: [],
};

const GROUP_COLORS = [
  "#f472b6",
  "#34d399",
  "#fbbf24",
  "#a78bfa",
  "#38bdf8",
  "#fb7185",
];

function controlsKey(kb: string) {
  return `orb:notes-graph-controls:${kb || "default"}`;
}

function loadControls(kb: string): Controls {
  try {
    const raw = localStorage.getItem(controlsKey(kb));
    if (!raw) return { ...DEFAULT_CONTROLS };
    return { ...DEFAULT_CONTROLS, ...JSON.parse(raw) };
  } catch {
    return { ...DEFAULT_CONTROLS };
  }
}

function folderOf(relPath?: string | null): string {
  if (!relPath) return "";
  const parts = relPath.replace(/\\/g, "/").split("/");
  parts.pop();
  return parts.join("/");
}

/** Turn [[wikilinks]] into markdown links for the preview renderer. */
function prepPreviewMarkdown(content: string): string {
  return (content || "")
    .slice(0, 6000)
    .replace(
      /\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]/g,
      (_m, target: string, alias?: string) => {
        const t = target.trim();
        const label = (alias || target).trim();
        return `[${label}](wikilink:${encodeURIComponent(t)})`;
      },
    );
}

function previewUrlTransform(url: string): string {
  if (url.startsWith("wikilink:")) return url;
  if (url.startsWith("/vault-files/") || url.startsWith("attachments/")) {
    return url;
  }
  return /^(https?|mailto):/i.test(url) || !url.includes(":") ? url : "";
}

const PREVIEW_PROSE =
  "prose prose-invert prose-sm max-w-none " +
  "prose-headings:font-semibold prose-headings:text-white " +
  "prose-h1:mb-2 prose-h1:mt-4 prose-h1:text-xl " +
  "prose-h2:mb-2 prose-h2:mt-3 prose-h2:text-lg " +
  "prose-h3:mb-1.5 prose-h3:mt-3 prose-h3:text-base " +
  "prose-p:my-2 prose-p:leading-relaxed prose-p:text-zinc-300 " +
  "prose-strong:text-white prose-em:text-zinc-300 " +
  "prose-a:text-teal-300 prose-a:no-underline hover:prose-a:underline " +
  "prose-code:rounded prose-code:bg-white/10 prose-code:px-1 prose-code:text-pink-300 " +
  "prose-code:before:content-none prose-code:after:content-none " +
  "prose-ul:my-2 prose-ol:my-2 prose-li:my-0.5 prose-li:text-zinc-300 " +
  "prose-blockquote:border-teal-500/40 prose-blockquote:text-zinc-400 " +
  "prose-hr:border-white/10";

function toForceGraphData(payload: NotesGraphPayload): GraphData {
  const nodes: GraphNode[] = (payload.nodes || []).map((n) => ({
    id: n.id,
    title: n.title || "Untitled",
    name: n.title || "Untitled",
    group: n.type === "missing" ? "Missing" : "Note",
    uuid: n.type === "missing" ? undefined : n.id,
    rel_path: n.rel_path,
    folder: folderOf(n.rel_path),
  }));
  const idSet = new Set(nodes.map((n) => n.id));
  const links: GraphLink[] = (payload.edges || [])
    .filter((e) => idSet.has(e.source) && idSet.has(e.target))
    .map((e) => ({
      source: e.source,
      target: e.target,
      type: e.type || "wikilink",
    }));
  return { nodes, links };
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="mb-3 block">
      <div className="mb-1 flex justify-between text-[11px] text-white/55">
        <span>{label}</span>
        <span className="font-mono text-white/40">{value}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-teal-400"
      />
    </label>
  );
}

export default function NotesGraphPage() {
  const { currentKB } = useKB();
  const [raw, setRaw] = useState<GraphData>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [nodeDetails, setNodeDetails] = useState<Note | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [showControls, setShowControls] = useState(false);
  const [controls, setControls] = useState<Controls>(DEFAULT_CONTROLS);
  const [groupDraft, setGroupDraft] = useState("");
  const graphRef = useRef<ForceGraphMethods | undefined>(undefined);
  const containerRef = useRef<HTMLDivElement>(null);
  const loadGenRef = useRef(0);
  const loadAbortRef = useRef<AbortController | null>(null);
  const detailRequestRef = useRef(0);
  // Fit once on first settle — onEngineStop / resize used to re-fire zoomToFit
  // and yank the camera back whenever the user zoomed out.
  const hasFittedRef = useRef(false);
  const userNavigatedRef = useRef(false);
  const fittingRef = useRef(false);
  const [dims, setDims] = useState({ w: 0, h: 0 });
  const controlsRef = useRef(controls);
  controlsRef.current = controls;

  const runZoomToFit = useCallback((duration = 400, padding = 80) => {
    fittingRef.current = true;
    hasFittedRef.current = true;
    graphRef.current?.zoomToFit?.(duration, padding);
    window.setTimeout(() => {
      fittingRef.current = false;
    }, duration + 80);
  }, []);

  const markUserNavigated = useCallback(() => {
    if (fittingRef.current) return;
    userNavigatedRef.current = true;
  }, []);

  // Tracks which KB the current `controls` state belongs to, so the save
  // effect can't write the previous KB's controls under the new KB's key
  // during the render where currentKB changed but setControls hasn't landed.
  const controlsLoadedKbRef = useRef<string | null>(null);

  useEffect(() => {
    setControls(loadControls(currentKB));
    controlsLoadedKbRef.current = currentKB;
  }, [currentKB]);

  useEffect(() => {
    if (controlsLoadedKbRef.current !== currentKB) return;
    try {
      localStorage.setItem(controlsKey(currentKB), JSON.stringify(controls));
    } catch {
      /* ignore */
    }
  }, [controls, currentKB]);

  const patch = useCallback(<K extends keyof Controls>(key: K, value: Controls[K]) => {
    setControls((c) => ({ ...c, [key]: value }));
  }, []);

  const loadGraph = useCallback(async () => {
    const gen = ++loadGenRef.current;
    // Cancel the previous in-flight load (KB switch / rapid reloads) so the
    // backend stops building a payload nobody will render.
    loadAbortRef.current?.abort();
    const controller = new AbortController();
    loadAbortRef.current = controller;
    setLoading(true);
    hasFittedRef.current = false;
    userNavigatedRef.current = false;
    try {
      const payload = await api.getNotesGraph(currentKB, {
        signal: controller.signal,
      });
      if (gen !== loadGenRef.current) return;
      setRaw(toForceGraphData(payload));
    } catch (error) {
      if (gen !== loadGenRef.current) return;
      console.error("Failed to fetch notes graph", error);
      setRaw({ nodes: [], links: [] });
    } finally {
      if (gen === loadGenRef.current) setLoading(false);
    }
  }, [currentKB]);

  useEffect(() => {
    void loadGraph();
  }, [loadGraph]);

  const filtered = useMemo(() => {
    const q = controls.search.trim().toLowerCase();
    const degree = new Map<string, number>();
    for (const l of raw.links) {
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      degree.set(s, (degree.get(s) || 0) + 1);
      degree.set(t, (degree.get(t) || 0) + 1);
    }

    let nodes = raw.nodes.filter((n) => {
      if (!controls.showMissing && n.group === "Missing") return false;
      if (!controls.showOrphans && (degree.get(n.id) || 0) === 0) return false;
      if (!q) return true;
      return (
        n.title.toLowerCase().includes(q) ||
        (n.rel_path || "").toLowerCase().includes(q) ||
        (n.folder || "").toLowerCase().includes(q)
      );
    });

    // When searching, keep neighbors of matches so links remain meaningful
    if (q) {
      const keep = new Set(nodes.map((n) => n.id));
      for (const l of raw.links) {
        const s = typeof l.source === "object" ? l.source.id : l.source;
        const t = typeof l.target === "object" ? l.target.id : l.target;
        if (keep.has(s) || keep.has(t)) {
          keep.add(s);
          keep.add(t);
        }
      }
      nodes = raw.nodes.filter((n) => {
        if (!keep.has(n.id)) return false;
        if (!controls.showMissing && n.group === "Missing") return false;
        return true;
      });
    }

    const idSet = new Set(nodes.map((n) => n.id));
    const links = raw.links.filter((l) => {
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      return idSet.has(s) && idSet.has(t);
    });

    return { nodes, links };
  }, [raw, controls.search, controls.showMissing, controls.showOrphans]);

  // Always measure the full viewport panel (fixes half-blank canvas)
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const measure = () => {
      const rect = el.getBoundingClientRect();
      const w = Math.max(1, Math.floor(rect.width));
      const h = Math.max(1, Math.floor(rect.height));
      setDims((prev) => (prev.w === w && prev.h === h ? prev : { w, h }));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => e.preventDefault();
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // Apply d3 forces from controls
  useEffect(() => {
    const g = graphRef.current;
    if (!g) return;
    const charge = g.d3Force("charge");
    if (charge?.strength) charge.strength(controls.repelForce);
    const link = g.d3Force("link");
    if (link?.distance) link.distance(controls.linkDistance);
    if (link?.strength) link.strength(controls.linkForce);
    const center = g.d3Force("center");
    if (center?.strength) center.strength(controls.centerForce);
    g.d3ReheatSimulation?.();
  }, [
    controls.repelForce,
    controls.linkDistance,
    controls.linkForce,
    controls.centerForce,
    filtered.nodes.length,
    dims.w,
    dims.h,
  ]);

  // When filters change the visible set and the user hasn't taken over the
  // camera, allow one fresh fit for the new topology.
  useEffect(() => {
    if (userNavigatedRef.current) return;
    hasFittedRef.current = false;
  }, [filtered.nodes.length, filtered.links.length]);

  // Initial fit when canvas becomes ready or the filtered set first arrives.
  // Never re-fit after the user has zoomed/panned — that was yanking zoom out.
  useEffect(() => {
    if (!dims.w || !dims.h || filtered.nodes.length === 0) return;
    if (userNavigatedRef.current || hasFittedRef.current) return;
    const t = window.setTimeout(() => {
      if (userNavigatedRef.current || hasFittedRef.current) return;
      runZoomToFit(400, 80);
    }, 250);
    return () => window.clearTimeout(t);
  }, [dims.w, dims.h, filtered.nodes.length, filtered.links.length, runZoomToFit]);

  const colorFor = useCallback(
    (node: GraphNode) => {
      const hay = `${node.title} ${node.rel_path || ""} ${node.folder || ""}`.toLowerCase();
      for (const g of controls.groups) {
        if (g.query && hay.includes(g.query.toLowerCase())) return g.color;
      }
      return node.group === "Missing" ? "#71717a" : "#3b82f6";
    },
    [controls.groups],
  );

  const paintNode = useCallback(
    (node: NodeObject, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const n = node as unknown as GraphNode;
      const c = controlsRef.current;
      const r = (n.group === "Missing" ? 4 : 6) * c.nodeSize;
      const color = colorFor(n);

      ctx.beginPath();
      ctx.arc(n.x || 0, n.y || 0, r, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,0.35)";
      ctx.lineWidth = 1 / globalScale;
      ctx.stroke();

      // Obsidian-style: labels fade in as you zoom past the threshold
      const zoom = globalScale;
      const labelOpacity = Math.max(
        0,
        Math.min(1, (zoom - c.textFade + 0.35) / 0.7),
      );
      if (labelOpacity <= 0.02) return;

      const label = (n.title || "").slice(0, 28);
      const fontSize = 12 / globalScale;
      ctx.font = `${fontSize}px Sans-Serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillStyle = `rgba(255,255,255,${labelOpacity})`;
      ctx.fillText(label, n.x || 0, (n.y || 0) + r + 2 / globalScale);
    },
    [colorFor],
  );

  const handleNodeClick = async (node: GraphNode) => {
    const requestId = ++detailRequestRef.current;
    setSelectedNode(node);
    setNodeDetails(null);
    setShowControls(false);

    if (typeof node.x === "number" && typeof node.y === "number") {
      // Treat focus zoom as user navigation so a later engine-stop fit
      // cannot undo it.
      userNavigatedRef.current = true;
      fittingRef.current = true;
      graphRef.current?.centerAt(node.x, node.y, 800);
      graphRef.current?.zoom(3.5, 800);
      window.setTimeout(() => {
        fittingRef.current = false;
      }, 880);
    }

    if (node.group === "Note" && node.uuid) {
      setDetailLoading(true);
      try {
        const details = await api.getNote(node.uuid, currentKB);
        // Clicking A then B quickly can deliver A's response last — only the
        // latest click may populate the panel.
        if (requestId === detailRequestRef.current) setNodeDetails(details);
      } catch (error) {
        console.error("Failed to fetch note details", error);
      } finally {
        if (requestId === detailRequestRef.current) setDetailLoading(false);
      }
    }
  };

  const handleRebuild = async () => {
    try {
      setRebuilding(true);
      await api.rebuildNotesGraph(currentKB);
      await loadGraph();
    } catch (error) {
      console.error("Failed to rebuild notes graph", error);
    } finally {
      setRebuilding(false);
    }
  };

  const addGroup = () => {
    const query = groupDraft.trim();
    if (!query) return;
    const color = GROUP_COLORS[controls.groups.length % GROUP_COLORS.length];
    patch("groups", [
      ...controls.groups,
      { id: `${Date.now()}`, query, color },
    ]);
    setGroupDraft("");
  };

  const stats = useMemo(
    () => ({
      notes: filtered.nodes.filter((n) => n.group === "Note").length,
      missing: filtered.nodes.filter((n) => n.group === "Missing").length,
      links: filtered.links.length,
    }),
    [filtered],
  );

  return (
    <div className="relative h-screen w-full overflow-hidden bg-black">
      <ShaderBackground />

      {/* Full-bleed graph host — sized to the main content area */}
      <div ref={containerRef} className="absolute inset-0 z-0 overflow-hidden">
        {loading || dims.w === 0 ? (
          <div className="flex h-full w-full items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-purple-400" />
          </div>
        ) : filtered.nodes.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <FileText className="h-12 w-12 text-white/20" />
            <p className="text-white/60">No notes to graph yet</p>
            <Link
              href="/notes"
              className="rounded-xl bg-white/10 px-4 py-2 text-sm text-white hover:bg-white/15"
            >
              Open Notes
            </Link>
          </div>
        ) : (
          <ForceGraph2D
            ref={graphRef}
            width={dims.w}
            height={dims.h}
            graphData={filtered}
            nodeId="id"
            nodeLabel={() => ""}
            nodeCanvasObject={paintNode}
            nodePointerAreaPaint={(node: NodeObject, color, ctx) => {
              const n = node as unknown as GraphNode;
              const r =
                (n.group === "Missing" ? 4 : 6) *
                controlsRef.current.nodeSize;
              ctx.beginPath();
              ctx.arc(node.x ?? 0, node.y ?? 0, r + 2, 0, 2 * Math.PI);
              ctx.fillStyle = color;
              ctx.fill();
            }}
            linkColor={() => "rgba(255,255,255,0.2)"}
            linkWidth={() => controls.linkThickness}
            linkDirectionalArrowLength={controls.showArrows ? 3.5 : 0}
            linkDirectionalArrowRelPos={1}
            backgroundColor="rgba(0,0,0,0)"
            d3VelocityDecay={0.3}
            cooldownTicks={120}
            enableNodeDrag
            onNodeClick={(node: NodeObject) => void handleNodeClick(node as unknown as GraphNode)}
            onBackgroundClick={() => {
              setSelectedNode(null);
            }}
            onZoom={markUserNavigated}
            onZoomEnd={markUserNavigated}
            onEngineStop={() => {
              if (hasFittedRef.current || userNavigatedRef.current) return;
              runZoomToFit(400, 80);
            }}
          />
        )}
      </div>

      {/* Top bar */}
      <div className="pointer-events-none absolute left-6 right-6 top-6 z-10 flex items-center justify-between gap-3">
        <div className="pointer-events-auto rounded-2xl border border-white/10 bg-black/70 px-4 py-2 backdrop-blur-xl">
          <p className="text-sm font-semibold text-white">Notes graph</p>
          <p className="text-[11px] text-zinc-500">
            {stats.notes} notes · {stats.missing} missing · {stats.links}{" "}
            wikilinks
          </p>
        </div>
        <div className="pointer-events-auto flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShowControls((v) => !v)}
            className={cn(
              "flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs backdrop-blur-xl transition",
              showControls
                ? "border-teal-500/40 bg-teal-500/15 text-teal-200"
                : "border-white/10 bg-black/70 text-white/70 hover:bg-white/10 hover:text-white",
            )}
          >
            <Settings2 className="h-3.5 w-3.5" />
            Filters
          </button>
          <button
            type="button"
            onClick={() => {
              userNavigatedRef.current = false;
              hasFittedRef.current = false;
              runZoomToFit(400, 60);
            }}
            className="rounded-xl border border-white/10 bg-black/70 px-3 py-2 text-xs text-white/70 backdrop-blur-xl transition hover:bg-white/10 hover:text-white"
          >
            Fit
          </button>
          <button
            type="button"
            onClick={() => void handleRebuild()}
            disabled={rebuilding}
            className="flex items-center gap-1.5 rounded-xl border border-white/10 bg-black/70 px-3 py-2 text-xs text-white/70 backdrop-blur-xl transition hover:bg-white/10 hover:text-white disabled:opacity-50"
          >
            {rebuilding ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RotateCcw className="h-3.5 w-3.5" />
            )}
            Rebuild
          </button>
        </div>
      </div>

      {/* Legend */}
      <div className="absolute bottom-6 left-6 z-10 max-w-xs rounded-2xl border border-white/10 bg-black/80 p-5 shadow-2xl backdrop-blur-xl">
        <div className="mb-4 flex items-center gap-2.5 border-b border-white/10 pb-3">
          <div className="rounded-lg bg-gradient-to-br from-purple-500/20 to-purple-500/5 p-2">
            <Network className="h-5 w-5 text-pink-400" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-white">Neural Graph</h3>
            <p className="text-[10px] font-medium text-zinc-500">
              Notes · Wikilinks
            </p>
          </div>
        </div>
        <div className="space-y-2">
          <div className="flex items-center gap-3 rounded-lg p-1.5 text-sm text-zinc-300">
            <span className="block h-3.5 w-3.5 shrink-0 rounded-full bg-[#3b82f6] shadow-[0_0_12px_#3b82f6]" />
            <span className="font-semibold">Note</span>
          </div>
          <div className="flex items-center gap-3 rounded-lg p-1.5 text-sm text-zinc-300">
            <span className="block h-3.5 w-3.5 shrink-0 rounded-full bg-[#71717a] shadow-[0_0_12px_#71717a]" />
            <span className="font-semibold">Missing link target</span>
          </div>
          {controls.groups.map((g) => (
            <div
              key={g.id}
              className="flex items-center gap-3 rounded-lg p-1.5 text-sm text-zinc-300"
            >
              <span
                className="block h-3.5 w-3.5 shrink-0 rounded-full"
                style={{
                  background: g.color,
                  boxShadow: `0 0 12px ${g.color}`,
                }}
              />
              <span className="truncate font-semibold">{g.query}</span>
            </div>
          ))}
        </div>
        <div className="mt-3 flex items-center justify-center gap-2 border-t border-white/10 pt-3 text-[10px] text-zinc-600">
          <div className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
          <span className="font-mono font-semibold">LIVE GRAPH</span>
        </div>
      </div>

      {/* Filters / forces panel */}
      <AnimatePresence>
        {showControls && (
          <motion.div
            initial={{ opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 24 }}
            className="absolute bottom-6 right-6 top-20 z-20 flex w-[320px] max-w-[calc(100vw-7rem)] flex-col overflow-hidden rounded-2xl border border-white/10 bg-black/85 shadow-2xl backdrop-blur-xl"
          >
            <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
              <p className="text-sm font-semibold text-white">Filters & forces</p>
              <button
                type="button"
                onClick={() => setShowControls(false)}
                className="rounded-lg p-1.5 text-white/50 hover:bg-white/10 hover:text-white"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
              <input
                value={controls.search}
                onChange={(e) => patch("search", e.target.value)}
                placeholder="Search notes..."
                className="mb-3 w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-white outline-none focus:border-teal-500/40"
              />

              <div className="mb-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => patch("showMissing", !controls.showMissing)}
                  className={cn(
                    "rounded-lg border px-2.5 py-1 text-[11px]",
                    controls.showMissing
                      ? "border-teal-500/40 bg-teal-500/15 text-teal-200"
                      : "border-white/10 text-white/50",
                  )}
                >
                  Missing
                </button>
                <button
                  type="button"
                  onClick={() => patch("showOrphans", !controls.showOrphans)}
                  className={cn(
                    "rounded-lg border px-2.5 py-1 text-[11px]",
                    controls.showOrphans
                      ? "border-teal-500/40 bg-teal-500/15 text-teal-200"
                      : "border-white/10 text-white/50",
                  )}
                >
                  Orphans
                </button>
                <button
                  type="button"
                  onClick={() => patch("showArrows", !controls.showArrows)}
                  className={cn(
                    "rounded-lg border px-2.5 py-1 text-[11px]",
                    controls.showArrows
                      ? "border-teal-500/40 bg-teal-500/15 text-teal-200"
                      : "border-white/10 text-white/50",
                  )}
                >
                  Arrows
                </button>
              </div>

              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-white/40">
                Color groups
              </p>
              <div className="mb-3 flex gap-2">
                <input
                  value={groupDraft}
                  onChange={(e) => setGroupDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") addGroup();
                  }}
                  placeholder="Match title/path…"
                  className="min-w-0 flex-1 rounded-lg border border-white/10 bg-white/5 px-2 py-1.5 text-xs text-white outline-none focus:border-teal-500/40"
                />
                <button
                  type="button"
                  onClick={addGroup}
                  className="rounded-lg bg-white/10 px-2.5 text-xs text-white hover:bg-white/15"
                >
                  Add
                </button>
              </div>
              {controls.groups.length > 0 && (
                <div className="mb-4 space-y-1">
                  {controls.groups.map((g) => (
                    <div
                      key={g.id}
                      className="flex items-center gap-2 rounded-lg bg-white/5 px-2 py-1.5"
                    >
                      <span
                        className="h-2.5 w-2.5 rounded-full"
                        style={{ background: g.color }}
                      />
                      <span className="min-w-0 flex-1 truncate text-xs text-white/70">
                        {g.query}
                      </span>
                      <button
                        type="button"
                        onClick={() =>
                          patch(
                            "groups",
                            controls.groups.filter((x) => x.id !== g.id),
                          )
                        }
                        className="text-white/40 hover:text-white"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}

              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-white/40">
                Display
              </p>
              <Slider
                label="Text fade threshold"
                value={controls.textFade}
                min={0}
                max={2}
                step={0.05}
                onChange={(v) => patch("textFade", v)}
              />
              <p className="mb-3 -mt-2 text-[10px] text-white/35">
                Labels appear when zoomed past this level
              </p>
              <Slider
                label="Node size"
                value={controls.nodeSize}
                min={0.4}
                max={2.5}
                step={0.1}
                onChange={(v) => patch("nodeSize", v)}
              />
              <Slider
                label="Link thickness"
                value={controls.linkThickness}
                min={0.5}
                max={4}
                step={0.1}
                onChange={(v) => patch("linkThickness", v)}
              />

              <p className="mb-2 mt-2 text-[11px] font-semibold uppercase tracking-wide text-white/40">
                Forces
              </p>
              <Slider
                label="Center force"
                value={controls.centerForce}
                min={0}
                max={1}
                step={0.01}
                onChange={(v) => patch("centerForce", v)}
              />
              <Slider
                label="Repel force"
                value={controls.repelForce}
                min={-400}
                max={-10}
                step={5}
                onChange={(v) => patch("repelForce", v)}
              />
              <Slider
                label="Link force"
                value={controls.linkForce}
                min={0}
                max={2}
                step={0.05}
                onChange={(v) => patch("linkForce", v)}
              />
              <Slider
                label="Link distance"
                value={controls.linkDistance}
                min={20}
                max={200}
                step={5}
                onChange={(v) => patch("linkDistance", v)}
              />

              <button
                type="button"
                onClick={() => setControls({ ...DEFAULT_CONTROLS })}
                className="mt-2 w-full rounded-lg border border-white/10 py-2 text-xs text-white/60 hover:bg-white/5 hover:text-white"
              >
                Reset defaults
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Node detail panel */}
      <AnimatePresence>
        {selectedNode && !showControls && (
          <motion.div
            initial={{ x: 400, opacity: 0, scale: 0.95 }}
            animate={{ x: 0, opacity: 1, scale: 1 }}
            exit={{ x: 400, opacity: 0, scale: 0.95 }}
            transition={{ type: "spring", stiffness: 200, damping: 25 }}
            className="absolute bottom-6 right-6 top-20 z-20 flex w-[420px] max-w-[calc(100vw-7rem)] flex-col rounded-2xl border border-white/10 bg-black/80 p-6 shadow-2xl backdrop-blur-xl"
          >
            <div className="mb-5 flex items-center justify-between border-b border-white/10 pb-4">
              <div
                className={cn(
                  "rounded-lg border px-3 py-1.5 text-xs font-bold uppercase tracking-wider",
                  selectedNode.group === "Note"
                    ? "border-blue-500/30 bg-blue-500/15 text-blue-400"
                    : "border-zinc-500/30 bg-zinc-500/15 text-zinc-300",
                )}
              >
                {selectedNode.group}
              </div>
              <button
                type="button"
                onClick={() => setSelectedNode(null)}
                className="rounded-xl border border-white/5 p-2 text-zinc-400 transition hover:bg-white/5 hover:text-white"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <h2 className="mb-3 text-2xl font-bold leading-tight text-white">
              {selectedNode.title || selectedNode.name}
            </h2>

            {selectedNode.rel_path && (
              <p className="mb-3 truncate text-xs text-zinc-500">
                {selectedNode.rel_path}
              </p>
            )}

            {nodeDetails?.created_at && (
              <div className="mb-5 w-fit rounded-lg border border-white/5 bg-white/5 px-3 py-2">
                <div className="flex items-center gap-2">
                  <Calendar className="h-3.5 w-3.5 text-zinc-500" />
                  <span className="text-xs font-medium text-zinc-400">
                    {new Date(nodeDetails.created_at).toLocaleDateString(
                      undefined,
                      {
                        year: "numeric",
                        month: "long",
                        day: "numeric",
                      },
                    )}
                  </span>
                </div>
              </div>
            )}

            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-2">
              {detailLoading ? (
                <div className="flex flex-col items-center justify-center gap-3 py-12">
                  <Loader2 className="h-6 w-6 animate-spin text-purple-400" />
                  <span className="text-xs font-medium text-zinc-500">
                    Loading details...
                  </span>
                </div>
              ) : selectedNode.group === "Note" && nodeDetails ? (
                <div className="rounded-xl border border-white/10 bg-white/5 p-5">
                  <div className="mb-3 flex items-center gap-2 border-b border-white/10 pb-3">
                    <FileText className="h-4 w-4 text-pink-400" />
                    <span className="text-xs font-bold uppercase tracking-wider text-white">
                      Content
                    </span>
                  </div>
                  {(nodeDetails.content || "").trim() ? (
                    <div className={PREVIEW_PROSE}>
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        urlTransform={previewUrlTransform}
                        components={{
                          a: ({ href, children }) => {
                            if (href?.startsWith("wikilink:")) {
                              return (
                                <span className="rounded bg-teal-500/15 px-1 text-teal-300">
                                  {children}
                                </span>
                              );
                            }
                            const yt = href ? youtubeEmbedUrl(href) : null;
                            const vimeo = href ? vimeoEmbedUrl(href) : null;
                            if (yt || vimeo) {
                              return (
                                <iframe
                                  src={yt || vimeo || ""}
                                  title={String(children || "Video")}
                                  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                                  allowFullScreen
                                  className="my-3 aspect-video w-full max-w-xl rounded-lg border border-white/10 bg-black"
                                />
                              );
                            }
                            return (
                              <a
                                href={href}
                                target="_blank"
                                rel="noreferrer"
                                className="text-teal-300"
                              >
                                {children}
                              </a>
                            );
                          },
                          img: ({ src, alt }) => {
                            const href = typeof src === "string" ? src : "";
                            const yt = href ? youtubeEmbedUrl(href) : null;
                            const vimeo = href ? vimeoEmbedUrl(href) : null;
                            if (yt || vimeo) {
                              return (
                                <iframe
                                  src={yt || vimeo || ""}
                                  title={alt || "Video"}
                                  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                                  allowFullScreen
                                  className="my-3 aspect-video w-full max-w-xl rounded-lg border border-white/10 bg-black"
                                />
                              );
                            }
                            return (
                              // eslint-disable-next-line @next/next/no-img-element
                              <img
                                src={href || undefined}
                                alt={alt || ""}
                                className="my-2 max-h-48 rounded-lg border border-white/10 object-contain"
                              />
                            );
                          },
                        }}
                      >
                        {prepPreviewMarkdown(nodeDetails.content || "")}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    <p className="text-sm text-zinc-500">(empty note)</p>
                  )}
                </div>
              ) : (
                <div className="rounded-xl border border-white/10 bg-white/5 p-5">
                  <div className="mb-3 flex items-center gap-2">
                    <Info className="h-4 w-4 text-pink-400" />
                    <span className="text-xs font-bold uppercase tracking-wider text-white">
                      Missing note
                    </span>
                  </div>
                  <p className="text-sm leading-relaxed text-zinc-300">
                    Linked via <code className="text-teal-300">[[wikilink]]</code>{" "}
                    but no matching note exists yet.
                  </p>
                </div>
              )}
            </div>

            {selectedNode.group === "Note" && selectedNode.uuid && (
              <Link
                href={`/notes?note=${encodeURIComponent(selectedNode.uuid)}`}
                onClick={() => {
                  if (selectedNode.uuid) {
                    sessionStorage.setItem(
                      lastNoteStorageKey(currentKB),
                      selectedNode.uuid,
                    );
                  }
                }}
                className="mt-4 flex items-center justify-center gap-2 rounded-xl bg-linear-to-br from-purple-500 to-pink-500 px-4 py-2.5 text-sm font-medium text-white transition hover:opacity-90"
              >
                <ExternalLink className="h-4 w-4" />
                Open in Notes
              </Link>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
