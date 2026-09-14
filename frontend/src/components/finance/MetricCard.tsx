import type { ReactNode } from "react";
import { money } from "./utils";

export function MetricCard({
  icon,
  label,
  value,
  currency,
}: {
  icon: ReactNode;
  label: string;
  value: number;
  currency?: string;
}) {
  return (
    <div className="card px-4 py-3.5">
      <div className="card-kicker flex items-center gap-1.5 [&_svg]:h-3 [&_svg]:w-3">
        {icon}
        {label}
      </div>
      <div className="text-[24px] font-medium tabular-nums tracking-[-0.01em]">
        {money(value)}
      </div>
      <div className="text-[11px] text-n-500">{currency || "—"}</div>
    </div>
  );
}
