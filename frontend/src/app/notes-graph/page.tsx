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
  Maximize2,
  RotateCcw,
  Search,
  X,
} from "lucide-react";
import { cn, youtubeEmbedUrl, vimeoEmbedUrl } from "@/lib/utils";
import { GraphModeSwitch } from "@/components/graph3d";
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

const PREVIEW_PROSE = "prose-orb text-[13.5px]";

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
      <div className="mb-1 flex justify-between text-[11px] text-n-500">
        <span>{label}</span>
        <span className="font-mono text-n-600">{value}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-accent"
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
  useEffect(() => {
    controlsRef.current = controls;
  }, [controls]);

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
      return node.group === "Missing" ? "#595d6c" : "#9184d9";
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
      ctx.strokeStyle = "rgba(233,233,237,0.25)";
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
      ctx.fillStyle = `rgba(233,233,237,${labelOpacity})`;
      ctx.fillText(label, n.x || 0, (n.y || 0) + r + 2 / globalScale);
    },
    [colorFor],
  );

  const handleNodeClick = async (node: GraphNode) => {
    const requestId = ++detailRequestRef.current;
    setSelectedNode(node);
    setNodeDetails(null);

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

  const chip = (on: boolean) => cn("tag cursor-pointer", on ? "tag-accent" : "tag-neutral");

  return (
    <div className="screen relative">
      {/* Full-bleed graph host */}
      <div ref={containerRef} className="relative min-w-0 flex-1 overflow-hidden bg-bg">
        {loading || dims.w === 0 ? (
          <div className="flex h-full items-center justify-center gap-2 text-[13px] text-n-500">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading graph…
          </div>
        ) : filtered.nodes.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2.5 text-center">
            <FileText className="h-8 w-8 text-n-700" />
            <p className="text-[14px] text-n-300">No notes to graph yet</p>
            <Link href="/notes" className="btn btn-primary no-underline">
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
            linkColor={() => "rgba(233,233,237,0.14)"}
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

        {/* Top-left: search + mode */}
        <div className="absolute left-5 top-4 z-10 flex items-center gap-2">
          <div className="flex h-8 w-[260px] items-center gap-1.5 rounded-md bg-surface px-2.5 shadow-sm">
            <Search className="h-3.5 w-3.5 text-n-500" />
            <input
              value={controls.search}
              onChange={(e) => patch("search", e.target.value)}
              placeholder="Find a note"
              className="min-w-0 flex-1 bg-transparent text-[12.5px] outline-none placeholder:text-n-600"
            />
          </div>
          <GraphModeSwitch mode="notes" />
        </div>

        {/* Top-right: actions */}
        <div className="absolute right-5 top-4 z-10 flex gap-1.5">
          <button
            type="button"
            title="Fit to view"
            onClick={() => {
              userNavigatedRef.current = false;
              hasFittedRef.current = false;
              runZoomToFit(400, 60);
            }}
            className="grid h-8 w-8 place-items-center rounded-md bg-surface text-n-300 shadow-sm hover:text-text"
          >
            <Maximize2 className="h-[15px] w-[15px]" />
          </button>
          <button
            type="button"
            onClick={() => void handleRebuild()}
            disabled={rebuilding}
            className="flex h-8 items-center gap-1.5 rounded-md bg-surface px-2.5 text-[12px] text-n-300 shadow-sm hover:text-text disabled:opacity-50"
          >
            {rebuilding ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RotateCcw className="h-3.5 w-3.5" />
            )}
            Rebuild
          </button>
        </div>

        <div className="pointer-events-none absolute bottom-4 left-5 z-10 flex items-center gap-3 text-[11px] text-n-500">
          <span>
            {stats.notes} notes · {stats.missing} missing · {stats.links} wikilinks
          </span>
          <span>·</span>
          <span>Drag to pan · scroll to zoom · click a node</span>
        </div>
      </div>

      {/* Right aside: selected note, or filters & forces */}
      <aside className="flex w-[340px] shrink-0 flex-col border-l border-n-900 bg-bg-deep/40">
        {selectedNode ? (
          <>
            <div className="flex items-center gap-2 px-3 pb-2 pt-3">
              <span className="kicker flex-1 text-accent">
                {selectedNode.group === "Note" ? "Note" : "Missing note"}
              </span>
              <button
                type="button"
                onClick={() => setSelectedNode(null)}
                className="grid h-[26px] w-[26px] place-items-center rounded-[6px] text-n-400 hover:bg-n-900"
                aria-label="Close"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
            <div className="flex-1 overflow-auto px-4 pb-4">
              <h5 className="text-[17px] font-medium leading-tight">
                {selectedNode.title || selectedNode.name}
              </h5>
              {selectedNode.rel_path && (
                <p className="mt-1 truncate font-mono text-[11px] text-n-500">
                  {selectedNode.rel_path}
                </p>
              )}
              {nodeDetails?.created_at && (
                <p className="mt-1.5 flex items-center gap-1.5 text-[11.5px] text-n-500">
                  <Calendar className="h-3 w-3" />
                  {new Date(nodeDetails.created_at).toLocaleDateString(undefined, {
                    year: "numeric",
                    month: "long",
                    day: "numeric",
                  })}
                </p>
              )}

              <div className="mt-3.5">
                {detailLoading ? (
                  <div className="flex items-center gap-2 py-6 text-[12px] text-n-500">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading details…
                  </div>
                ) : selectedNode.group === "Note" && nodeDetails ? (
                  <div className="card">
                    {(nodeDetails.content || "").trim() ? (
                      <div className={PREVIEW_PROSE}>
                        <ReactMarkdown
                          remarkPlugins={[remarkGfm]}
                          urlTransform={previewUrlTransform}
                          components={{
                            a: ({ href, children }) => {
                              if (href?.startsWith("wikilink:")) {
                                return (
                                  <span className="rounded bg-accent-900 px-1 text-accent-200">
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
                                    className="my-3 aspect-video w-full rounded-md bg-bg-deep"
                                  />
                                );
                              }
                              return (
                                <a href={href} target="_blank" rel="noreferrer">
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
                                    className="my-3 aspect-video w-full rounded-md bg-bg-deep"
                                  />
                                );
                              }
                              return (
                                // eslint-disable-next-line @next/next/no-img-element
                                <img
                                  src={href || undefined}
                                  alt={alt || ""}
                                  className="my-2 max-h-48 rounded-md object-contain"
                                />
                              );
                            },
                          }}
                        >
                          {prepPreviewMarkdown(nodeDetails.content || "")}
                        </ReactMarkdown>
                      </div>
                    ) : (
                      <p className="text-[12.5px] text-n-500">(empty note)</p>
                    )}
                  </div>
                ) : (
                  <div className="card flex gap-2.5 text-[12.5px] text-n-300">
                    <Info className="mt-0.5 h-4 w-4 shrink-0 text-accent-300" />
                    <span>
                      Linked via <code className="text-accent-200">[[wikilink]]</code> but no
                      matching note exists yet.
                    </span>
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
                  className="btn btn-primary mt-4 w-full no-underline"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  Open in Notes
                </Link>
              )}
            </div>
          </>
        ) : (
          <div className="flex-1 overflow-auto px-4 py-3.5">
            <h5 className="mb-1 text-[15px] font-medium">Notes graph</h5>
            <div className="mb-3.5 text-[12px] text-n-500">
              {stats.notes} notes · {stats.links} wikilinks
            </div>

            <div className="mb-3.5 flex flex-col gap-1 text-[12px] text-n-300">
              <span className="flex items-center gap-2">
                <span className="dot bg-accent" /> Note
              </span>
              <span className="flex items-center gap-2">
                <span className="dot bg-n-700" /> Missing link target
              </span>
              {controls.groups.map((g) => (
                <span key={g.id} className="flex items-center gap-2">
                  <span className="dot" style={{ background: g.color }} />
                  <span className="truncate">{g.query}</span>
                </span>
              ))}
            </div>

            <div className="kicker mb-1.5">Show</div>
            <div className="mb-3.5 flex flex-wrap gap-1.5">
              <button
                type="button"
                onClick={() => patch("showMissing", !controls.showMissing)}
                className={chip(controls.showMissing)}
              >
                Missing
              </button>
              <button
                type="button"
                onClick={() => patch("showOrphans", !controls.showOrphans)}
                className={chip(controls.showOrphans)}
              >
                Orphans
              </button>
              <button
                type="button"
                onClick={() => patch("showArrows", !controls.showArrows)}
                className={chip(controls.showArrows)}
              >
                Arrows
              </button>
            </div>

            <div className="kicker mb-1.5">Colour groups</div>
            <div className="mb-2 flex gap-1.5">
              <input
                value={groupDraft}
                onChange={(e) => setGroupDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") addGroup();
                }}
                placeholder="Match title or path…"
                className="input min-h-[26px] py-1 text-[12px]"
              />
              <button type="button" onClick={addGroup} className="btn btn-secondary h-[26px]">
                Add
              </button>
            </div>
            {controls.groups.length > 0 && (
              <div className="mb-3.5 flex flex-col gap-1">
                {controls.groups.map((g) => (
                  <div key={g.id} className="flex items-center gap-2 rounded-[6px] bg-surface px-2 py-1.5">
                    <span className="dot" style={{ background: g.color }} />
                    <span className="min-w-0 flex-1 truncate text-[12px] text-n-300">{g.query}</span>
                    <button
                      type="button"
                      onClick={() =>
                        patch(
                          "groups",
                          controls.groups.filter((x) => x.id !== g.id),
                        )
                      }
                      className="text-n-500 hover:text-text"
                      aria-label={`Remove ${g.query}`}
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}

            <div className="kicker mb-1.5 mt-1">Display</div>
            <Slider
              label="Text fade threshold"
              value={controls.textFade}
              min={0}
              max={2}
              step={0.05}
              onChange={(v) => patch("textFade", v)}
            />
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

            <div className="kicker mb-1.5 mt-1">Forces</div>
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
              className="btn btn-secondary btn-sm mt-1 w-full"
            >
              Reset defaults
            </button>
          </div>
        )}
      </aside>
    </div>
  );
}
