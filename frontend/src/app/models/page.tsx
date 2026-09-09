"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  ChevronDown,
  Cloud,
  Cpu,
  Download,
  Loader2,
  RotateCcw,
  Sparkles,
} from "lucide-react";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import { useKB } from "@/lib/kb-context";
import { getDesktopBridge } from "@/lib/desktop";
import { cn } from "@/lib/utils";
import { ShaderBackground } from "@/components/shader-background";
import type { ModelsPageState } from "@/lib/models-types";
import { endpointName, setEndpointName } from "@/lib/endpoint-names";
import { Card, ModelPicker, SavedTick } from "./_components/ModelPicker";

type Mode = "local" | "cloud";

/**
 * The single page for every model decision.
 *
 * Model choice used to live in three places — Setup (mode, downloads), Settings
 * (provider, cloud names, keys, endpoints) and the Knowledge Bases list (per-KB
 * pin). All of it is here, in the order a person actually decides: how this
 * knowledge base answers, then what the rest of the app defaults to, then the
 * models and keys those choices draw on.
 */
export default function ModelsPage() {
  const { currentKB, currentKBName, isHydrated } = useKB();
  const [state, setState] = useState<ModelsPageState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // Per-KB draft
  const [kbMode, setKbMode] = useState<Mode | "inherit">("inherit");
  const [kbModel, setKbModel] = useState("");
  const [kbUrl, setKbUrl] = useState("");
  const [kbCloudModel, setKbCloudModel] = useState("");
  // Empty = ingestion follows the chat model above. Kept opt-in: one model is
  // the right default, and the page exists to make model choices legible.
  const [kbIngestModel, setKbIngestModel] = useState("");
  const [newName, setNewName] = useState("");
  // Bumped when a name changes so the lists re-read localStorage.
  const [nameNonce, setNameNonce] = useState(0);

  // System draft
  const [sysMode, setSysMode] = useState<Mode>("local");
  const [sysModel, setSysModel] = useState("");
  const [sysUrl, setSysUrl] = useState("");
  const [sysCloudModel, setSysCloudModel] = useState("");

  // New endpoint
  const [newUrl, setNewUrl] = useState("");
  const [newKey, setNewKey] = useState("");

  const bridge = getDesktopBridge();

  const load = useCallback(async () => {
    try {
      const data = await api.getModelsPage(currentKB);
      setState(data);
      setSysMode(data.global.mode);
      setSysUrl(data.global.base_url ?? "");
      if (data.global.mode === "local") setSysModel(data.global.model ?? "");
      else setSysCloudModel(data.global.model ?? "");

      const ov = data.kb?.override;
      const pinned = Boolean(ov?.provider || ov?.model || ov?.base_url);
      setKbMode(!pinned ? "inherit" : ov?.provider === "openai_compat" ? "cloud" : "local");
      setKbUrl(ov?.base_url ?? "");
      if (ov?.provider === "openai_compat") setKbCloudModel(ov?.model ?? "");
      else setKbModel(ov?.model ?? "");
      setKbIngestModel(ov?.ingestion_model ?? "");
      setError(null);
    } catch {
      setError("Could not load models. Is the backend running?");
    } finally {
      setLoading(false);
    }
  }, [currentKB]);

  useEffect(() => {
    if (isHydrated) void load();
  }, [isHydrated, load]);

  function flash(key: string) {
    setSaved(key);
    setTimeout(() => setSaved((s) => (s === key ? null : s)), 2500);
  }

  function describeError(err: unknown, fallback: string): string {
    if (err && typeof err === "object" && "response" in err) {
      const detail = (err as { response?: { data?: { detail?: string } } }).response?.data
        ?.detail;
      if (detail) return detail;
    }
    return err instanceof Error && err.message ? err.message : fallback;
  }

  /** Validate a browsed-to path, then select it. */
  async function applyBrowsedPath(path: string, target: "kb" | "system") {
    setBusy(`browse-${target}`);
    setError(null);
    try {
      const info = await api.inspectModelPath(path);
      if (target === "kb") setKbModel(info.ref);
      else setSysModel(info.ref);
      await load();
    } catch (err) {
      setError(describeError(err, "That path is not a model Orb can use."));
    } finally {
      setBusy(null);
    }
  }

  async function saveKb() {
    if (!state?.kb) return;
    setBusy("kb");
    setError(null);
    try {
      if (kbMode === "inherit") {
        await api.updateKBLLM(state.kb.id, {
          provider: "",
          model: "",
          ingestion_model: "",
          base_url: "",
        });
      } else if (kbMode === "local") {
        await api.updateKBLLM(state.kb.id, {
          provider: "local",
          model: kbModel,
          ingestion_model: kbIngestModel,
          base_url: "",
        });
      } else {
        await api.updateKBLLM(state.kb.id, {
          provider: "openai_compat",
          model: kbCloudModel,
          ingestion_model: kbIngestModel,
          base_url: kbUrl,
        });
      }
      flash("kb");
      await load();
    } catch (err) {
      setError(describeError(err, "Could not save this knowledge base's model."));
    } finally {
      setBusy(null);
    }
  }

  async function saveSystem() {
    setBusy("system");
    setError(null);
    try {
      if (sysMode === "local") {
        await api.updateLLMSettings({ provider: "local", model: sysModel });
        if (sysModel && !sysModel.includes("/") && !sysModel.endsWith(".gguf")) {
          // A curated catalog id also drives the embed/rerank pairing.
          await api.selectChatModel(sysModel);
        }
      } else {
        await api.updateLLMSettings({
          provider: "openai_compat",
          model: sysCloudModel,
          base_url: sysUrl,
        });
      }
      flash("system");
      await load();
    } catch (err) {
      setError(describeError(err, "Could not save the system model."));
    } finally {
      setBusy(null);
    }
  }

  async function removeEndpoint(url: string) {
    if (!bridge?.deleteEndpointCredential) return;
    if (
      !confirm(
        `Remove ${endpointName(url)}?\n\n${url}\n\nIts key is forgotten. Any KB pinned to it stops working until you re-add it.`,
      )
    )
      return;
    setBusy("endpoint");
    setError(null);
    try {
      await bridge.deleteEndpointCredential(url);
      setEndpointName(url, "");
      setNameNonce((n) => n + 1);
      await load();
    } catch (err) {
      setError(describeError(err, "Could not remove the endpoint."));
    } finally {
      setBusy(null);
    }
  }

  async function addEndpoint() {
    if (!bridge?.setEndpointCredential || !newUrl.trim()) return;
    setBusy("endpoint");
    setError(null);
    try {
      const url = newUrl.trim();
      const result = await bridge.setEndpointCredential(url, newKey.trim());
      if (!result?.ok) throw new Error(result?.error || "Could not add the endpoint");
      if (newName.trim()) setEndpointName(url, newName.trim());
      if (!sysUrl) setSysUrl(newUrl.trim());
      if (!kbUrl) setKbUrl(newUrl.trim());
      setNewUrl("");
      setNewKey("");
      setNewName("");
      setNameNonce((n) => n + 1);
      flash("endpoint");
      await load();
    } catch (err) {
      setError(describeError(err, "Could not add the endpoint."));
    } finally {
      setBusy(null);
    }
  }

  /** Florence / Whisper / Marlin — fetched together, in the background. */
  async function downloadMedia() {
    setBusy("media");
    setError(null);
    try {
      await api.downloadModels(true, undefined, { multimodalOnly: true });
      flash("media");
      await load();
    } catch (err) {
      setError(describeError(err, "Could not download the media models."));
    } finally {
      setBusy(null);
    }
  }

  async function download(id: string) {
    setBusy(`dl-${id}`);
    setError(null);
    try {
      await api.downloadModels(false, id);
      flash(`dl-${id}`);
      await load();
    } catch (err) {
      setError(describeError(err, "Download failed."));
    } finally {
      setBusy(null);
    }
  }

  if (loading) {
    return (
      <div className="relative min-h-screen bg-black text-white">
        <ShaderBackground />
        <div className="relative z-10 flex items-center gap-2 px-6 py-16 text-white/40">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading models…
        </div>
      </div>
    );
  }

  const local = state?.local;
  const installed = local?.installed ?? [];
  const endpoints = state?.cloud.endpoints ?? [];
  const effective = state?.kb?.effective;

  const modeButton = (
    active: boolean,
    onClick: () => void,
    icon: React.ReactNode,
    title: string,
    sub: string,
  ) => (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex flex-1 items-start gap-3 rounded-xl border p-3 text-left transition",
        active
          ? "border-purple-500/50 bg-purple-500/10"
          : "border-white/10 bg-white/5 hover:border-white/25",
      )}
    >
      <span className={cn("mt-0.5", active ? "text-purple-300" : "text-white/40")}>
        {icon}
      </span>
      <span className="min-w-0">
        <span className="block text-sm text-white">{title}</span>
        <span className="block text-[11px] text-white/40">{sub}</span>
      </span>
    </button>
  );

  return (
    <div className="relative min-h-screen bg-black text-white">
      <ShaderBackground />
      <div className="relative z-10 mx-auto max-w-3xl px-6 py-16">
        <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-8">
          <div className="mb-2 flex items-center gap-3">
            <Sparkles className="h-7 w-7 text-purple-400" />
            <h1 className="bg-gradient-to-r from-white to-white/60 bg-clip-text text-3xl font-bold text-transparent">
              Models
            </h1>
          </div>
          <p className="text-sm text-white/50">
            Every model decision in one place. Chat and note ingestion use the model
            you pick here; embedding, reranking and media models are chosen
            automatically and shared by all knowledge bases.
          </p>
        </motion.div>

        {error && (
          <p className="mb-6 flex items-start gap-2 rounded-xl border border-red-500/25 bg-red-500/5 px-4 py-3 text-xs text-red-200/90">
            <AlertCircle className="mt-px h-3.5 w-3.5 shrink-0" />
            {error}
          </p>
        )}

        <div className="space-y-5">
          {/* 1 — the active knowledge base */}
          <Card
            accent
            title={`This knowledge base — ${currentKBName}`}
            subtitle="Applies only to the knowledge base you have open. Leave it inheriting unless this vault needs something different."
          >
            <div className="flex gap-2">
              {modeButton(kbMode === "inherit", () => setKbMode("inherit"), <RotateCcw className="h-4 w-4" />, "Use the system model", "Follows the setting below")}
              {modeButton(kbMode === "local", () => setKbMode("local"), <Cpu className="h-4 w-4" />, "On this device", "A model on your machine")}
              {modeButton(kbMode === "cloud", () => setKbMode("cloud"), <Cloud className="h-4 w-4" />, "Cloud endpoint", "Any OpenAI-compatible URL")}
            </div>

            {kbMode === "local" && (
              <ModelPicker
                label="Model for this knowledge base"
                models={installed}
                value={kbModel}
                onChange={setKbModel}
                onBrowse={(p) => void applyBrowsedPath(p, "kb")}
                browsing={busy === "browse-kb"}
              />
            )}
            {kbMode === "cloud" && (
              <CloudFields
                endpoints={endpoints}
                url={kbUrl}
                setUrl={setKbUrl}
                model={kbCloudModel}
                setModel={setKbCloudModel}
              />
            )}

            {kbMode !== "inherit" && (
              <div className="space-y-1.5 border-t border-white/5 pt-3">
                <label className="flex items-center gap-2 text-xs text-white/45">
                  <input
                    type="checkbox"
                    checked={kbIngestModel !== ""}
                    onChange={(e) =>
                      setKbIngestModel(
                        e.target.checked
                          ? kbMode === "cloud"
                            ? kbCloudModel
                            : kbModel
                          : "",
                      )
                    }
                    className="h-3.5 w-3.5 accent-purple-500"
                  />
                  Use a different model for note ingestion
                </label>
                {kbIngestModel === "" ? (
                  <p className="text-[11px] text-white/25">
                    Ingestion uses the model above. Extraction emits strict JSON
                    that the whole graph is built from — a cheaper model here
                    saves on the highest-volume calls, but weak structured
                    output costs more than it saves.
                  </p>
                ) : (
                  <input
                    type="text"
                    value={kbIngestModel}
                    onChange={(e) => setKbIngestModel(e.target.value)}
                    placeholder="model name for extraction"
                    className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 font-mono text-xs text-white placeholder-white/25 outline-none focus:border-purple-500/50"
                  />
                )}
              </div>
            )}

            <div className="flex items-center justify-between pt-1">
              <p className="text-[11px] text-white/30">
                Now using:{" "}
                <span className="text-white/60">
                  {effective?.provider === "openai_compat"
                    ? `${effective?.base_url ?? "no endpoint"} · ${effective?.model ?? "—"}`
                    : `on this device · ${effective?.model ?? "—"}`}
                </span>
                {effective?.inherited ? " (inherited)" : " (pinned)"}
                {effective?.ingestion_model &&
                  effective.ingestion_model !== effective.model && (
                    <>
                      {" · ingestion: "}
                      <span className="text-white/60">
                        {effective.ingestion_model}
                      </span>
                    </>
                  )}
              </p>
              <div className="flex items-center gap-2">
                <SavedTick show={saved === "kb"} />
                <button
                  type="button"
                  onClick={() => void saveKb()}
                  disabled={busy === "kb"}
                  className="rounded-xl bg-purple-600 px-4 py-2 text-xs font-medium text-white transition hover:bg-purple-500 disabled:opacity-50"
                >
                  {busy === "kb" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Save"}
                </button>
              </div>
            </div>
          </Card>

          {/* 2 — the system default */}
          <Card
            title="Default for everything else"
            subtitle="Used by every knowledge base that has not pinned its own model."
          >
            <div className="flex gap-2">
              {modeButton(sysMode === "local", () => setSysMode("local"), <Cpu className="h-4 w-4" />, "On this device", "Private; nothing leaves the machine")}
              {modeButton(sysMode === "cloud", () => setSysMode("cloud"), <Cloud className="h-4 w-4" />, "Cloud endpoint", "Any OpenAI-compatible URL")}
            </div>

            {sysMode === "local" ? (
              <ModelPicker
                label="System model"
                models={installed}
                value={sysModel}
                onChange={setSysModel}
                onBrowse={(p) => void applyBrowsedPath(p, "system")}
                browsing={busy === "browse-system"}
              />
            ) : (
              <CloudFields
                endpoints={endpoints}
                url={sysUrl}
                setUrl={setSysUrl}
                model={sysCloudModel}
                setModel={setSysCloudModel}
              />
            )}

            <div className="flex items-center justify-end gap-2">
              <SavedTick show={saved === "system"} />
              <button
                type="button"
                onClick={() => void saveSystem()}
                disabled={busy === "system"}
                className="rounded-xl bg-purple-600 px-4 py-2 text-xs font-medium text-white transition hover:bg-purple-500 disabled:opacity-50"
              >
                {busy === "system" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Save"}
              </button>
            </div>
          </Card>

          {/* 3 — cloud endpoints */}
          <Card
            title="Cloud endpoints"
            subtitle="Any server speaking the OpenAI API — OpenRouter, Groq, Google's OpenAI endpoint, Together, vLLM, LM Studio, llama-server, Ollama. The URL identifies the key, so two servers never share one."
          >
            {endpoints.length > 0 && (
              <div className="space-y-1.5">
                {endpoints.map((e) => (
                  <div
                    key={`${e}:${nameNonce}`}
                    className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-3 py-2"
                  >
                    <input
                      type="text"
                      defaultValue={endpointName(e)}
                      onBlur={(ev) => {
                        setEndpointName(e, ev.target.value);
                        setNameNonce((n) => n + 1);
                      }}
                      title="Rename this endpoint"
                      className="w-32 shrink-0 rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-xs text-white outline-none focus:border-purple-500/50"
                    />
                    <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-white/45">
                      {e}
                    </span>
                    <button
                      type="button"
                      onClick={() => void removeEndpoint(e)}
                      disabled={busy === "endpoint" || !bridge?.deleteEndpointCredential}
                      title="Remove this endpoint and forget its key"
                      className="shrink-0 rounded-lg border border-red-500/20 px-2 py-1 text-xs text-red-400/70 transition hover:border-red-500/40 hover:text-red-300 disabled:opacity-40"
                    >
                      Remove
                    </button>
                  </div>
                ))}
              </div>
            )}
            <div className="space-y-2">
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="Name (optional — defaults to the host)"
                className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs text-white placeholder-white/25 outline-none focus:border-purple-500/50"
              />
              <input
                type="text"
                value={newUrl}
                onChange={(e) => setNewUrl(e.target.value)}
                placeholder="https://generativelanguage.googleapis.com/v1beta/openai/"
                className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-mono text-xs text-white placeholder-white/25 outline-none focus:border-purple-500/50"
              />
              <div className="flex gap-2">
                <input
                  type="password"
                  autoComplete="off"
                  value={newKey}
                  onChange={(e) => setNewKey(e.target.value)}
                  placeholder="API key (blank for local servers)"
                  className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-mono text-xs text-white placeholder-white/25 outline-none focus:border-purple-500/50"
                />
                <button
                  type="button"
                  onClick={() => void addEndpoint()}
                  disabled={busy === "endpoint" || !newUrl.trim() || !bridge?.setEndpointCredential}
                  className="shrink-0 rounded-xl bg-purple-600 px-4 py-2 text-xs font-medium text-white transition hover:bg-purple-500 disabled:opacity-40"
                >
                  {busy === "endpoint" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Add"}
                </button>
                <SavedTick show={saved === "endpoint"} />
              </div>
              {!bridge?.setEndpointCredential && (
                <p className="text-[11px] text-amber-300/80">
                  Adding endpoints requires the Orb desktop app.
                </p>
              )}
            </div>
          </Card>

          {/* 4 — downloads */}
          <Card
            title="Download a model"
            subtitle={
              local?.hardware
                ? `Chat models Orb can fetch for you. ~${local.hardware.usable_model_gb} GB of ${local.hardware.ram_gb} GB is usable for models on this machine; anything larger is marked "may be tight" and can still be chosen.`
                : ""
            }
          >
            <div className="space-y-1.5">
              {(local?.downloadable ?? []).map((m) => (
                <div
                  key={m.id}
                  className={cn(
                    "flex items-center gap-3 rounded-xl border px-3 py-2.5",
                    m.downloaded
                      ? "border-green-500/25 bg-green-500/5"
                      : "border-white/10 bg-white/5",
                  )}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm text-white">{m.label}</span>
                      {m.recommended && (
                        <span className="rounded-full bg-purple-500/20 px-2 py-0.5 text-[10px] text-purple-300">
                          suggested
                        </span>
                      )}
                      {!m.fits_budget && (
                        <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] text-amber-300">
                          may be tight
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-white/35">
                      {m.params ? `${m.params} · ` : ""}~{m.size_gb} GB download
                    </p>
                  </div>
                  {m.downloaded ? (
                    <span className="shrink-0 text-[11px] text-green-400">on disk</span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => void download(m.id)}
                      disabled={busy === `dl-${m.id}`}
                      className="shrink-0 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-white/60 transition hover:border-white/25 hover:text-white disabled:opacity-40"
                    >
                      {busy === `dl-${m.id}` ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Download className="h-3.5 w-3.5" />
                      )}
                    </button>
                  )}
                </div>
              ))}
            </div>
            <p className="text-[11px] text-white/25">
              Downloads go to{" "}
              <span className="font-mono text-white/40">{local?.models_dir}</span>. Change
              that folder in <a href="/setup" className="underline underline-offset-2">Setup</a>.
            </p>
          </Card>

          {/* 5 — shared support models */}
          <Card
            title="Search and media models"
            subtitle="Chosen automatically and shared by every knowledge base. Embedding dimensions are tied to the search index, so these are deliberately not per-KB."
          >
            <div className="grid gap-2 sm:grid-cols-2">
              <Readonly label="Embedding" value={local?.embed?.label ?? "—"} />
              <Readonly label="Reranker" value={local?.reranker?.label ?? "—"} />
            </div>
            <div className="space-y-1.5">
              {(local?.media ?? []).map((m) => (
                <div
                  key={m.kind}
                  className="flex items-center gap-3 rounded-xl border border-white/10 bg-white/[0.02] px-4 py-2.5"
                >
                  <span
                    className={cn(
                      "mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full",
                      m.installed ? "bg-green-400/70" : "bg-white/20",
                    )}
                    title={m.installed ? "Downloaded" : "Not downloaded"}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm text-white/80">{m.label}</span>
                      <span className="truncate font-mono text-[10px] text-white/30">
                        {m.name}
                      </span>
                      {m.engine_note && (
                        <span className="rounded-full bg-purple-500/15 px-2 py-0.5 text-[10px] text-purple-300">
                          {m.engine_note}
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-white/35">{m.purpose}</p>
                  </div>
                  {!m.installed && (
                    <span className="shrink-0 text-[10px] text-white/30">
                      downloads on first use
                    </span>
                  )}
                </div>
              ))}
            </div>
            <div className="flex items-center justify-between gap-3">
              <p className="text-[11px] text-white/25">
                {local?.hardware
                  ? `${local.hardware.ram_gb} GB RAM · ${local.hardware.accel.backend} · ~${local.hardware.usable_model_gb} GB usable for models`
                  : ""}
              </p>
              {(local?.media ?? []).some((m) => !m.installed) && (
                <div className="flex shrink-0 items-center gap-2">
                  <SavedTick show={saved === "media"} />
                  <button
                    type="button"
                    onClick={() => void downloadMedia()}
                    disabled={busy === "media"}
                    className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-white/60 transition hover:border-white/25 hover:text-white disabled:opacity-40"
                  >
                    {busy === "media" ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      "Download media models"
                    )}
                  </button>
                </div>
              )}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Readonly({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] px-4 py-2.5">
      <p className="text-[10px] uppercase tracking-wide text-white/35">{label}</p>
      <p className="truncate text-sm text-white/70">{value}</p>
    </div>
  );
}

function CloudFields({
  endpoints,
  url,
  setUrl,
  model,
  setModel,
}: {
  endpoints: string[];
  url: string;
  setUrl: (v: string) => void;
  model: string;
  setModel: (v: string) => void;
}) {
  const [models, setModels] = useState<string[] | null>(null);
  const [probing, setProbing] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  // Endpoints that list hundreds of models are still worth typing into, and a
  // fetched list can omit one the server accepts — so typing stays available.
  const [typing, setTyping] = useState(false);
  const showList = !typing && models !== null && models.length > 0;

  async function probe() {
    if (!url.trim()) return;
    setProbing(true);
    setNote(null);
    try {
      const data = await api.getEndpointModels(url.trim());
      const sorted = [...data.models].sort((a, b) => a.localeCompare(b));
      setModels(sorted);
      setTyping(false);
      if (!sorted.length) setNote("That endpoint listed no models — type the name.");
      else if (model && !sorted.includes(model)) {
        setNote(`"${model}" is not in this endpoint's list — keeping it anyway.`);
      }
    } catch {
      setNote("This endpoint has no model list. Type the model name instead.");
    } finally {
      setProbing(false);
    }
  }

  return (
    <div className="space-y-2">
      <label className="text-xs text-white/45">Endpoint</label>
      {endpoints.length > 0 ? (
        <div className="relative">
          <select
            value={endpoints.includes(url) ? url : "__custom__"}
            onChange={(e) => setUrl(e.target.value === "__custom__" ? "" : e.target.value)}
            className="w-full appearance-none rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 pr-9 text-sm text-white outline-none transition focus:border-purple-500/50"
          >
            {endpoints.map((e) => (
              <option key={e} value={e} className="bg-[#0d0d12]">
                {endpointName(e)} — {e}
              </option>
            ))}
            <option value="__custom__" className="bg-[#0d0d12]">
              Another URL…
            </option>
          </select>
          <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-white/40" />
        </div>
      ) : null}
      {(endpoints.length === 0 || !endpoints.includes(url)) && (
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://openrouter.ai/api/v1"
          className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 font-mono text-xs text-white placeholder-white/25 outline-none focus:border-purple-500/50"
        />
      )}

      <label className="text-xs text-white/45">Model name</label>
      <div className="flex gap-2">
        {showList ? (
          <div className="relative min-w-0 flex-1">
            <select
              value={models.includes(model) ? model : ""}
              onChange={(e) => {
                if (e.target.value === "__type__") {
                  setTyping(true);
                  return;
                }
                setModel(e.target.value);
              }}
              className="w-full appearance-none rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 pr-9 font-mono text-xs text-white outline-none transition focus:border-purple-500/50"
            >
              <option value="" className="bg-[#0d0d12]">
                {model ? `${model} (not in list)` : "Choose a model…"}
              </option>
              {models.map((m) => (
                <option key={m} value={m} className="bg-[#0d0d12]">
                  {m}
                </option>
              ))}
              <option value="__type__" className="bg-[#0d0d12]">
                Type a name instead…
              </option>
            </select>
            <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-white/40" />
          </div>
        ) : (
          <input
            type="text"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="gemini-2.5-flash"
            className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 font-mono text-xs text-white placeholder-white/25 outline-none focus:border-purple-500/50"
          />
        )}
        <button
          type="button"
          onClick={() => void probe()}
          disabled={probing || !url.trim()}
          title="Ask the endpoint which models it serves"
          className="shrink-0 rounded-xl border border-white/10 bg-white/5 px-3 text-xs text-white/60 transition hover:border-white/25 hover:text-white disabled:opacity-40"
        >
          {probing ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : models === null ? (
            "List models"
          ) : (
            "Refresh"
          )}
        </button>
      </div>
      {showList && (
        <p className="text-[11px] text-white/35">
          {models.length} model{models.length === 1 ? "" : "s"} from this endpoint.
        </p>
      )}
      {typing && models && models.length > 0 && (
        <button
          type="button"
          onClick={() => setTyping(false)}
          className="text-[11px] text-purple-300/70 underline-offset-2 hover:underline"
        >
          ← Back to the {models.length} listed models
        </button>
      )}
      {note && <p className="text-[11px] text-amber-300/80">{note}</p>}
    </div>
  );
}
