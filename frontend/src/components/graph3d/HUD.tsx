"use client";

/** Bottom-left stats + hint line shared by both graph screens. */
export function HUD({
  stats,
  hint,
}: {
  stats: string;
  hint: string;
}) {
  return (
    <div className="pointer-events-none absolute bottom-4 left-5 z-10 flex items-center gap-3 text-[11px] text-n-500">
      <span>{stats}</span>
      <span>·</span>
      <span>{hint}</span>
    </div>
  );
}
