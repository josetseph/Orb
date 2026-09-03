"use client";

import { useEffect, useState } from "react";
import { Check, ChevronDown, Cpu, Loader2, RotateCcw, X } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { KBLLMConfig, KnowledgeBase } from "@/lib/types";

const INHERIT = "";

const PROVIDER_LABELS: Record<string, string> = {
  local: "Local (on this device)",
  openai: "OpenAI (cloud)",
  gemini: "Google Gemini (cloud)",
  anthropic: "Anthropic (cloud)",
  huggingface: "Hugging Face (cloud)",
};

const CLOUD_HINTS: Record<string, string> = {
  openai: "e.g. gpt-4.1, gpt-4o-mini",
  gemini: "e.g. gemini-2.5-pro, gemini-2.5-flash",
  anthropic: "e.g. claude-sonnet-4-5, claude-haiku-4-5",
  huggingface: "e.g. meta-llama/Llama-3.3-70B-Instruct",
};

function describeEffective(kb: KnowledgeBase): string {
  const eff = kb.effective_llm;
  if (!eff) return "—";
  const model = eff.model || "(not set)";
  const ingest =
    eff.ingestion_model && eff.ingestion_model !== eff.model
      ? ` · ingest ${eff.ingestion_model}`
      : "";
  return `${eff.provider} · ${model}${ingest}`;
}

/**
 * Per-KB chat/ingestion model. A KB inherits Settings until something is
 * pinned here; embed / rerank / multimedia models stay system-wide.
 */
export function KBModelPanel({
  kb,
  onSaved,
  onError,
}: {
  kb: KnowledgeBase;
  onSaved: () => Promise<void> | void;
  onError: (message: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [config, setConfig] = useState<KBLLMConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [provider, setProvider] = useState(INHERIT);
  const [model, setModel] = useState(INHERIT);
  const [ingestionModel, setIngestionModel] = useState(INHERIT);

  const inherited = kb.effective_llm?.inherited ?? true;

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    api
      .getKBLLM(kb.id)
      .then((cfg) => {
        if (cancelled) return;
        setConfig(cfg);
        setProvider(cfg.override.provider ?? INHERIT);
        setModel(cfg.override.model ?? INHERIT);
        setIngestionModel(cfg.override.ingestion_model ?? INHERIT);
      })
      .catch(() => onError("Could not load model settings for this knowledge base."))
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, kb.id, onError]);

  // The provider the pinned models must belong to (inherit → system provider).
  const effectiveProvider = provider || config?.effective.provider || "local";
  const isLocal = effectiveProvider === "local";
  const localModels = config?.local_models ?? [];

  async function save(next: { provider: string; model: string; ingestion_model: string }) {
    setSaving(true);
    onError(null);
    try {
      const cfg = await api.updateKBLLM(kb.id, next);
      setConfig(cfg);
      setProvider(cfg.override.provider ?? INHERIT);
      setModel(cfg.override.model ?? INHERIT);
      setIngestionModel(cfg.override.ingestion_model ?? INHERIT);
      await onSaved();
      setOpen(false);
    } catch (err: unknown) {
      let msg = "Failed to save model settings.";
      if (err && typeof err === "object" && "response" in err) {
        const detail = (err as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail;
        if (detail) msg = detail;
      }
      onError(msg);
    } finally {
      setSaving(false);
    }
  }

  function modelField(
    label: string,
    value: string,
    setValue: (v: string) => void,
    hint: string,
  ) {
    const select = "w-full appearance-none rounded-lg border border-white/10 bg-white/5 px-3 py-2 pr-8 text-xs text-white outline-none focus:border-purple-500/50";
    return (
      <div className="space-y-1">
        <label className="text-[11px] text-white/45">{label}</label>
        {isLocal ? (
          <div className="relative">
            <select value={value} onChange={(e) => setValue(e.target.value)} className={select}>
              <option value={INHERIT} className="bg-[#0d0d12]">
                Inherit ({hint})
              </option>
              {localModels.map((m) => (
                <option key={m.id} value={m.id} className="bg-[#0d0d12]">
                  {m.label} · {m.size_gb} GB
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-white/40" />
          </div>
        ) : (
          <input
            type="text"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={`Inherit (${hint}) — or ${CLOUD_HINTS[effectiveProvider] ?? "a model id"}`}
            className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs text-white placeholder-white/25 outline-none focus:border-purple-500/50"
          />
        )}
      </div>
    );
  }

  return (
    <div className="mt-2">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px]",
            inherited
              ? "border-white/10 bg-white/5 text-white/40"
              : "border-teal-500/30 bg-teal-500/10 text-teal-200",
          )}
          title={inherited ? "Follows Settings" : "Pinned for this knowledge base"}
        >
          <Cpu className="h-3 w-3" />
          {inherited ? "inherits Settings" : "pinned"} · {describeEffective(kb)}
        </span>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="text-[11px] text-white/40 underline-offset-2 hover:text-white/70 hover:underline"
        >
          {open ? "Close" : "Change model"}
        </button>
      </div>

      {open && (
        <div className="mt-3 space-y-3 rounded-xl border border-white/10 bg-black/30 p-3">
          {loading || !config ? (
            <div className="flex items-center gap-2 text-xs text-white/40">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading…
            </div>
          ) : (
            <>
              <p className="text-[11px] text-white/40">
                Chat and note ingestion for <span className="text-white/70">{kb.name}</span> only.
                Embedding, reranking, and image/audio models stay shared (Setup).
              </p>
              <div className="space-y-1">
                <label className="text-[11px] text-white/45">Provider</label>
                <div className="relative">
                  <select
                    value={provider}
                    onChange={(e) => {
                      setProvider(e.target.value);
                      // Model ids are provider-specific; don't carry them across.
                      setModel(INHERIT);
                      setIngestionModel(INHERIT);
                    }}
                    className="w-full appearance-none rounded-lg border border-white/10 bg-white/5 px-3 py-2 pr-8 text-xs text-white outline-none focus:border-purple-500/50"
                  >
                    <option value={INHERIT} className="bg-[#0d0d12]">
                      Inherit Settings ({PROVIDER_LABELS[config.effective.provider] ?? config.effective.provider})
                    </option>
                    {config.providers.map((p) => (
                      <option key={p} value={p} className="bg-[#0d0d12]">
                        {PROVIDER_LABELS[p] ?? p}
                      </option>
                    ))}
                  </select>
                  <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-white/40" />
                </div>
              </div>

              {isLocal && localModels.length === 0 && (
                <p className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[11px] text-amber-100/80">
                  No local chat models are downloaded yet. Download one in Setup → Local
                  models to pin it here.
                </p>
              )}

              {modelField("Chat model", model, setModel, config.effective.model || "system")}
              {modelField(
                "Ingestion model",
                ingestionModel,
                setIngestionModel,
                "same as chat",
              )}
              {isLocal && (
                <p className="text-[11px] text-white/30">
                  Tip: a smaller GGUF for ingestion (extraction is structured JSON) keeps
                  the bigger model for Chat.
                </p>
              )}

              <div className="flex items-center justify-end gap-2 pt-1">
                {!inherited && (
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() =>
                      void save({ provider: INHERIT, model: INHERIT, ingestion_model: INHERIT })
                    }
                    className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-xs text-white/50 transition hover:text-white disabled:opacity-50"
                    title="Clear the override and follow Settings again"
                  >
                    <RotateCcw className="h-3 w-3" /> Inherit Settings
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-xs text-white/50 transition hover:text-white"
                >
                  <X className="h-3 w-3" /> Cancel
                </button>
                <button
                  type="button"
                  disabled={saving}
                  onClick={() =>
                    void save({ provider, model, ingestion_model: ingestionModel })
                  }
                  className="flex items-center gap-1.5 rounded-lg bg-purple-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-purple-500 disabled:opacity-50"
                >
                  {saving ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Check className="h-3 w-3" />
                  )}
                  Save
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
