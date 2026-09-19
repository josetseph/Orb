import { useEffect, useRef, useState, type MutableRefObject } from "react";
import { api } from "@/lib/api";

export function useGraph3DData(currentKB: string) {
  const [graphData, setGraphData] = useState<{
    nodes: object[];
    links: object[];
  }>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);
  const [prevKB, setPrevKB] = useState(currentKB);

  if (prevKB !== currentKB) {
    setPrevKB(currentKB);
    setLoading(true);
  }

  // Mirror nodes and links into refs so the rAF label loop never has a stale closure
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const nodesRef = useRef<any[]>([]);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const linksRef = useRef<any[]>([]);

  // zoomToFit must run only once — onEngineStop can re-fire and yank the camera
  // back to the overview whenever React re-renders (e.g. proximity labels).
  const hasFittedRef = useRef(false);
  const userNavigatedRef = useRef(false);

  // Build a stable id→node_type map so linkColor can resolve string IDs too
  const nodeTypeMapRef = useRef<Map<string, string>>(new Map());

  useEffect(() => {
    nodesRef.current = graphData.nodes as any[]; // eslint-disable-line @typescript-eslint/no-explicit-any
  }, [graphData.nodes]);

  useEffect(() => {
    linksRef.current = graphData.links as any[]; // eslint-disable-line @typescript-eslint/no-explicit-any
  }, [graphData.links]);

  useEffect(() => {
    const m = new Map<string, string>();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    for (const n of graphData.nodes as any[]) {
      if (n.id != null) m.set(String(n.id), n.node_type ?? "");
    }
    nodeTypeMapRef.current = m;
  }, [graphData.nodes]);

  // Fetch and adapt data: nodes need `id` field, edges become `links`
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    hasFittedRef.current = false;
    userNavigatedRef.current = false;
    api
      .getGraph3DFull(currentKB, { signal: controller.signal })
      .then(({ nodes, edges }) => {
        if (cancelled) return;
        const nodeIdSet = new Set(nodes.map((n) => n.node_id));
        const degree = new Map<string, number>();
        for (const e of edges) {
          if (!nodeIdSet.has(e.source) || !nodeIdSet.has(e.target)) continue;
          degree.set(e.source, (degree.get(e.source) || 0) + 1);
          degree.set(e.target, (degree.get(e.target) || 0) + 1);
        }
        setGraphData({
          nodes: nodes.map((n) => ({
            ...n,
            id: n.node_id,
            fx: n.x,
            fy: n.y,
            fz: n.z,
            // Degree-weighted size so hubs stay readable after zoomToFit.
            val: 1 + Math.min(8, degree.get(n.node_id) || 0),
          })),
          // Drop edges where either endpoint is missing from the node list
          links: edges
            .filter((e) => nodeIdSet.has(e.source) && nodeIdSet.has(e.target))
            .map((e) => ({ source: e.source, target: e.target, type: e.type })),
        });
      })
      .catch((err) => {
        if (!cancelled) console.error(err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [currentKB]);

  return {
    graphData,
    loading,
    nodesRef: nodesRef as MutableRefObject<any[]>, // eslint-disable-line @typescript-eslint/no-explicit-any
    linksRef: linksRef as MutableRefObject<any[]>, // eslint-disable-line @typescript-eslint/no-explicit-any
    nodeTypeMapRef,
    hasFittedRef,
    userNavigatedRef,
  };
}
