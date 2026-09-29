import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, X } from "lucide-react";
import type { ForceGraphMethods, LinkObject, NodeObject } from "react-force-graph-2d";
import { api } from "@/lib/api";
import { cn, useDebounced } from "@/lib/utils";
import type { NotesGraphPayload } from "@/lib/types";

const ForceGraph2D = lazy(() => import("react-force-graph-2d"));

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

type GraphNode = { id: string; title: string; type: string; hop: number; x?: number; y?: number };

// The canvas cannot read CSS variables per draw; resolve the theme once.
function themeColor(name: string, fallback: string) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** In-note graph: the open note's links (1 or 2 hops, both directions) or the entities it mentions. */
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
  const [depth, setDepth] = useState<1 | 2>(1);
  const [data, setData] = useState<NotesGraphPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // "nodes" mode derives from the note text — debounced while typing.
  const debouncedContent = useDebounced(noteContent, 500);
  const [prevParams, setPrevParams] = useState({ mode, noteId, kb, depth, debouncedContent });
  if (
    prevParams.mode !== mode ||
    prevParams.noteId !== noteId ||
    prevParams.kb !== kb ||
    (mode === "note" && prevParams.depth !== depth) ||
    (mode === "nodes" && prevParams.debouncedContent !== debouncedContent)
  ) {
    setPrevParams({ mode, noteId, kb, depth, debouncedContent });
    setLoading(true);
    setError(null);
    setData(null);
  }

  // "note" mode keys on the note id (and depth) only — the text changing
  // would otherwise refetch the link graph on every keystroke.
  useEffect(() => {
    if (mode !== "note") return;
    let cancelled = false;
    const controller = new AbortController();
    api
      .getNoteNeighbors(noteId, kb, { signal: controller.signal }, depth)
      .then((payload) => !cancelled && setData(payload))
      .catch(() => !cancelled && setError("Could not load graph."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [mode, noteId, kb, depth]);

  useEffect(() => {
    if (mode !== "nodes") return;
    let cancelled = false;
    const controller = new AbortController();
    api
      .getNoteEntitySubgraph(debouncedContent, kb, { signal: controller.signal })
      .then((payload) => !cancelled && setData(payload))
      .catch(() => !cancelled && setError("Could not load graph."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [mode, debouncedContent, kb]);

  // Fill the panel; the canvas needs pixel dimensions.
  const boxRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const graphData = useMemo(() => {
    if (!data) return { nodes: [] as GraphNode[], links: [] as { source: string; target: string }[] };
    return {
      nodes: data.nodes.map((n) => ({ ...n, hop: n.id === noteId ? 0 : (n.hop ?? 1) })),
      links: data.edges.map((e) => ({ source: e.source, target: e.target })),
    };
  }, [data, noteId]);

  const colors = useMemo(
    () => ({
      center: themeColor("--color-accent-200", "#e7e5fe"),
      near: themeColor("--color-accent-400", "#b5abfc"),
      far: themeColor("--color-n-600", "#75798c"),
      missing: themeColor("--color-danger", "#e0786b"),
      label: themeColor("--color-n-300", "#cfd3e5"),
      link: "rgba(233,233,237,0.16)",
    }),
    [],
  );

  const graphRef = useRef<ForceGraphMethods | undefined>(undefined);
  const fitted = useRef(false);
  useEffect(() => {
    fitted.current = false;
  }, [data]);

  const paintNode = useCallback(
    (node: NodeObject, ctx: CanvasRenderingContext2D, scale: number) => {
      const n = node as unknown as GraphNode;
      const missing = n.type === "missing" || n.id.startsWith("missing:");
      const r = n.hop === 0 ? 7 : n.hop === 1 ? 5 : 4;
      ctx.beginPath();
      ctx.arc(n.x ?? 0, n.y ?? 0, r, 0, 2 * Math.PI);
      ctx.fillStyle = missing ? colors.missing : n.hop === 0 ? colors.center : n.hop === 1 ? colors.near : colors.far;
      ctx.globalAlpha = missing ? 0.45 : 1;
      ctx.fill();
      ctx.globalAlpha = 1;
      // Second-hop labels appear once zoomed in, so a busy graph stays readable.
      if (n.hop === 2 && scale < 1.6) return;
      const fontSize = (n.hop === 0 ? 12 : 11) / scale;
      ctx.font = `${n.hop === 0 ? "600 " : ""}${fontSize}px Inter, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillStyle = colors.label;
      ctx.fillText((n.title || "Untitled").slice(0, 26), n.x ?? 0, (n.y ?? 0) + r + 2 / scale);
    },
    [colors],
  );

  const onNodeClick = useCallback(
    (node: NodeObject) => {
      const n = node as unknown as GraphNode;
      if (mode === "note") {
        if (n.id !== noteId && !n.id.startsWith("missing:")) onSelectNote?.(n.id);
      } else {
        onSelectEntity?.(n.id, n.title);
      }
    },
    [mode, noteId, onSelectNote, onSelectEntity],
  );

  const pill = (active: boolean) =>
    cn("h-7 flex-1 rounded-[6px] text-[12px] font-medium", active ? "bg-surface text-text" : "text-n-500 hover:text-n-300");

  const empty = !loading && data && data.nodes.length <= (mode === "note" ? 1 : 0);

  return (
    <div className={cn("flex h-full min-h-0 flex-col animate-rise", className)}>
      <div className="flex items-center gap-2 px-3 pb-2 pt-3">
        <span className="kicker flex-1 text-accent">Graph</span>
        <button type="button" onClick={onClose} className="btn btn-ghost btn-icon h-[26px] w-[26px]" aria-label="Close graph">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="flex gap-0.5 px-3">
        <button type="button" onClick={() => setMode("note")} className={pill(mode === "note")}>
          Links
        </button>
        <button type="button" onClick={() => setMode("nodes")} className={pill(mode === "nodes")}>
          Entities
        </button>
      </div>
      {mode === "note" && (
        <div className="flex gap-0.5 px-3 pt-1">
          <button type="button" onClick={() => setDepth(1)} className={pill(depth === 1)}>
            1 level
          </button>
          <button type="button" onClick={() => setDepth(2)} className={pill(depth === 2)}>
            2 levels
          </button>
        </div>
      )}

      <div ref={boxRef} className="relative mt-2 min-h-0 flex-1 overflow-hidden">
        {loading && (
          <div className="flex h-full items-center justify-center gap-2 text-[12px] text-n-500">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading…
          </div>
        )}
        {error && <p className="p-3 text-[12.5px] text-danger-text">{error}</p>}
        {empty && (
          <p className="p-4 text-center text-[12px] text-n-500">
            {mode === "note"
              ? "No links to or from this note yet. Type [[ to link a note."
              : "No knowledge-graph entities found in this note."}
          </p>
        )}
        {!loading && !empty && data && size.w > 0 && (
          <Suspense fallback={null}>
            <ForceGraph2D
              ref={((g: ForceGraphMethods | undefined) => {
                graphRef.current = g;
                // Room for labels: nodes push apart harder and links run longer
                // than the library defaults (tuned for dots, not titled notes).
                g?.d3Force("charge")?.strength(-260);
                g?.d3Force("link")?.distance(70);
              }) as never}
              width={size.w}
              height={size.h}
              graphData={graphData}
              nodeId="id"
              nodeLabel={(n: NodeObject) => (n as unknown as GraphNode).title}
              nodeCanvasObject={paintNode}
              nodePointerAreaPaint={(node: NodeObject, color, ctx) => {
                ctx.beginPath();
                ctx.arc(node.x ?? 0, node.y ?? 0, 8, 0, 2 * Math.PI);
                ctx.fillStyle = color;
                ctx.fill();
              }}
              linkColor={() => colors.link}
              linkDirectionalArrowLength={3}
              linkDirectionalArrowRelPos={1}
              backgroundColor="rgba(0,0,0,0)"
              cooldownTicks={100}
              onNodeClick={onNodeClick}
              onEngineStop={() => {
                if (fitted.current) return;
                fitted.current = true;
                graphRef.current?.zoomToFit(300, 48);
              }}
              linkCanvasObjectMode={() => undefined}
              linkVisibility={(l: LinkObject) => Boolean(l.source && l.target)}
            />
          </Suspense>
        )}
      </div>

      <div className="px-4 py-2 text-[11px] text-n-500">
        {data
          ? mode === "note"
            ? `${Math.max(0, data.nodes.length - 1)} linked notes · ${data.edges.length} links · click a note to open it`
            : `${data.nodes.length} entities · click one to open it`
          : "—"}
      </div>
    </div>
  );
}
