import type { ReactNode } from "react";

export function FinanceHeader({ meta, children }: { meta?: string; children?: ReactNode }) {
  return (
    <div className="flex items-center gap-2.5 px-7 pb-2.5 pt-3.5">
      <h1 className="flex-1 text-[15px] font-medium">Finance</h1>
      {meta && <span className="text-[11.5px] text-n-500">{meta}</span>}
      {children}
    </div>
  );
}
