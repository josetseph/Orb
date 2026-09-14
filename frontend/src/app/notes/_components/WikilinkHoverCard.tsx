"use client";

import type { WikilinkPreviewState } from "../_lib/types";

type WikilinkHoverCardProps = {
  preview: WikilinkPreviewState;
};

export function WikilinkHoverCard({ preview }: WikilinkHoverCardProps) {
  return (
    <div
      className="pointer-events-none fixed z-[80] w-80 animate-rise overflow-hidden rounded-lg bg-surface shadow-md"
      style={{ left: preview.x, top: preview.y }}
    >
      <div className="border-b border-divider px-3 py-2">
        <p className="truncate text-[13px] font-medium text-accent-300">{preview.title}</p>
        {preview.missing && <p className="text-[11px] text-n-500">Click to create</p>}
      </div>
      <div className="max-h-44 overflow-hidden px-3 py-2">
        <p className="line-clamp-[10] whitespace-pre-wrap text-[12px] leading-relaxed text-n-300">
          {preview.missing
            ? "This note does not exist yet. Click the link to create it."
            : preview.content.slice(0, 600) || "Empty note"}
        </p>
      </div>
    </div>
  );
}
