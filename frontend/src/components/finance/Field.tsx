import type { ReactNode } from "react";

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field block">
      <span className="mb-1.5 block text-[12px] text-n-400">{label}</span>
      {children}
    </label>
  );
}
