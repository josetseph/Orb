import { useEffect, type RefObject } from "react";
import { Search } from "lucide-react";
import { nodeColor } from "@/components/graph3d/nodeColors";

/** Always-visible node search; `/` focuses it. Picking a result flies to and selects the node. */
export function GraphSearchOverlay({
  searchOpen,
  setSearchOpen,
  searchQuery,
  setSearchQuery,
  searchResults,
  searchInputRef,
  onPick,
}: {
  searchOpen: boolean;
  setSearchOpen: (open: boolean) => void;
  searchQuery: string;
  setSearchQuery: (q: string) => void;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  searchResults: any[];
  searchInputRef: RefObject<HTMLInputElement | null>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  onPick: (node: any) => void;
}) {
  // The camera hook flips searchOpen on "/" — turn that into focus.
  useEffect(() => {
    if (searchOpen) searchInputRef.current?.focus();
  }, [searchOpen, searchInputRef]);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const pick = (n: any) => {
    onPick(n);
    setSearchQuery("");
    searchInputRef.current?.blur();
  };

  return (
    <div className="relative">
      <div className="flex h-8 w-[260px] items-center gap-1.5 rounded-md bg-surface px-2.5 shadow-sm">
        <Search className="h-3.5 w-3.5 text-n-500" />
        <input
          ref={searchInputRef}
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onFocus={() => setSearchOpen(true)}
          onBlur={() => setSearchOpen(false)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              setSearchQuery("");
              searchInputRef.current?.blur();
            }
            if (e.key === "Enter" && searchResults.length > 0) pick(searchResults[0]);
          }}
          placeholder="Find an entity"
          className="min-w-0 flex-1 bg-transparent text-[12.5px] outline-none placeholder:text-n-600"
        />
      </div>
      {searchOpen && searchResults.length > 0 && (
        <div className="popover absolute left-0 top-9 w-[260px]">
          {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
          {searchResults.map((n: any, i: number) => (
            <button
              key={n.node_id ?? i}
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(n)}
              className="menu-item"
            >
              <span
                className="dot"
                style={{ background: nodeColor(n.node_type ?? "") }}
              />
              <span className="min-w-0 flex-1 truncate">{n.name}</span>
              <span className="text-[11px] text-n-500">{n.node_type ?? ""}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
