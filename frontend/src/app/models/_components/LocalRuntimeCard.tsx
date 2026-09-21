import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { api, type LocalRuntimeSettings } from "@/lib/api";
import { errMessage } from "@/lib/utils";
import { SettingRow } from "@/components/settings-shell";
import { Card, SavedTick } from "./ModelPicker";

type Key = keyof LocalRuntimeSettings;

// kind "int?" = an empty box means automatic (null).
const ROWS: Array<{ key: Key; title: string; description: string; kind: "int" | "int?" | "float" | "bool" | "backend" }> = [
  { key: "llama_n_ctx", title: "Context size", description: "Tokens the chat model can hold. Larger uses more memory.", kind: "int" },
  { key: "llama_swa_full", title: "Full sliding-window cache", description: "Steadier long outputs; off shrinks the cache a lot on Gemma-style models.", kind: "bool" },
  { key: "llama_flash_attn", title: "Flash attention", description: "Less memory for attention where the build supports it.", kind: "bool" },
  { key: "model_idle_seconds", title: "Unload after idle (seconds)", description: "0 keeps models in memory for the whole session.", kind: "float" },
  { key: "llama_backend", title: "Acceleration", description: "Auto picks Metal, CUDA or CPU for this machine.", kind: "backend" },
  { key: "llama_n_gpu_layers", title: "GPU layers", description: "Empty = automatic. -1 = all layers, 0 = CPU only.", kind: "int?" },
  { key: "llama_n_threads", title: "CPU threads", description: "Empty = automatic.", kind: "int?" },
  { key: "llama_max_tokens", title: "Output cap (tokens)", description: "Empty = whatever context is left after the prompt.", kind: "int?" },
  { key: "llama_prompt_reserve", title: "Prompt reserve (tokens)", description: "Context kept for the prompt when an output cap is set.", kind: "int" },
  { key: "llama_repeat_penalty", title: "Repeat penalty", description: "1.0 disables it.", kind: "float" },
  { key: "embed_n_ctx", title: "Embedding context", description: "Tokens per embedded passage.", kind: "int" },
  { key: "rerank_n_ctx", title: "Reranker context", description: "Tokens per query-passage pair.", kind: "int" },
  { key: "large_attachment_tokens", title: "Ask before ingesting attachments over (tokens)", description: "Larger attachments wait for you to pick: graph, summarize, or index for search.", kind: "int" },
  { key: "extraction_chunk_tokens", title: "Extraction chunk (tokens)", description: "Empty = learned per model from truncated outputs.", kind: "int?" },
];

export function LocalRuntimeCard() {
  const [values, setValues] = useState<LocalRuntimeSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getLocalRuntime().then(setValues).catch((e) => setError(errMessage(e, "Could not load runtime settings.")));
  }, []);

  if (!values) return error ? <p className="text-[12px] text-danger-text">{error}</p> : null;

  const set = (key: Key, value: LocalRuntimeSettings[Key]) => {
    setSaved(false);
    setValues({ ...values, [key]: value });
  };

  async function save() {
    if (!values) return;
    setBusy(true);
    setError(null);
    try {
      setValues(await api.saveLocalRuntime(values));
      setSaved(true);
    } catch (e) {
      setError(errMessage(e, "Could not save runtime settings."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Local runtime" subtitle="How models load into memory. Saving unloads them; the next request reloads with these values.">
      <div className="space-y-1.5">
        {ROWS.map(({ key, title, description, kind }) => (
          <SettingRow key={key} title={title} description={description}>
            {kind === "bool" ? (
              <input type="checkbox" checked={Boolean(values[key])} onChange={(e) => set(key, e.target.checked)} />
            ) : kind === "backend" ? (
              <select className="input w-[120px]" value={String(values[key])} onChange={(e) => set(key, e.target.value as LocalRuntimeSettings["llama_backend"])}>
                {["auto", "metal", "cuda", "vulkan", "cpu"].map((b) => (
                  <option key={b}>{b}</option>
                ))}
              </select>
            ) : (
              <input
                type="number"
                className="input w-[120px]"
                step={kind === "float" ? "any" : 1}
                placeholder={kind === "int?" ? "auto" : undefined}
                value={values[key] === null ? "" : String(values[key])}
                onChange={(e) => {
                  const raw = e.target.value;
                  if (raw === "") return kind === "int?" ? set(key, null) : undefined;
                  set(key, Number(raw));
                }}
              />
            )}
          </SettingRow>
        ))}
      </div>
      <div className="flex items-center justify-end gap-2">
        {error && <span className="text-[11.5px] text-danger-text">{error}</span>}
        <SavedTick show={saved} />
        <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => void save()}>
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Save"}
        </button>
      </div>
    </Card>
  );
}
