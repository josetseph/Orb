"use client";

import {
  AlertTriangle,
  Check,
  ChevronDown,
  File as FileIcon,
  FolderOpen,
  Loader2,
} from "lucide-react";
import { getDesktopBridge, pickDesktopFile, pickDesktopDirectory } from "@/lib/desktop";
import { cn } from "@/lib/utils";
import type { InstalledModel } from "@/lib/models-types";

const FORMAT_LABEL: Record<string, string> = {
  gguf: "GGUF",
  mlx: "MLX",
  transformers: "Safetensors",
};

/**
 * Choose one local model: pick from what is on disk, or point at a file or
 * folder anywhere. Models this machine cannot run stay visible with the reason,
 * so a missing model never looks like a disappearance.
 */
export function ModelPicker({
  models,
  value,
  onChange,
  onBrowse,
  browsing,
  label,
  inheritLabel,
  allowInherit = false,
  disabled = false,
}: {
  models: InstalledModel[];
  value: string;
  onChange: (ref: string) => void;
  onBrowse: (path: string) => void;
  browsing?: boolean;
  label: string;
  inheritLabel?: string;
  allowInherit?: boolean;
  disabled?: boolean;
}) {
  const canBrowse = Boolean(getDesktopBridge()?.pickFile);
  // "local-chat" is the backend placeholder for "whatever is selected", not a
  // model anyone picked; showing it as a chosen entry is misleading.
  const isPlaceholder = !value || value === "local-chat";
  const known = models.some((m) => m.ref === value);
  const selected = models.find((m) => m.ref === value);

  async function browse(kind: "file" | "folder") {
    const picked =
      kind === "file"
        ? await pickDesktopFile({
            title: "Choose a model file",
            filters: [{ name: "Model files", extensions: ["gguf"] }],
          })
        : await pickDesktopDirectory({ title: "Choose a model folder" });
    if (picked) onBrowse(picked);
  }

  return (
    <div className="space-y-1.5">
      <label className="text-xs text-white/45">{label}</label>
      <div className="flex gap-2">
        <div className="relative min-w-0 flex-1">
          <select
            value={value}
            disabled={disabled}
            onChange={(e) => onChange(e.target.value)}
            className="w-full appearance-none rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 pr-9 text-sm text-white outline-none transition focus:border-purple-500/50 disabled:opacity-50"
          >
            {(allowInherit || isPlaceholder) && (
              <option value="" className="bg-[#0d0d12]">
                {allowInherit
                  ? (inheritLabel ?? "Use the system model")
                  : models.length
                    ? "Choose a model…"
                    : "No models on this machine yet"}
              </option>
            )}
            {models.map((m) => (
              <option
                key={m.ref}
                value={m.ref}
                disabled={!m.runnable}
                className="bg-[#0d0d12]"
              >
                {m.label} · {FORMAT_LABEL[m.format] ?? m.format} · {m.size_gb} GB
                {m.runnable ? "" : " — cannot run here"}
              </option>
            ))}
            {!known && !isPlaceholder && (
              <option value={value} className="bg-[#0d0d12]">
                {value.split("/").pop()} (added by path)
              </option>
            )}
          </select>
          <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-white/40" />
        </div>
        {canBrowse && (
          <>
            <button
              type="button"
              disabled={disabled || browsing}
              onClick={() => void browse("file")}
              title="Pick a single .gguf file"
              className="shrink-0 rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-xs text-white/60 transition hover:border-white/25 hover:text-white disabled:opacity-40"
            >
              {browsing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <span className="flex items-center gap-1.5">
                  <FileIcon className="h-3.5 w-3.5" /> .gguf file
                </span>
              )}
            </button>
            <button
              type="button"
              disabled={disabled || browsing}
              onClick={() => void browse("folder")}
              title="Pick a model folder — MLX or safetensors, or a folder holding a .gguf"
              className="shrink-0 rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-xs text-white/60 transition hover:border-white/25 hover:text-white disabled:opacity-40"
            >
              <span className="flex items-center gap-1.5">
                <FolderOpen className="h-3.5 w-3.5" /> folder
              </span>
            </button>
          </>
        )}
      </div>

      {canBrowse && (
        <p className="text-[11px] text-white/25">
          Point at a <span className="font-mono">.gguf</span> file, or a folder
          holding GGUF, MLX or safetensors weights — Orb detects the format and
          says so if it cannot run it here.
        </p>
      )}
      {selected && (
        <p className="text-[11px] text-white/30">
          {FORMAT_LABEL[selected.format] ?? selected.format}
          {selected.context_length
            ? ` · ${selected.context_length.toLocaleString()} token context`
            : ""}
          {selected.shards > 1 ? ` · ${selected.shards} shards` : ""}
        </p>
      )}
      {selected && !selected.runnable && (
        <p className="flex items-start gap-1.5 rounded-lg border border-red-500/25 bg-red-500/5 px-3 py-2 text-[11px] text-red-200/90">
          <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
          {selected.unsupported_reason}
        </p>
      )}
      {(selected?.warnings ?? []).map((w) => (
        <p key={w} className="flex items-start gap-1.5 text-[11px] text-amber-300/80">
          <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
          {w}
        </p>
      ))}
    </div>
  );
}

/** Shared card chrome so every section on the page reads the same. */
export function Card({
  title,
  subtitle,
  children,
  accent,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  accent?: boolean;
}) {
  return (
    <section
      className={cn(
        "rounded-2xl border p-6 space-y-4",
        accent
          ? "border-purple-500/30 bg-purple-500/[0.04]"
          : "border-white/10 bg-white/[0.03]",
      )}
    >
      <div className="space-y-1">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-white/70">
          {title}
        </h2>
        {subtitle && <p className="text-xs text-white/40">{subtitle}</p>}
      </div>
      {children}
    </section>
  );
}

export function SavedTick({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-green-400">
      <Check className="h-3 w-3" /> Saved
    </span>
  );
}
