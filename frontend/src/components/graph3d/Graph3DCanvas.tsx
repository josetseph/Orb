"use client";

import type { MutableRefObject, RefObject } from "react";
import dynamic from "next/dynamic";
import { nodeColor } from "@/components/graph3d/nodeColors";
import { ErrorBoundary } from "@/components/graph3d/ErrorBoundary";

// ForceGraph3D relies on browser APIs — must be dynamically imported (no SSR)
const ForceGraph3D = dynamic(() => import("react-force-graph-3d"), {
  ssr: false,
});

export function Graph3DCanvas({
  graphRef,
  graphData,
  nodeTypeMapRef,
  hasFittedRef,
  userNavigatedRef,
  onNodeClick,
}: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  graphRef: RefObject<any>;
  graphData: { nodes: object[]; links: object[] };
  nodeTypeMapRef: MutableRefObject<Map<string, string>>;
  hasFittedRef: MutableRefObject<boolean>;
  userNavigatedRef: MutableRefObject<boolean>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  onNodeClick: (node: any) => void;
}) {
  return (
    <ErrorBoundary>
      <ForceGraph3D
        ref={graphRef}
        graphData={graphData}
        nodeId="id"
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        nodeColor={(node: any) => nodeColor(node.node_type ?? "")}
        nodeRelSize={10}
        nodeOpacity={1.0}
        nodeResolution={18}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        nodeVal={(node: any) => Math.max(1, Number(node.val) || 1)}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        linkColor={(link: any) => {
          const src = link.source;
          const type =
            typeof src === "object" && src !== null
              ? (src.node_type ?? "")
              : (nodeTypeMapRef.current.get(String(src)) ?? "");
          return nodeColor(type);
        }}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        linkDirectionalParticleColor={(link: any) => {
          const src = link.source;
          const type =
            typeof src === "object" && src !== null
              ? (src.node_type ?? "")
              : (nodeTypeMapRef.current.get(String(src)) ?? "");
          return nodeColor(type);
        }}
        linkWidth={2.8}
        linkOpacity={0.72}
        linkCurvature={0.1}
        linkDirectionalParticles={2}
        linkDirectionalParticleWidth={4}
        linkDirectionalParticleSpeed={0.006}
        // Transparent so the CSS radial gradient on the host shows through.
        backgroundColor="rgba(0,0,0,0)"
        showNavInfo={false}
        enableNodeDrag={false}
        enableNavigationControls={false}
        cooldownTicks={0}
        warmupTicks={0}
        onEngineStop={() => {
          // Fit once on first settle only. Re-firing zoomToFit (common when
          // parent state updates) snaps the camera back to overview mid-flight.
          if (hasFittedRef.current || userNavigatedRef.current) return;
          hasFittedRef.current = true;
          graphRef.current?.zoomToFit(400, 120);
        }}
        onNodeClick={onNodeClick}
      />
    </ErrorBoundary>
  );
}
