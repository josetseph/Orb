"use client";

import { useEffect, useMemo, useState } from "react";
import { Loader2, Maximize2 } from "lucide-react";
import { useKB } from "@/lib/kb-context";
import { cn } from "@/lib/utils";
import {
  type KnowledgeNode,
  HUD,
  NodeDetailModal,
  ProximityLabelLayer,
  GraphSearchOverlay,
  Graph3DCanvas,
  nodeColor,
  useGraph3DData,
  useGraphSearch,
  useProximityLabels,
  useGraph3DCamera,
  GraphModeSwitch,
} from "@/components/graph3d";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyNode = any;

const MAX_TYPE_FILTERS = 8;

export default function Graph3DPage() {
  const { currentKB, isHydrated } = useKB();
  const [selectedNode, setSelectedNode] = useState<KnowledgeNode | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());

  const {
    graphData,
    loading,
    nodesRef,
    linksRef,
    nodeTypeMapRef,
    hasFittedRef,
    userNavigatedRef,
  } = useGraph3DData(currentKB, isHydrated);

  const nodes = graphData.nodes as AnyNode[];
  const links = graphData.links as AnyNode[];

  const typeCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const n of nodes) {
      const t = n.node_type ?? "unknown";
      m.set(t, (m.get(t) || 0) + 1);
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [nodes]);

  const degree = useMemo(() => {
    const m = new Map<string, number>();
    const idOf = (v: AnyNode) => String(typeof v === "object" && v ? v.id : v);
    for (const l of links) {
      m.set(idOf(l.source), (m.get(idOf(l.source)) || 0) + 1);
      m.set(idOf(l.target), (m.get(idOf(l.target)) || 0) + 1);
    }
    return m;
  }, [links]);

  const filtered = useMemo(() => {
    if (hidden.size === 0) return graphData;
    const keep = new Set(
      nodes.filter((n) => !hidden.has(n.node_type ?? "unknown")).map((n) => String(n.id)),
    );
    const idOf = (v: AnyNode) => String(typeof v === "object" && v ? v.id : v);
    return {
      nodes: nodes.filter((n) => keep.has(String(n.id))),
      links: links.filter((l) => keep.has(idOf(l.source)) && keep.has(idOf(l.target))),
    };
  }, [graphData, nodes, links, hidden]);

  // Labels follow what is on screen, not the full dataset.
  useEffect(() => {
    nodesRef.current = filtered.nodes as AnyNode[];
    linksRef.current = filtered.links as AnyNode[];
  }, [filtered, nodesRef, linksRef]);

  const {
    searchOpen,
    setSearchOpen,
    searchQuery,
    setSearchQuery,
    searchResults,
    searchInputRef,
    searchOpenRef,
  } = useGraphSearch(nodesRef, filtered.nodes);

  const { graphRef, flyToNode, handleNodeClick } = useGraph3DCamera({
    nodeCount: filtered.nodes.length,
    selectedNode,
    searchOpen,
    setSearchOpen,
    setSelectedNode,
    searchOpenRef,
    userNavigatedRef,
  });

  const { proximityLabels, linkLabels } = useProximityLabels({
    graphRef,
    nodeCount: filtered.nodes.length,
    linkCount: filtered.links.length,
    currentKB,
    nodesRef,
    linksRef,
  });

  const pick = (n: AnyNode) => {
    flyToNode(n);
    handleNodeClick(n);
  };
  const pickById = (id: string) => {
    const n = (filtered.nodes as AnyNode[]).find((x) => String(x.id) === id);
    if (n) pick(n);
  };
  const toggleType = (t: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });

  const topNodes = useMemo(
    () =>
      [...(filtered.nodes as AnyNode[])]
        .sort((a, b) => (degree.get(String(b.id)) || 0) - (degree.get(String(a.id)) || 0))
        .slice(0, 6),
    [filtered.nodes, degree],
  );

  const stats = `${filtered.nodes.length} entities · ${filtered.links.length} links`;

  return (
    <div className="screen relative">
      <div
        className="relative min-w-0 flex-1 overflow-hidden"
        style={{
          background:
            "radial-gradient(ellipse at 50% 45%, color-mix(in srgb, var(--color-accent-900) 55%, var(--color-bg)) 0%, var(--color-bg) 70%)",
        }}
      >
        {loading ? (
          <div className="flex h-full items-center justify-center gap-2 text-[13px] text-n-500">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading graph…
          </div>
        ) : (
          <>
            <Graph3DCanvas
              graphRef={graphRef}
              graphData={filtered}
              nodeTypeMapRef={nodeTypeMapRef}
              hasFittedRef={hasFittedRef}
              userNavigatedRef={userNavigatedRef}
              onNodeClick={handleNodeClick}
            />
            <ProximityLabelLayer proximityLabels={proximityLabels} linkLabels={linkLabels} />
          </>
        )}

        <div className="absolute left-5 top-4 z-10 flex items-center gap-2">
          <GraphSearchOverlay
            searchOpen={searchOpen}
            setSearchOpen={setSearchOpen}
            searchQuery={searchQuery}
            setSearchQuery={setSearchQuery}
            searchResults={searchResults}
            searchInputRef={searchInputRef}
            onPick={pick}
          />
          <GraphModeSwitch mode="entities" />
          {typeCounts.length > 0 && (
            <div className="flex h-8 items-center gap-1 rounded-md bg-surface px-1.5 shadow-sm">
              {typeCounts.slice(0, MAX_TYPE_FILTERS).map(([t, n]) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => toggleType(t)}
                  className={cn(
                    "flex h-[22px] items-center gap-1.5 rounded-[5px] px-2 text-[11.5px] text-n-300 hover:bg-n-900",
                    hidden.has(t) && "opacity-40",
                  )}
                >
                  <span className="dot" style={{ background: nodeColor(t) }} />
                  {t}
                  <span className="text-n-500">{n}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="absolute right-5 top-4 z-10">
          <button
            type="button"
            title="Fit to view"
            onClick={() => graphRef.current?.zoomToFit(400, 120)}
            className="grid h-8 w-8 place-items-center rounded-md bg-surface text-n-300 shadow-sm hover:text-text"
          >
            <Maximize2 className="h-[15px] w-[15px]" />
          </button>
        </div>

        <HUD stats={stats} hint="Drag to look · scroll to fly · click a node" />
      </div>

      <aside className="flex w-[340px] shrink-0 flex-col border-l border-n-900 bg-bg-deep/40">
        {selectedNode ? (
          <NodeDetailModal
            node={selectedNode}
            onClose={() => setSelectedNode(null)}
            kb={currentKB}
            onSelectNodeId={pickById}
          />
        ) : (
          <div className="flex flex-col gap-3.5 overflow-auto px-4 py-3.5">
            <div>
              <h5 className="mb-1 text-[15px] font-medium">Graph</h5>
              <div className="text-[12px] text-n-500">{stats}</div>
            </div>
            <div>
              <div className="kicker mb-1.5">Types</div>
              {typeCounts.map(([t, n]) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => toggleType(t)}
                  className={cn("row -mx-2", hidden.has(t) && "opacity-40")}
                >
                  <span className="dot" style={{ background: nodeColor(t) }} />
                  <span className="flex-1">{t}</span>
                  <span className="text-[11px] text-n-500">{n} nodes</span>
                </button>
              ))}
            </div>
            <div>
              <div className="kicker mb-1.5">Most connected</div>
              {topNodes.map((n) => (
                <button key={n.id} type="button" onClick={() => pick(n)} className="row -mx-2">
                  <span
                    className="h-[22px] w-[22px] shrink-0 rounded-[6px]"
                    style={{
                      background: `color-mix(in srgb, ${nodeColor(n.node_type ?? "")} 55%, var(--color-surface))`,
                    }}
                  />
                  <span className="min-w-0 flex-1 truncate">{n.name}</span>
                  <span className="text-[11px] text-n-500">
                    {degree.get(String(n.id)) || 0} links
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}
