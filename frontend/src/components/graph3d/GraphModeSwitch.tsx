import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";

/** Notes ↔ Entities switch shared by both graph screens. */
export function GraphModeSwitch({ mode }: { mode: "notes" | "entities" }) {
  return (
    <div className="seg h-8 bg-surface shadow-sm">
      <Link
        to="/notes-graph"
        className={cn("seg-opt no-underline", mode === "notes" && "seg-opt-active")}
      >
        Notes
      </Link>
      <Link
        to="/graph-3d"
        className={cn("seg-opt no-underline", mode === "entities" && "seg-opt-active")}
      >
        Entities
      </Link>
    </div>
  );
}
