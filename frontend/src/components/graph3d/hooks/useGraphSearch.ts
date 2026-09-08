"use client";

import { useMemo, useRef, useState, type MutableRefObject } from "react";

export function useGraphSearch(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  _nodesRef: MutableRefObject<any[]>,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  nodes: any[],
) {
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const searchInputRef = useRef<HTMLInputElement>(null);
  const searchOpenRef = useRef(false);

  // ── Search results — filter nodes client-side as user types ──
  const searchResults = useMemo(() => {
    if (!searchQuery.trim()) return [];
    const q = searchQuery.toLowerCase();
    const list = Array.isArray(nodes) ? nodes : [];
    return list
      .filter((n) => (n.name ?? "").toLowerCase().includes(q))
      .sort((a, b) => {
        const aStarts = (a.name ?? "").toLowerCase().startsWith(q);
        const bStarts = (b.name ?? "").toLowerCase().startsWith(q);
        if (aStarts && !bStarts) return -1;
        if (!aStarts && bStarts) return 1;
        return (a.name ?? "").localeCompare(b.name ?? "");
      })
      .slice(0, 8);
  }, [searchQuery, nodes]);

  return {
    searchOpen,
    setSearchOpen,
    searchQuery,
    setSearchQuery,
    searchResults,
    searchInputRef,
    searchOpenRef,
  };
}
