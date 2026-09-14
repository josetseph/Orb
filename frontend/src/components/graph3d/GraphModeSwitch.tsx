"use client";

import Link from "next/link";
import { cn } from "@/lib/utils";

/** Notes ↔ Entities switch shared by both graph screens. */
export function GraphModeSwitch({ mode }: { mode: "notes" | "entities" }) {
  return (
    <div className="seg h-8 bg-surface shadow-sm">
      <Link
        href="/notes-graph"
        className={cn("seg-opt no-underline", mode === "notes" && "seg-opt-active")}
      >
        Notes
      </Link>
      <Link
        href="/graph-3d"
        className={cn("seg-opt no-underline", mode === "entities" && "seg-opt-active")}
      >
        Entities
      </Link>
    </div>
  );
}
