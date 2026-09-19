import { AlertTriangle, Check, File as FileIcon, FolderOpen, Loader2 } from "lucide-react";
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
    <div className="field space-y-1.5">
      <label>{label}</label>
      <div className="flex gap-2">
        <select
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          className="input min-w-0 flex-1"
        >
          {(allowInherit || isPlaceholder) && (
            <option value="">
              {allowInherit
                ? (inheritLabel ?? "Use the system model")
                : models.length
                  ? "Choose a model…"
                  : "No models on this machine yet"}
            </option>
          )}
          {models.map((m) => (
            <option key={m.ref} value={m.ref} disabled={!m.runnable}>
              {m.label} · {FORMAT_LABEL[m.format] ?? m.format} · {m.size_gb} GB
              {m.runnable ? "" : " — cannot run here"}
            </option>
          ))}
          {!known && !isPlaceholder && (
            <option value={value}>{value.split("/").pop()} (added by path)</option>
          )}
        </select>
        {canBrowse && (
          <>
            <button
              type="button"
              disabled={disabled || browsing}
              onClick={() => void browse("file")}
              title="Pick a single .gguf file"
              className="btn btn-secondary btn-sm h-[30px]"
            >
              {browsing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileIcon className="h-3.5 w-3.5" />}
              .gguf file
            </button>
            <button
              type="button"
              disabled={disabled || browsing}
              onClick={() => void browse("folder")}
              title="Pick a model folder — MLX or safetensors, or a folder holding a .gguf"
              className="btn btn-secondary btn-sm h-[30px]"
            >
              <FolderOpen className="h-3.5 w-3.5" /> folder
            </button>
          </>
        )}
      </div>

      {canBrowse && (
        <p className="text-[11px] text-n-500">
          Point at a <span className="font-mono">.gguf</span> file, or a folder holding GGUF, MLX or
          safetensors weights — Orb detects the format and says so if it cannot run it here.
        </p>
      )}
      {selected && (
        <p className="text-[11px] text-n-500">
          {FORMAT_LABEL[selected.format] ?? selected.format}
          {selected.context_length ? ` · ${selected.context_length.toLocaleString()} token context` : ""}
          {selected.shards > 1 ? ` · ${selected.shards} shards` : ""}
        </p>
      )}
      {selected && !selected.runnable && (
        <p className="flex items-start gap-1.5 text-[11px] text-danger-text">
          <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
          {selected.unsupported_reason}
        </p>
      )}
      {(selected?.warnings ?? []).map((w) => (
        <p key={w} className="flex items-start gap-1.5 text-[11px] text-danger-text">
          <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
          {w}
        </p>
      ))}
    </div>
  );
}

/** Shared section chrome so every block on the page reads the same. */
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
    <section className={cn("card-outline space-y-3.5 p-4", accent && "shadow-[0_0_0_1px_var(--color-accent-700)]")}>
      <div>
        <div className="kicker">{title}</div>
        {subtitle && <p className="mt-1 text-[12.5px] text-n-500">{subtitle}</p>}
      </div>
      {children}
    </section>
  );
}

export function SavedTick({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-accent-300">
      <Check className="h-3 w-3" /> Saved
    </span>
  );
}
