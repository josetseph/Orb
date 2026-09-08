"use client";

import { useState } from "react";
import { useKB } from "@/lib/kb-context";
import {
  type KnowledgeNode,
  HUD,
  NodeDetailModal,
  ProximityLabelLayer,
  GraphSearchOverlay,
  Graph3DCanvas,
  useGraph3DData,
  useGraphSearch,
  useProximityLabels,
  useGraph3DCamera,
} from "@/components/graph3d";

export default function Graph3DPage() {
  const { currentKB, isHydrated } = useKB();
  const [selectedNode, setSelectedNode] = useState<KnowledgeNode | null>(null);

  const {
    graphData,
    loading,
    nodesRef,
    linksRef,
    nodeTypeMapRef,
    hasFittedRef,
    userNavigatedRef,
  } = useGraph3DData(currentKB, isHydrated);

  const {
    searchOpen,
    setSearchOpen,
    searchQuery,
    setSearchQuery,
    searchResults,
    searchInputRef,
    searchOpenRef,
  } = useGraphSearch(nodesRef, graphData.nodes);

  const { graphRef, flyToNode, handleNodeClick } = useGraph3DCamera({
    nodeCount: graphData.nodes.length,
    selectedNode,
    searchOpen,
    setSearchOpen,
    setSelectedNode,
    searchOpenRef,
    userNavigatedRef,
  });

  const { proximityLabels, linkLabels } = useProximityLabels({
    graphRef,
    nodeCount: graphData.nodes.length,
    linkCount: graphData.links.length,
    currentKB,
    nodesRef,
    linksRef,
  });

  if (loading) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100vh",
          background: "#000",
          color: "#94a3b8",
          fontFamily: "system-ui, sans-serif",
          fontSize: "0.9rem",
        }}
      >
        Loading graph…
      </div>
    );
  }

  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        height: "100vh",
        background: "#000",
      }}
    >
      <Graph3DCanvas
        graphRef={graphRef}
        graphData={graphData}
        nodeTypeMapRef={nodeTypeMapRef}
        hasFittedRef={hasFittedRef}
        userNavigatedRef={userNavigatedRef}
        onNodeClick={handleNodeClick}
      />

      <HUD
        nodeCount={graphData.nodes.length}
        edgeCount={graphData.links.length}
      />

      <ProximityLabelLayer
        proximityLabels={proximityLabels}
        linkLabels={linkLabels}
      />

      <GraphSearchOverlay
        searchOpen={searchOpen}
        setSearchOpen={setSearchOpen}
        searchQuery={searchQuery}
        setSearchQuery={setSearchQuery}
        searchResults={searchResults}
        searchInputRef={searchInputRef}
        flyToNode={flyToNode}
      />

      {selectedNode && (
        <NodeDetailModal
          node={selectedNode}
          onClose={() => setSelectedNode(null)}
          kb={currentKB}
        />
      )}
    </div>
  );
}
