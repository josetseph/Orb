"use client";

import { useEffect, useState } from "react";
import { Sparkles, FolderOpen, Brain, Cloud, SkipForward, Check } from "lucide-react";
import { api } from "@/lib/api";
import { pickDesktopDirectory, getDesktopBridge } from "@/lib/desktop";
import { ShaderBackground } from "@/components/shader-background";
import type { SetupStatus } from "@/lib/types";

type AiMode = "local" | "cloud" | "hybrid" | "none";

export default function SetupPage() {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [dataDir, setDataDir] = useState("");
  const [modelsDir, setModelsDir] = useState("");
  const [vaultPath, setVaultPath] = useState("");
  const [aiMode, setAiMode] = useState<AiMode>("none");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canBrowse = Boolean(getDesktopBridge()?.pickDirectory);

  useEffect(() => {
    api
      .getSetupStatus()
      .then((s) => {
        setStatus(s);
        setDataDir(s.data_dir);
        setModelsDir(s.models_dir);
        setVaultPath(s.default_vault_path || s.active_vault_path || "");
        setAiMode((s.ai_setup_mode as AiMode) || "none");
      })
      .catch(() => setError("Could not load setup status."));
  }, []);

  async function browseInto(
    setter: (v: string) => void,
    title: string,
    current: string,
  ) {
    const dir = await pickDesktopDirectory({
      title,
      defaultPath: current || undefined,
    });
    if (dir) setter(dir);
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await api.saveSetupPaths({
        data_dir: dataDir,
        models_dir: modelsDir,
        default_vault_path: vaultPath || undefined,
        ai_setup_mode: aiMode,
      });
      setSaved(true);
      const s = await api.getSetupStatus();
      setStatus(s);
    } catch {
      setError("Failed to save paths. Check that directories are writable.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="relative min-h-screen text-white">
      <ShaderBackground />
      <div className="relative z-10 mx-auto max-w-2xl px-8 py-12">
        <div className="mb-8 flex items-center gap-3">
          <Sparkles className="h-7 w-7 text-amber-300" />
          <div>
            <h1 className="text-2xl font-semibold">Setup</h1>
            <p className="text-sm text-white/50">
              Data folders and AI mode — restart the desktop app after changing paths
            </p>
          </div>
        </div>

        {error && (
          <p className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-200">
            {error}
          </p>
        )}


        <form onSubmit={save} className="space-y-8">
          <section className="rounded-2xl border border-white/10 bg-black/40 p-6 space-y-4">
            <h2 className="flex items-center gap-2 text-lg font-medium">
              <FolderOpen className="h-5 w-5" /> Paths
            </h2>
            <p className="text-xs text-white/40">
              Notes vault is the folder of markdown files Orb reads and writes. You can
              change it anytime — click Save setup after browsing.
            </p>
            <label className="block text-sm">
              <span className="text-white/50">Notes vault folder</span>
              <div className="mt-1 flex gap-2">
                <input
                  value={vaultPath}
                  onChange={(e) => setVaultPath(e.target.value)}
                  className="w-full rounded-lg border border-white/15 bg-black/50 px-3 py-2 font-mono text-sm"
                  placeholder="~/Documents/Orb Vault"
                />
                {canBrowse && (
                  <button
                    type="button"
                    onClick={() =>
                      void browseInto(setVaultPath, "Choose notes vault folder", vaultPath)
                    }
                    className="shrink-0 rounded-lg border border-white/15 px-3 py-2 text-xs text-white/70 hover:bg-white/5 disabled:opacity-50"
                  >
                    Browse…
                  </button>
                )}
              </div>
            </label>
            <label className="block text-sm">
              <span className="text-white/50">Data directory (indexes, SQLite)</span>
              <div className="mt-1 flex gap-2">
                <input
                  value={dataDir}
                  onChange={(e) => setDataDir(e.target.value)}
                  className="w-full rounded-lg border border-white/15 bg-black/50 px-3 py-2 font-mono text-sm"
                  required
                />
                {canBrowse && (
                  <button
                    type="button"
                    onClick={() =>
                      void browseInto(setDataDir, "Choose data directory", dataDir)
                    }
                    className="shrink-0 rounded-lg border border-white/15 px-3 py-2 text-xs text-white/70 hover:bg-white/5 disabled:opacity-50"
                  >
                    Browse…
                  </button>
                )}
              </div>
            </label>
            <label className="block text-sm">
              <span className="text-white/50">Models directory (local ML weights)</span>
              <div className="mt-1 flex gap-2">
                <input
                  value={modelsDir}
                  onChange={(e) => setModelsDir(e.target.value)}
                  className="w-full rounded-lg border border-white/15 bg-black/50 px-3 py-2 font-mono text-sm"
                  required
                />
                {canBrowse && (
                  <button
                    type="button"
                    onClick={() =>
                      void browseInto(setModelsDir, "Choose models directory", modelsDir)
                    }
                    className="shrink-0 rounded-lg border border-white/15 px-3 py-2 text-xs text-white/70 hover:bg-white/5 disabled:opacity-50"
                  >
                    Browse…
                  </button>
                )}
              </div>
            </label>
          </section>

          <section className="rounded-2xl border border-white/10 bg-black/40 p-6 space-y-3">
            <h2 className="text-lg font-medium">AI setup</h2>
            <p className="text-sm text-white/50">
              You can skip AI and use Orb like Obsidian (notes, wikilinks, finance). Chat,
              ingest, and entity graph stay unavailable until AI is configured.
            </p>
            {(
              [
                {
                  id: "local" as const,
                  icon: Brain,
                  title: "Full local",
                  desc: "Pick a chat GGUF; Qwen3 embed + reranker size to your RAM automatically.",
                },
                {
                  id: "cloud" as const,
                  icon: Cloud,
                  title: "Cloud / hybrid",
                  desc: "OpenAI / Gemini / Anthropic in Settings; optional local multimodal",
                },
                {
                  id: "none" as const,
                  icon: SkipForward,
                  title: "Skip for now",
                  desc: "Obsidian-like limited mode — set up AI later in Settings",
                },
              ] as const
            ).map((opt) => {
              const Icon = opt.icon;
              const active = aiMode === opt.id || (opt.id === "cloud" && aiMode === "hybrid");
              return (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => setAiMode(opt.id)}
                  className={`flex w-full items-start gap-3 rounded-xl border px-4 py-3 text-left transition ${
                    active
                      ? "border-amber-400/40 bg-amber-400/10"
                      : "border-white/10 bg-black/30 hover:border-white/20"
                  }`}
                >
                  <Icon className="mt-0.5 h-5 w-5 shrink-0 text-amber-200" />
                  <div>
                    <div className="font-medium">{opt.title}</div>
                    <div className="text-xs text-white/45">{opt.desc}</div>
                  </div>
                </button>
              );
            })}
          </section>

          {aiMode === "local" && (
            <section className="rounded-2xl border border-white/10 bg-black/40 p-6 space-y-3">
              <h2 className="text-lg font-medium">Local models</h2>
              <p className="text-xs text-white/45">
                Downloading a chat model, choosing which one each knowledge base
                uses, and cloud endpoints all live on one page now.
              </p>
              <a
                href="/models"
                className="inline-flex items-center gap-2 rounded-xl border border-purple-500/40 bg-purple-500/10 px-4 py-2.5 text-sm text-purple-200 transition hover:bg-purple-500/20"
              >
                Open Models →
              </a>
            </section>
          )}

          <button
            type="submit"
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-lg bg-amber-500/20 px-5 py-2.5 text-sm text-amber-100 hover:bg-amber-500/30 disabled:opacity-50"
          >
            {saved ? <Check className="h-4 w-4" /> : null}
            {saving ? "Saving…" : saved ? "Saved" : "Save setup"}
          </button>


          {status && (
            <p className="text-xs text-white/35">
              Backend: {status.database_backend} · AI mode: {status.ai_setup_mode || "none"} ·
              models: {status.local_models_ready ? "ready" : "missing"} · configured:{" "}
              {status.ai_configured ? "yes" : "no"}
            </p>
          )}
        </form>
      </div>
    </div>
  );
}
