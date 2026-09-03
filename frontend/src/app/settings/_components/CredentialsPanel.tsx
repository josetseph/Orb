"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Check, Eye, KeyRound, Loader2, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { getDesktopBridge } from "@/lib/desktop";
import { cn } from "@/lib/utils";

const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  gemini: "Google Gemini",
  anthropic: "Anthropic",
  huggingface: "Hugging Face",
};

type ProviderState = { configured: boolean; source: string | null };

/**
 * Cloud API keys, entered in-app and encrypted into the OS keychain by the
 * desktop shell. Keys are write-only here: the app can say whether a provider
 * is configured, never show the key back.
 */
export function CredentialsPanel() {
  const [providers, setProviders] = useState<Record<string, ProviderState>>({});
  const [known, setKnown] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [encryptionAvailable, setEncryptionAvailable] = useState(true);

  const bridge = getDesktopBridge();
  const canStore = Boolean(bridge?.setCredential);

  const refresh = useCallback(async () => {
    try {
      const data = await api.getCredentials();
      setProviders(data.providers);
      setKnown(data.known);
    } catch {
      setError("Could not load API key status.");
    } finally {
      setLoading(false);
    }
    if (bridge?.listCredentials) {
      try {
        const listed = await bridge.listCredentials();
        setEncryptionAvailable(listed.encryptionAvailable);
      } catch {
        /* non-fatal */
      }
    }
  }, [bridge]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function save(provider: string) {
    const key = (drafts[provider] || "").trim();
    if (!key) return;
    setBusy(provider);
    setError(null);
    setSaved(null);
    try {
      const result = await bridge!.setCredential!(provider, key);
      if (!result?.ok) throw new Error(result?.error || "Could not save the key");
      setDrafts((d) => ({ ...d, [provider]: "" }));
      setSaved(provider);
      setTimeout(() => setSaved(null), 2500);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the key.");
    } finally {
      setBusy(null);
    }
  }

  async function remove(provider: string) {
    setBusy(provider);
    setError(null);
    try {
      const result = await bridge!.deleteCredential!(provider);
      if (!result?.ok) throw new Error(result?.error || "Could not remove the key");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove the key.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 space-y-4">
      <div className="flex items-center gap-2">
        <KeyRound className="h-4 w-4 text-purple-400" />
        <h2 className="text-sm font-semibold text-white/70 uppercase tracking-wide">
          Cloud API keys
        </h2>
      </div>
      <p className="text-xs text-white/40">
        Stored encrypted in your operating system&apos;s keychain, never in your notes
        or data folder. Keys are never shown again after saving.
      </p>

      {!canStore && (
        <p className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-xs text-amber-100/80">
          Saving keys requires the Orb desktop app. In a browser you can see which
          providers are configured, but not change them.
        </p>
      )}
      {canStore && !encryptionAvailable && (
        <p className="rounded-xl border border-red-500/25 bg-red-500/5 px-4 py-3 text-xs text-red-200/90">
          This system has no available keychain, so Orb will not store API keys on
          disk. On Linux, install a secret service (e.g. gnome-keyring) and restart.
        </p>
      )}
      {error && (
        <p className="flex items-start gap-2 rounded-xl border border-red-500/25 bg-red-500/5 px-4 py-3 text-xs text-red-200/90">
          <AlertCircle className="mt-px h-3.5 w-3.5 shrink-0" />
          {error}
        </p>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-xs text-white/40">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading…
        </div>
      ) : (
        <div className="space-y-3">
          {known.map((provider) => {
            const state = providers[provider] || { configured: false, source: null };
            const isBusy = busy === provider;
            return (
              <div key={provider} className="space-y-1.5">
                <div className="flex items-center gap-2">
                  <label className="text-xs text-white/55">
                    {PROVIDER_LABELS[provider] ?? provider}
                  </label>
                  {state.configured ? (
                    <span
                      className={cn(
                        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px]",
                        state.source === "env"
                          ? "bg-amber-500/15 text-amber-200"
                          : "bg-green-500/15 text-green-300",
                      )}
                      title={
                        state.source === "env"
                          ? "Loaded from the environment for this session — save it here to keep it in your keychain."
                          : "Stored in your OS keychain"
                      }
                    >
                      <Check className="h-2.5 w-2.5" />
                      {state.source === "env" ? "from environment" : "saved"}
                    </span>
                  ) : (
                    <span className="rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-white/35">
                      not set
                    </span>
                  )}
                  {saved === provider && (
                    <span className="text-[10px] text-green-400">Saved</span>
                  )}
                </div>
                <div className="flex gap-2">
                  <input
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    value={drafts[provider] || ""}
                    onChange={(e) =>
                      setDrafts((d) => ({ ...d, [provider]: e.target.value }))
                    }
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void save(provider);
                    }}
                    placeholder={state.configured ? "Enter a new key to replace" : "Paste API key"}
                    disabled={!canStore || isBusy}
                    className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-mono text-xs text-white placeholder-white/25 outline-none transition focus:border-purple-500/50 disabled:opacity-50"
                  />
                  <button
                    type="button"
                    onClick={() => void save(provider)}
                    disabled={!canStore || isBusy || !(drafts[provider] || "").trim()}
                    className="shrink-0 rounded-xl bg-purple-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-purple-500 disabled:opacity-40"
                  >
                    {isBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Save"}
                  </button>
                  {state.configured && canStore && (
                    <button
                      type="button"
                      onClick={() => void remove(provider)}
                      disabled={isBusy}
                      title={`Remove the ${PROVIDER_LABELS[provider] ?? provider} key`}
                      className="shrink-0 rounded-xl border border-red-500/20 bg-red-500/5 px-2.5 py-2 text-red-400/70 transition hover:border-red-500/40 hover:text-red-300 disabled:opacity-40"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <p className="flex items-start gap-1.5 text-[11px] text-white/25">
        <Eye className="mt-px h-3 w-3 shrink-0" />
        Orb never displays a saved key and never sends one anywhere except the
        provider you configured it for.
      </p>
    </div>
  );
}
