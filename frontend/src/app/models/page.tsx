"use client";

import { useCallback, useEffect, useState } from "react";
import { Cloud, Cpu, Download, Loader2, RotateCcw } from "lucide-react";
import { api } from "@/lib/api";
import { useKB } from "@/lib/kb-context";
import { getDesktopBridge } from "@/lib/desktop";
import { cn } from "@/lib/utils";
import { SettingRow, SettingsShell } from "@/components/settings-shell";
import type { ModelsPageState } from "@/lib/models-types";
import { endpointName, endpointRequestUrl, setEndpointName } from "@/lib/endpoint-names";
import { Card, ModelPicker, SavedTick } from "./_components/ModelPicker";

type Mode = "local" | "cloud";

const INTRO =
  "One place for every model decision. Local by default; a cloud endpoint is opt-in per workspace.";

/**
 * The single page for every model decision: how this knowledge base answers,
 * then what the rest of the app defaults to, then the models and keys those
 * choices draw on.
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
        await api.updateKBLLM(state.kb.id, { provider: "", model: "", ingestion_model: "", base_url: "" });
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
        await api.updateLLMSettings({ provider: "openai_compat", model: sysCloudModel, base_url: sysUrl });
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
      // The name rides in the fragment, so "Personal" and "Work" on one
      // server are two endpoints with two keys.
      const base = endpointRequestUrl(newUrl.trim());
      const url = newName.trim() ? `${base}#${newName.trim()}` : base;
      const result = await bridge.setEndpointCredential(url, newKey.trim());
      if (!result?.ok) throw new Error(result?.error || "Could not add the endpoint");
      if (!sysUrl) setSysUrl(url);
      if (!kbUrl) setKbUrl(url);
      setNewUrl("");
      setNewKey("");
      setNewName("");
      flash("endpoint");
      await load();
    } catch (err) {
      setError(describeError(err, "Could not add the endpoint."));
    } finally {
      setBusy(null);
    }
  }

  /** Transcription / Marlin models and the chat model's vision projector — fetched together. */
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
      <SettingsShell title="Models" intro={INTRO}>
        <div className="flex items-center gap-2 text-[12.5px] text-n-500">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading models…
        </div>
      </SettingsShell>
    );
  }

  const local = state?.local;
  const installed = local?.installed ?? [];
  const endpoints = state?.cloud.endpoints ?? [];
  const effective = state?.kb?.effective;

  const seg = (
    options: Array<{ key: string; active: boolean; onClick: () => void; icon: React.ReactNode; label: string }>,
  ) => (
    <div className="seg">
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          onClick={o.onClick}
          className={cn("seg-opt", o.active && "seg-opt-active")}
        >
          {o.icon}
          {o.label}
        </button>
      ))}
    </div>
  );

  const saveButton = (key: string, onClick: () => void) => (
    <div className="flex items-center justify-end gap-2">
      <SavedTick show={saved === key} />
      <button type="button" onClick={onClick} disabled={busy === key} className="btn btn-primary btn-sm">
        {busy === key ? <Loader2 className="h-3 w-3 animate-spin" /> : "Save"}
      </button>
    </div>
  );

  return (
    <SettingsShell title="Models" intro={INTRO}>
      {error && <div className="card mb-4 text-[12.5px] text-danger-text">{error}</div>}

      <div className="max-w-[620px] space-y-4">
        {/* 1 — the active knowledge base */}
        <Card
          accent
          title={`This workspace — ${currentKBName}`}
          subtitle="Applies only to the workspace you have open. Leave it inheriting unless this vault needs something different."
        >
          {seg([
            { key: "inherit", active: kbMode === "inherit", onClick: () => setKbMode("inherit"), icon: <RotateCcw className="h-3.5 w-3.5" />, label: "System model" },
            { key: "local", active: kbMode === "local", onClick: () => setKbMode("local"), icon: <Cpu className="h-3.5 w-3.5" />, label: "On this device" },
            { key: "cloud", active: kbMode === "cloud", onClick: () => setKbMode("cloud"), icon: <Cloud className="h-3.5 w-3.5" />, label: "Cloud endpoint" },
          ])}

          {kbMode === "local" && (
            <ModelPicker
              label="Model for this workspace"
              models={installed}
              value={kbModel}
              onChange={setKbModel}
              onBrowse={(p) => void applyBrowsedPath(p, "kb")}
              browsing={busy === "browse-kb"}
            />
          )}
          {kbMode === "cloud" && (
            <CloudFields endpoints={endpoints} url={kbUrl} setUrl={setKbUrl} model={kbCloudModel} setModel={setKbCloudModel} />
          )}

          {kbMode !== "inherit" && (
            <div className="space-y-1.5 border-t border-divider pt-3">
              <label className="flex items-center gap-2 text-[12px] text-n-400">
                <input
                  type="checkbox"
                  checked={kbIngestModel !== ""}
                  onChange={(e) =>
                    setKbIngestModel(e.target.checked ? (kbMode === "cloud" ? kbCloudModel : kbModel) : "")
                  }
                  className="h-3.5 w-3.5 accent-[var(--color-accent)]"
                />
                Use a different model for note ingestion
              </label>
              {kbIngestModel === "" ? (
                <p className="text-[11px] text-n-500">
                  Ingestion uses the model above. Extraction emits strict JSON the whole graph is
                  built from — a cheaper model saves on the highest-volume calls, but weak
                  structured output costs more than it saves.
                </p>
              ) : (
                <input
                  type="text"
                  value={kbIngestModel}
                  onChange={(e) => setKbIngestModel(e.target.value)}
                  placeholder="model name for extraction"
                  className="input input-mono"
                />
              )}
            </div>
          )}

          <div className="flex items-center justify-between gap-3 pt-1">
            <p className="text-[11px] text-n-500">
              Now using:{" "}
              <span className="text-n-300">
                {effective?.provider === "openai_compat"
                  ? `${effective?.base_url ?? "no endpoint"} · ${effective?.model ?? "—"}`
                  : `on this device · ${effective?.model ?? "—"}`}
              </span>
              {effective?.inherited ? " (inherited)" : " (pinned)"}
              {effective?.ingestion_model && effective.ingestion_model !== effective.model && (
                <>
                  {" · ingestion: "}
                  <span className="text-n-300">{effective.ingestion_model}</span>
                </>
              )}
            </p>
            {saveButton("kb", () => void saveKb())}
          </div>
        </Card>

        {/* 2 — the system default */}
        <Card title="Default for everything else" subtitle="Used by every workspace that has not pinned its own model.">
          {seg([
            { key: "local", active: sysMode === "local", onClick: () => setSysMode("local"), icon: <Cpu className="h-3.5 w-3.5" />, label: "On this device" },
            { key: "cloud", active: sysMode === "cloud", onClick: () => setSysMode("cloud"), icon: <Cloud className="h-3.5 w-3.5" />, label: "Cloud endpoint" },
          ])}

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
            <CloudFields endpoints={endpoints} url={sysUrl} setUrl={setSysUrl} model={sysCloudModel} setModel={setSysCloudModel} />
          )}

          {saveButton("system", () => void saveSystem())}
        </Card>

        {/* 3 — cloud endpoints */}
        <Card
          title="Cloud endpoints (optional)"
          subtitle="Any server speaking the OpenAI API — OpenRouter, Groq, Google's OpenAI endpoint, Together, vLLM, LM Studio, llama-server, Ollama. The URL plus the name you give it identifies the key, so one server can hold several accounts and two servers never share one."
        >
          {endpoints.length > 0 && (
            <div className="space-y-1.5">
              {endpoints.map((e) => (
                <div key={e} className="flex items-center gap-2 rounded-md px-3 py-2 shadow-sm">
                  <span className="w-32 shrink-0 truncate text-[12px] text-text" title={endpointName(e)}>
                    {endpointName(e)}
                  </span>
                  <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-n-500">{endpointRequestUrl(e)}</span>
                  <button
                    type="button"
                    onClick={() => void removeEndpoint(e)}
                    disabled={busy === "endpoint" || !bridge?.deleteEndpointCredential}
                    title="Remove this endpoint and forget its key"
                    className="btn btn-danger btn-sm"
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
              placeholder="Name, e.g. Personal — several names can share one URL, each with its own key"
              className="input"
            />
            <input
              type="text"
              value={newUrl}
              onChange={(e) => setNewUrl(e.target.value)}
              placeholder="https://generativelanguage.googleapis.com/v1beta/openai/"
              className="input input-mono"
            />
            <div className="flex gap-2">
              <input
                type="password"
                autoComplete="off"
                value={newKey}
                onChange={(e) => setNewKey(e.target.value)}
                placeholder="API key (blank for local servers)"
                className="input input-mono min-w-0 flex-1"
              />
              <button
                type="button"
                onClick={() => void addEndpoint()}
                disabled={busy === "endpoint" || !newUrl.trim() || !bridge?.setEndpointCredential}
                className="btn btn-primary"
              >
                {busy === "endpoint" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Add endpoint"}
              </button>
              <SavedTick show={saved === "endpoint"} />
            </div>
            {!bridge?.setEndpointCredential && (
              <p className="text-[11px] text-n-500">Adding endpoints requires the Orb desktop app.</p>
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
              <SettingRow
                key={m.id}
                title={m.label}
                description={`${m.params ? `${m.params} · ` : ""}~${m.size_gb} GB download`}
              >
                {m.recommended && <span className="tag tag-accent">suggested</span>}
                {!m.fits_budget && <span className="tag tag-neutral">may be tight</span>}
                {m.downloaded ? (
                  <span className="text-[11px] text-accent-300">on disk</span>
                ) : (
                  <button
                    type="button"
                    onClick={() => void download(m.id)}
                    disabled={busy === `dl-${m.id}`}
                    className="btn btn-secondary btn-sm"
                    title="Download"
                  >
                    {busy === `dl-${m.id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                  </button>
                )}
              </SettingRow>
            ))}
          </div>
          <p className="text-[11px] text-n-500">
            Downloads go to <span className="font-mono text-n-400">{local?.models_dir}</span>. Change
            that folder in <a href="/setup">Storage</a>.
          </p>
        </Card>

        {/* 5 — shared support models */}
        <Card
          title="Search and media models"
          subtitle="Chosen automatically and shared by every workspace. Embedding dimensions are tied to the search index, so these are deliberately not per-workspace."
        >
          <div className="grid gap-2 sm:grid-cols-2">
            <Readonly label="Embedding" value={local?.embed?.label ?? "—"} />
            <Readonly label="Reranker" value={local?.reranker?.label ?? "—"} />
          </div>
          <div className="space-y-1.5">
            {(local?.media ?? []).map((m) => (
              <SettingRow
                key={m.kind}
                icon={<span className={cn("dot", m.installed ? "bg-accent" : "bg-n-600")} />}
                title={m.label}
                description={`${m.purpose} · ${m.name}`}
              >
                {m.engine_note && <span className="tag tag-accent">{m.engine_note}</span>}
                {!m.installed && (
                  <span className="max-w-[220px] text-right text-[11px] text-n-500">
                    {m.hint ?? "not downloaded yet"}
                  </span>
                )}
              </SettingRow>
            ))}
          </div>
          <div className="flex items-center justify-between gap-3">
            <p className="text-[11px] text-n-500">
              {local?.hardware
                ? `${local.hardware.ram_gb} GB RAM · ${local.hardware.accel.backend} · ~${local.hardware.usable_model_gb} GB usable for models`
                : ""}
            </p>
            {(local?.media ?? []).some((m) => !m.installed) && (
              <div className="flex shrink-0 items-center gap-2">
                <SavedTick show={saved === "media"} />
                <button type="button" onClick={() => void downloadMedia()} disabled={busy === "media"} className="btn btn-secondary btn-sm">
                  {busy === "media" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Download media models"}
                </button>
              </div>
            )}
          </div>
        </Card>
      </div>
    </SettingsShell>
  );
}

function Readonly({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md px-3.5 py-2.5 shadow-sm">
      <p className="kicker">{label}</p>
      <p className="truncate text-[13px] text-n-300">{value}</p>
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
      <div className="field">
        <label>Endpoint</label>
        {endpoints.length > 0 && (
          <select
            value={endpoints.includes(url) ? url : "__custom__"}
            onChange={(e) => setUrl(e.target.value === "__custom__" ? "" : e.target.value)}
            className="input mb-2"
          >
            {endpoints.map((e) => (
              <option key={e} value={e}>
                {endpointName(e)} — {endpointRequestUrl(e)}
              </option>
            ))}
            <option value="__custom__">Another URL…</option>
          </select>
        )}
        {(endpoints.length === 0 || !endpoints.includes(url)) && (
          <input
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://openrouter.ai/api/v1"
            className="input input-mono"
          />
        )}
      </div>

      <div className="field">
        <label>Model name</label>
        <div className="flex gap-2">
          {showList ? (
            <select
              value={models.includes(model) ? model : ""}
              onChange={(e) => {
                if (e.target.value === "__type__") {
                  setTyping(true);
                  return;
                }
                setModel(e.target.value);
              }}
              className="input input-mono min-w-0 flex-1"
            >
              <option value="">{model ? `${model} (not in list)` : "Choose a model…"}</option>
              {models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
              <option value="__type__">Type a name instead…</option>
            </select>
          ) : (
            <input
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="gemini-2.5-flash"
              className="input input-mono min-w-0 flex-1"
            />
          )}
          <button
            type="button"
            onClick={() => void probe()}
            disabled={probing || !url.trim()}
            title="Ask the endpoint which models it serves"
            className="btn btn-secondary"
          >
            {probing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : models === null ? "List models" : "Refresh"}
          </button>
        </div>
      </div>
      {showList && (
        <p className="text-[11px] text-n-500">
          {models.length} model{models.length === 1 ? "" : "s"} from this endpoint.
        </p>
      )}
      {typing && models && models.length > 0 && (
        <button type="button" onClick={() => setTyping(false)} className="text-[11px] text-accent-300 hover:underline">
          ← Back to the {models.length} listed models
        </button>
      )}
      {note && <p className="text-[11px] text-n-400">{note}</p>}
    </div>
  );
}
