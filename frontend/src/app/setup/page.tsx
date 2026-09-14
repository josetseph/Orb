"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { useKB } from "@/lib/kb-context";
import { pickDesktopDirectory, getDesktopBridge } from "@/lib/desktop";
import { SettingRow, SettingsShell } from "@/components/settings-shell";
import type { SetupStatus } from "@/lib/types";

type Job = "idle" | "running" | "done" | "error";

export default function SetupPage() {
  const { currentKB } = useKB();
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [dataDir, setDataDir] = useState("");
  const [modelsDir, setModelsDir] = useState("");
  const [vaultPath, setVaultPath] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canBrowse = Boolean(getDesktopBridge()?.pickDirectory);

  const [reingest, setReingest] = useState<Job>("idle");
  const [reingestCount, setReingestCount] = useState<number | null>(null);
  const [rebuild, setRebuild] = useState<Job>("idle");
  const rebuildTriggeredRef = useRef(false);
  const [reset, setReset] = useState<Job | "confirming">("idle");
  const confirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    api
      .getSetupStatus()
      .then((s) => {
        setStatus(s);
        setDataDir(s.data_dir);
        setModelsDir(s.models_dir);
        setVaultPath(s.default_vault_path || s.active_vault_path || "");
      })
      .catch(() => setError("Could not load setup status."));
  }, []);

  // Poll fast only while the user-triggered rebuild runs; back off when idle
  // and pause while hidden — the sidebar already polls this globally.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = (active: boolean) => {
      if (!cancelled) timer = setTimeout(poll, active ? 3000 : 15000);
    };
    const poll = async () => {
      if (cancelled) return;
      if (document.visibilityState === "hidden") return schedule(false);
      let active = false;
      try {
        const s = await api.getMaintenanceStatus(currentKB);
        if (cancelled) return;
        const running = s.community_detection.running || s.temporal_digests.running;
        active = Boolean(running || rebuildTriggeredRef.current);
        if (running) {
          setRebuild("running");
        } else if (rebuildTriggeredRef.current) {
          rebuildTriggeredRef.current = false;
          setRebuild("done");
          setTimeout(() => setRebuild("idle"), 3000);
        }
      } catch {
        /* ignore */
      }
      schedule(active);
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        clearTimeout(timer);
        void poll();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    void poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [currentKB]);

  async function browseInto(setter: (v: string) => void, title: string, current: string) {
    const dir = await pickDesktopDirectory({ title, defaultPath: current || undefined });
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
      });
      setSaved(true);
      setStatus(await api.getSetupStatus());
    } catch {
      setError("Failed to save paths. Check that directories are writable.");
    } finally {
      setSaving(false);
    }
  }

  async function handleReingest() {
    setReingest("running");
    setReingestCount(null);
    try {
      const r = await api.reingestAll(currentKB);
      setReingestCount(r.notes_queued);
      setReingest("done");
      setTimeout(() => setReingest("idle"), 5000);
    } catch {
      setReingest("error");
      setTimeout(() => setReingest("idle"), 4000);
    }
  }

  async function handleRebuild() {
    rebuildTriggeredRef.current = true;
    setRebuild("running");
    try {
      await api.rebuildCommunities(currentKB);
      await api.buildTemporalDigests(undefined, currentKB);
    } catch {
      rebuildTriggeredRef.current = false;
      setRebuild("error");
      setTimeout(() => setRebuild("idle"), 4000);
    }
  }

  async function handleReset() {
    if (reset === "idle") {
      setReset("confirming");
      if (confirmTimer.current) clearTimeout(confirmTimer.current);
      confirmTimer.current = setTimeout(() => setReset("idle"), 5000);
      return;
    }
    if (reset !== "confirming") return;
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
    setReset("running");
    try {
      await api.resetIngestionData(currentKB);
      setReset("done");
    } catch {
      setReset("error");
    }
    setTimeout(() => setReset("idle"), 4000);
  }

  const pathField = (
    label: string,
    value: string,
    setter: (v: string) => void,
    title: string,
    required = false,
    placeholder?: string,
  ) => (
    <div className="field">
      <label>{label}</label>
      <div className="flex gap-2">
        <input
          className="input input-mono"
          value={value}
          onChange={(e) => setter(e.target.value)}
          required={required}
          placeholder={placeholder}
        />
        {canBrowse && (
          <button type="button" className="btn btn-secondary" onClick={() => void browseInto(setter, title, value)}>
            Browse…
          </button>
        )}
      </div>
    </div>
  );

  return (
    <SettingsShell title="Storage" intro="Where Orb keeps indexes and model weights. Restart after changing a path.">
      {error && <div className="card mb-4 text-[12.5px] text-danger-text">{error}</div>}

      <form onSubmit={save} className="max-w-[560px] space-y-3.5">
        {pathField("Notes vault folder — the Markdown files Orb reads and writes", vaultPath, setVaultPath, "Choose notes vault folder", false, "~/Documents/Orb Vault")}
        {pathField("Data folder — graph, vectors, search index", dataDir, setDataDir, "Choose data directory", true)}
        {pathField("Models folder — local model weights", modelsDir, setModelsDir, "Choose models directory", true)}
        <div className="flex items-center gap-3">
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : saved ? <Check className="h-3.5 w-3.5" /> : null}
            {saving ? "Saving…" : saved ? "Saved" : "Save"}
          </button>
          {status && (
            <span className="text-[11px] text-n-500">
              Backend: {status.database_backend} · AI: {status.ai_setup_mode || "none"} · models:{" "}
              {status.local_models_ready ? "ready" : "missing"} · configured: {status.ai_configured ? "yes" : "no"}
            </span>
          )}
        </div>
      </form>

      <div className="kicker mb-2 mt-7">Maintenance</div>
      <div className="max-w-[560px] space-y-2">
        <SettingRow title="Re-ingest whole vault" description="Rebuilds entities and links for every note.">
          <button type="button" className="btn btn-primary btn-sm" disabled={reingest === "running"} onClick={() => void handleReingest()}>
            {reingest === "running" ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : reingest === "done" ? (
              `Queued ${reingestCount ?? 0} notes`
            ) : reingest === "error" ? (
              "Failed — check logs"
            ) : (
              "Re-ingest"
            )}
          </button>
        </SettingRow>
        <SettingRow title="Rebuild communities and digests" description="Normally runs on its own after ingest settles.">
          <button type="button" className="btn btn-secondary btn-sm" disabled={rebuild === "running"} onClick={() => void handleRebuild()}>
            {rebuild === "running" ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : rebuild === "done" ? (
              <Check className="h-3 w-3" />
            ) : rebuild === "error" ? (
              "Failed — check logs"
            ) : (
              "Rebuild"
            )}
          </button>
        </SettingRow>
        <SettingRow title="Reset indexes" description="Deletes graph, vectors and search index. Your notes are untouched.">
          <button type="button" className="btn btn-danger btn-sm" disabled={reset === "running"} onClick={() => void handleReset()}>
            {reset === "running" ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : reset === "confirming" ? (
              "Confirm reset?"
            ) : reset === "done" ? (
              <Check className="h-3 w-3" />
            ) : reset === "error" ? (
              "Failed — check logs"
            ) : (
              "Reset…"
            )}
          </button>
        </SettingRow>
        <p className="text-[11px] text-n-500">Jobs run in the background for the current workspace ({currentKB}).</p>
      </div>
    </SettingsShell>
  );
}
