import { useState } from "react";
import { cn } from "@/lib/utils";

/** A width (or flag) remembered across launches; storage may be unavailable. */
export function useStored<T>(key: string, initial: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw ? (JSON.parse(raw) as T) : initial;
    } catch {
      return initial;
    }
  });
  const set = (next: T) => {
    setValue(next);
    try {
      localStorage.setItem(key, JSON.stringify(next));
    } catch {
      /* not remembered */
    }
  };
  return [value, set];
}

/**
 * Drag strip on a panel's edge. `edge="right"` sits on a left-hand panel
 * (drag right to widen); `edge="left"` on a right-hand one. Double-click resets.
 */
export function ResizeHandle({
  edge,
  width,
  min,
  max,
  initial,
  onWidth,
}: {
  edge: "left" | "right";
  width: number;
  min: number;
  max: number;
  initial: number;
  onWidth: (w: number) => void;
}) {
  const [dragging, setDragging] = useState(false);
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      onPointerDown={(e) => {
        e.preventDefault();
        const startX = e.clientX;
        const startW = width;
        const target = e.currentTarget;
        target.setPointerCapture(e.pointerId);
        setDragging(true);
        const move = (ev: PointerEvent) => {
          const dx = (ev.clientX - startX) * (edge === "right" ? 1 : -1);
          onWidth(Math.round(Math.min(max, Math.max(min, startW + dx))));
        };
        const up = () => {
          setDragging(false);
          target.removeEventListener("pointermove", move);
          target.removeEventListener("pointerup", up);
        };
        target.addEventListener("pointermove", move);
        target.addEventListener("pointerup", up);
      }}
      onDoubleClick={() => onWidth(initial)}
      className={cn(
        "absolute bottom-0 top-0 z-20 w-1.5 cursor-col-resize transition-colors hover:bg-accent/40",
        edge === "right" ? "-right-[3px]" : "-left-[3px]",
        dragging && "bg-accent/60",
      )}
    />
  );
}
