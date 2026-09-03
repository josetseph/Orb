"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Check, Link2, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { getDesktopBridge } from "@/lib/desktop";

const EXAMPLES = [
  "https://openrouter.ai/api/v1",
  "https://api.groq.com/openai/v1",
  "http://127.0.0.1:1234/v1",
];

/**
 * OpenAI-compatible endpoints: the user supplies a URL, a key, and a model name.
 * The URL is the credential's identity, so no naming step is needed and two
 * servers can never share a key by accident.
 */
export function EndpointPanel() {
  const [endpoints, setEndpoints] = useState<string[]>([]);
  const [url, setUrl] = useState("");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [models, setModels] = useState<string[] | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeNote, setProbeNote] = useState<string | null>(null);

  const bridge = getDesktopBridge();
  const canStore = Boolean(bridge?.setEndpointCredential);

  const refresh = useCallback(async () => {
    try {
      setEndpoints((await api.getCredentials()).endpoints ?? []);
    } catch {
      /* the credentials panel already surfaces load failures */
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function save() {
    const target = url.trim();
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      const result = await bridge!.setEndpointCredential!(target, key.trim());
      if (!result?.ok) throw new Error(result?.error || "Could not save the endpoint");
      setKey("");
      await refresh();
      await probe(target);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the endpoint.");
    } finally {
      setBusy(false);
    }
  }

  async function remove(target: string) {
    setBusy(true);
    setError(null);
    try {
      const result = await bridge!.deleteEndpointCredential!(target);
      if (!result?.ok) throw new Error(result?.error || "Could not remove the endpoint");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove the endpoint.");
    } finally {
      setBusy(false);
    }
  }

  /** Most OpenAI-compatible servers implement /v1/models; some do not. */
  async function probe(target: string) {
    setProbing(true);
    setModels(null);
    setProbeNote(null);
    try {
      const data = await api.getEndpointModels(target);
      setModels(data.models);
      if (!data.models.length) {
        setProbeNote("The endpoint listed no models — type the model name manually.");
      }
    } catch {
      setProbeNote(
        "This endpoint did not return a model list. That is fine — type the model name manually.",
      );
    } finally {
      setProbing(false);
    }
  }

  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 space-y-4">
      <div className="flex items-center gap-2">
        <Link2 className="h-4 w-4 text-purple-400" />
        <h2 className="text-sm font-semibold text-white/70 uppercase tracking-wide">
          OpenAI-compatible endpoints
        </h2>
      </div>
      <p className="text-xs text-white/40">
        Any server speaking the OpenAI API — OpenRouter, Groq, Together, vLLM,
        LM Studio, llama-server, Ollama. Add the URL and key here, then pick the
        model name in Settings above or per knowledge base.
      </p>

      {!canStore && (
        <p className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-xs text-amber-100/80">
          Adding endpoints requires the Orb desktop app.
        </p>
      )}
      {error && (
        <p className="flex items-start gap-2 rounded-xl border border-red-500/25 bg-red-500/5 px-4 py-3 text-xs text-red-200/90">
          <AlertCircle className="mt-px h-3.5 w-3.5 shrink-0" />
          {error}
        </p>
      )}

      {endpoints.length > 0 && (
        <div className="space-y-1.5">
          {endpoints.map((endpoint) => (
            <div
              key={endpoint}
              className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-3 py-2"
            >
              <Check className="h-3.5 w-3.5 shrink-0 text-green-400" />
              <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-white/70">
                {endpoint}
              </span>
              <button
                type="button"
                onClick={() => void probe(endpoint)}
                disabled={probing}
                title="List the models this endpoint serves"
                className="shrink-0 rounded-lg border border-white/10 px-2 py-1 text-white/50 transition hover:text-white disabled:opacity-40"
              >
                {probing ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <RefreshCw className="h-3 w-3" />
                )}
              </button>
              {canStore && (
                <button
                  type="button"
                  onClick={() => void remove(endpoint)}
                  disabled={busy}
                  title="Remove this endpoint"
                  className="shrink-0 rounded-lg border border-red-500/20 px-2 py-1 text-red-400/70 transition hover:text-red-300 disabled:opacity-40"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="space-y-2">
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder={EXAMPLES[0]}
          disabled={!canStore || busy}
          className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-mono text-xs text-white placeholder-white/25 outline-none transition focus:border-purple-500/50 disabled:opacity-50"
        />
        <div className="flex gap-2">
          <input
            type="password"
            autoComplete="off"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void save();
            }}
            placeholder="API key (leave blank for local servers)"
            disabled={!canStore || busy}
            className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-mono text-xs text-white placeholder-white/25 outline-none transition focus:border-purple-500/50 disabled:opacity-50"
          />
          <button
            type="button"
            onClick={() => void save()}
            disabled={!canStore || busy || !url.trim()}
            className="shrink-0 rounded-xl bg-purple-600 px-4 py-2 text-xs font-medium text-white transition hover:bg-purple-500 disabled:opacity-40"
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Add"}
          </button>
        </div>
        <p className="text-[10px] text-white/25">
          e.g. {EXAMPLES.slice(1).join("  ·  ")}
        </p>
      </div>

      {probeNote && <p className="text-[11px] text-amber-300/80">{probeNote}</p>}
      {models && models.length > 0 && (
        <div className="space-y-1">
          <p className="text-[11px] text-white/45">
            {models.length} model{models.length === 1 ? "" : "s"} available — use one of
            these as the model name:
          </p>
          <div className="max-h-28 overflow-y-auto rounded-lg border border-white/10 bg-black/30 p-2">
            {models.map((m) => (
              <div key={m} className="font-mono text-[10px] text-white/55">
                {m}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
