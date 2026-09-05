"use client";

import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import {
    Settings,
    Check,
    Loader2,
    AlertCircle,
    RefreshCw,
    Calendar,
    Trash2,
    RotateCcw,
    BookOpen,
    Database,
} from "lucide-react";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import { ShaderBackground } from "@/components/shader-background";
import { useKB } from "@/lib/kb-context";
import type { SetupStatus } from "@/lib/types";

const LOCAL_PROVIDERS = new Set(["local", "ollama", "lm_studio"]);

interface LLMSettings {
    provider: string;
    model: string;
    ingestion_model: string;
    base_url: string;
}

export default function SettingsPage() {
    const { currentKB, currentKBName } = useKB();
    const [settings, setSettings] = useState<LLMSettings | null>(null);
    const [form, setForm] = useState({ provider: "", model: "", ingestion_model: "", base_url: "" });
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

    const [communityStatus, setCommunityStatus] = useState<"idle" | "running" | "done" | "error">("idle");
    const [digestStatus, setDigestStatus] = useState<"idle" | "running" | "done" | "error">("idle");
    const communityTriggeredRef = useRef(false);
    const digestTriggeredRef = useRef(false);

    const [resetStatus, setResetStatus] = useState<"idle" | "confirming" | "running" | "done" | "error">("idle");
    const [reingestStatus, setReingestStatus] = useState<"idle" | "running" | "done" | "error">("idle");
    const [reingestCount, setReingestCount] = useState<number | null>(null);
    const confirmResetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

    const [paths, setPaths] = useState<SetupStatus | null>(null);
    const [emptyStatus, setEmptyStatus] = useState<"idle" | "confirming" | "running" | "done" | "error">("idle");
    const [financeClearStatus, setFinanceClearStatus] = useState<
        "idle" | "confirming" | "running" | "done" | "error"
    >("idle");
    const confirmEmptyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const confirmFinanceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        api
            .getLLMSettings()
            .then((s) => {
                setSettings(s);
                setForm({ provider: s.provider, model: s.model, ingestion_model: s.ingestion_model, base_url: s.base_url });
            })
            .catch(() => setError("Could not load settings. Is the backend running?"))
            .finally(() => setLoading(false));
        api
            .getSetupStatus()
            .then(setPaths)
            .catch(() => {
                /* optional for guide */
            });
    }, []);

    useEffect(() => {
        let cancelled = false;
        let timer: ReturnType<typeof setTimeout> | undefined;

        // The sidebar indicator already polls this endpoint globally — here
        // poll fast only while a user-triggered job runs, back off when idle,
        // and pause while the window is hidden.
        const schedule = (active: boolean) => {
            if (cancelled) return;
            timer = setTimeout(poll, active ? 3000 : 15000);
        };

        const poll = async () => {
            if (cancelled) return;
            if (document.visibilityState === "hidden") {
                schedule(false);
                return;
            }
            let active = false;
            try {
                const status = await api.getMaintenanceStatus(currentKB);
                if (cancelled) return;
                active = Boolean(
                    status.community_detection.running ||
                        status.temporal_digests.running ||
                        communityTriggeredRef.current ||
                        digestTriggeredRef.current,
                );
                if (status.community_detection.running) {
                    setCommunityStatus("running");
                } else {
                    setCommunityStatus((prev) => {
                        if (prev === "running" && communityTriggeredRef.current) {
                            communityTriggeredRef.current = false;
                            setTimeout(() => setCommunityStatus("idle"), 3000);
                            return "done";
                        }
                        if (prev === "done" || prev === "error") return prev;
                        return "idle";
                    });
                }
                if (status.temporal_digests.running) {
                    setDigestStatus("running");
                } else {
                    setDigestStatus((prev) => {
                        if (prev === "running" && digestTriggeredRef.current) {
                            digestTriggeredRef.current = false;
                            setTimeout(() => setDigestStatus("idle"), 3000);
                            return "done";
                        }
                        if (prev === "done" || prev === "error") return prev;
                        return "idle";
                    });
                }
            } catch {
                // Silently ignore poll failures.
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

    const isLocal = LOCAL_PROVIDERS.has(form.provider);

    async function handleRebuildCommunities() {
        communityTriggeredRef.current = true;
        setCommunityStatus("running");
        try {
            await api.rebuildCommunities(currentKB);
        } catch {
            communityTriggeredRef.current = false;
            setCommunityStatus("error");
            setTimeout(() => setCommunityStatus("idle"), 4000);
        }
    }

    async function handleBuildDigests() {
        digestTriggeredRef.current = true;
        setDigestStatus("running");
        try {
            await api.buildTemporalDigests(undefined, currentKB);
        } catch {
            digestTriggeredRef.current = false;
            setDigestStatus("error");
            setTimeout(() => setDigestStatus("idle"), 4000);
        }
    }

    async function handleResetIngestion() {
        if (resetStatus === "idle") {
            // First click — ask for confirmation.
            setResetStatus("confirming");
            if (confirmResetTimer.current) clearTimeout(confirmResetTimer.current);
            confirmResetTimer.current = setTimeout(() => setResetStatus("idle"), 5000);
            return;
        }
        if (resetStatus === "confirming") {
            // Second click — execute.
            if (confirmResetTimer.current) clearTimeout(confirmResetTimer.current);
            setResetStatus("running");
            try {
                await api.resetIngestionData(currentKB);
                setResetStatus("done");
                setTimeout(() => setResetStatus("idle"), 4000);
            } catch {
                setResetStatus("error");
                setTimeout(() => setResetStatus("idle"), 4000);
            }
        }
    }

    async function handleEmptyKB() {
        if (emptyStatus === "idle") {
            setEmptyStatus("confirming");
            if (confirmEmptyTimer.current) clearTimeout(confirmEmptyTimer.current);
            confirmEmptyTimer.current = setTimeout(() => setEmptyStatus("idle"), 6000);
            return;
        }
        if (emptyStatus === "confirming") {
            if (confirmEmptyTimer.current) clearTimeout(confirmEmptyTimer.current);
            setEmptyStatus("running");
            try {
                await api.emptyKB(currentKB);
                setEmptyStatus("done");
                setTimeout(() => setEmptyStatus("idle"), 4000);
            } catch {
                setEmptyStatus("error");
                setTimeout(() => setEmptyStatus("idle"), 4000);
            }
        }
    }

    async function handleClearFinance() {
        if (financeClearStatus === "idle") {
            setFinanceClearStatus("confirming");
            if (confirmFinanceTimer.current) clearTimeout(confirmFinanceTimer.current);
            confirmFinanceTimer.current = setTimeout(() => setFinanceClearStatus("idle"), 6000);
            return;
        }
        if (financeClearStatus === "confirming") {
            if (confirmFinanceTimer.current) clearTimeout(confirmFinanceTimer.current);
            setFinanceClearStatus("running");
            try {
                await api.resetFinanceAdministration(currentKB);
                setFinanceClearStatus("done");
                setTimeout(() => setFinanceClearStatus("idle"), 4000);
            } catch {
                setFinanceClearStatus("error");
                setTimeout(() => setFinanceClearStatus("idle"), 4000);
            }
        }
    }

    async function handleReingestAll() {
        setReingestStatus("running");
        setReingestCount(null);
        try {
            const result = await api.reingestAll(currentKB);
            setReingestCount(result.notes_queued);
            setReingestStatus("done");
            setTimeout(() => setReingestStatus("idle"), 5000);
        } catch {
            setReingestStatus("error");
            setTimeout(() => setReingestStatus("idle"), 4000);
        }
    }

    async function handleSave() {
        if (!settings) return;
        setSaving(true);
        setError(null);
        setSaved(false);
        try {
            const patch: Partial<typeof form> = {};
            if (form.provider !== settings.provider) patch.provider = form.provider;
            // Model name fields only apply to cloud providers.
            if (!isLocal) {
                if (form.model !== settings.model) patch.model = form.model;
                if (form.ingestion_model !== settings.ingestion_model) {
                    patch.ingestion_model = form.ingestion_model;
                }
            }

            if (Object.keys(patch).length === 0) {
                setSaved(true);
                if (savedTimer.current) clearTimeout(savedTimer.current);
                savedTimer.current = setTimeout(() => setSaved(false), 2000);
                return;
            }

            const updated = await api.updateLLMSettings(patch);
            setSettings(updated);
            setForm({ provider: updated.provider, model: updated.model, ingestion_model: updated.ingestion_model, base_url: updated.base_url });
            setSaved(true);
            if (savedTimer.current) clearTimeout(savedTimer.current);
            savedTimer.current = setTimeout(() => setSaved(false), 2000);
        } catch {
            setError("Failed to save. Check the server logs.");
        } finally {
            setSaving(false);
        }
    }

    return (
        <div className="relative min-h-screen bg-black text-white">
            <ShaderBackground />

            <div className="relative z-10 max-w-2xl mx-auto px-6 py-16">
                {/* Header */}
                <motion.div
                    initial={{ opacity: 0, y: -10 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="mb-10"
                >
                    <div className="flex items-center gap-3 mb-2">
                        <Settings className="h-7 w-7 text-purple-400" />
                        <h1 className="text-3xl font-bold bg-gradient-to-r from-white to-white/60 bg-clip-text text-transparent">
                            Settings
                        </h1>
                    </div>
                    <p className="text-white/50 text-sm">
                        Choose where Chat and note ingestion get their AI from. Changes apply immediately.
                    </p>
                    <a
                        href="/setup"
                        className="mt-3 inline-flex text-sm text-amber-300/90 underline-offset-2 hover:underline"
                    >
                        Open Setup (paths + local GGUF download) →
                    </a>
                </motion.div>

                {loading ? (
                    <div className="flex items-center gap-2 text-white/40">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        <span className="text-sm">Loading…</span>
                    </div>
                ) : (
                    <motion.div
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.05 }}
                        className="space-y-6"
                    >
                        {/* Models moved to their own page: one place for one decision. */}
                        <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 space-y-3">
                            <h2 className="text-sm font-semibold text-white/70 uppercase tracking-wide">
                                AI models
                            </h2>
                            <p className="text-xs text-white/40">
                                Choosing a model — on this device or a cloud endpoint, for the
                                whole app or for one knowledge base — now lives in one place.
                            </p>
                            <a
                                href="/models"
                                className="inline-flex items-center gap-2 rounded-xl border border-purple-500/40 bg-purple-500/10 px-4 py-2.5 text-sm text-purple-200 transition hover:bg-purple-500/20"
                            >
                                Open Models →
                            </a>
                        </div>

                        {/* Maintenance */}
                        <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 space-y-4">
                            <h2 className="text-sm font-semibold text-white/70 uppercase tracking-wide">Maintenance</h2>
                            <p className="text-xs text-white/40">
                                Active knowledge base: <span className="text-white/70">{currentKBName}</span>
                            </p>
                            <div className="flex flex-col gap-2">
                                <button
                                    onClick={handleRebuildCommunities}
                                    disabled={communityStatus === "running"}
                                    className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 text-sm text-white/70 transition hover:border-white/20 hover:bg-white/10 hover:text-white disabled:opacity-50"
                                >
                                    {communityStatus === "running" ? (
                                        <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                                    ) : communityStatus === "done" ? (
                                        <Check className="h-4 w-4 shrink-0 text-green-400" />
                                    ) : communityStatus === "error" ? (
                                        <AlertCircle className="h-4 w-4 shrink-0 text-red-400" />
                                    ) : (
                                        <RefreshCw className="h-4 w-4 shrink-0" />
                                    )}
                                    {communityStatus === "running"
                                        ? "Running…"
                                        : communityStatus === "done"
                                            ? "Done!"
                                            : communityStatus === "error"
                                                ? "Failed — check logs"
                                                : "Rebuild Communities"}
                                </button>
                                <button
                                    onClick={handleBuildDigests}
                                    disabled={digestStatus === "running"}
                                    className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 text-sm text-white/70 transition hover:border-white/20 hover:bg-white/10 hover:text-white disabled:opacity-50"
                                >
                                    {digestStatus === "running" ? (
                                        <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                                    ) : digestStatus === "done" ? (
                                        <Check className="h-4 w-4 shrink-0 text-green-400" />
                                    ) : digestStatus === "error" ? (
                                        <AlertCircle className="h-4 w-4 shrink-0 text-red-400" />
                                    ) : (
                                        <Calendar className="h-4 w-4 shrink-0" />
                                    )}
                                    {digestStatus === "running"
                                        ? "Running…"
                                        : digestStatus === "done"
                                            ? "Done!"
                                            : digestStatus === "error"
                                                ? "Failed — check logs"
                                                : "Build Temporal Digests"}
                                </button>
                                <button
                                    onClick={handleReingestAll}
                                    disabled={reingestStatus === "running"}
                                    className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 text-sm text-white/70 transition hover:border-white/20 hover:bg-white/10 hover:text-white disabled:opacity-50"
                                >
                                    {reingestStatus === "running" ? (
                                        <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                                    ) : reingestStatus === "done" ? (
                                        <Check className="h-4 w-4 shrink-0 text-green-400" />
                                    ) : reingestStatus === "error" ? (
                                        <AlertCircle className="h-4 w-4 shrink-0 text-red-400" />
                                    ) : (
                                        <RotateCcw className="h-4 w-4 shrink-0" />
                                    )}
                                    {reingestStatus === "running"
                                        ? "Queueing notes…"
                                        : reingestStatus === "done"
                                            ? `Queued ${reingestCount ?? 0} notes`
                                            : reingestStatus === "error"
                                                ? "Failed — check logs"
                                                : "Re-ingest All Notes"}
                                </button>
                            </div>
                            <p className="text-xs text-white/30">Jobs run in the background — monitor progress via server logs.</p>
                        </div>

                        {/* Data cleanup */}
                        <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 space-y-4">
                            <div className="flex items-center gap-2">
                                <Database className="h-4 w-4 text-white/50" />
                                <h2 className="text-sm font-semibold text-white/70 uppercase tracking-wide">Data</h2>
                            </div>
                            <p className="text-xs text-white/40">
                                Destructive actions for <span className="text-white/70">{currentKBName}</span>.
                                Prefer the lightest wipe that solves your problem.
                            </p>
                            <div className="flex flex-col gap-2">
                                <button
                                    onClick={handleResetIngestion}
                                    disabled={resetStatus === "running"}
                                    className={`flex items-center gap-2 rounded-xl border px-4 py-2.5 text-sm transition disabled:opacity-50 ${resetStatus === "confirming"
                                            ? "border-red-500/50 bg-red-500/10 text-red-300 hover:bg-red-500/20"
                                            : "border-white/10 bg-white/5 text-white/70 hover:border-red-500/30 hover:bg-red-500/5 hover:text-red-300"
                                        }`}
                                >
                                    {resetStatus === "running" ? (
                                        <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                                    ) : resetStatus === "done" ? (
                                        <Check className="h-4 w-4 shrink-0 text-green-400" />
                                    ) : resetStatus === "error" ? (
                                        <AlertCircle className="h-4 w-4 shrink-0 text-red-400" />
                                    ) : (
                                        <Trash2 className="h-4 w-4 shrink-0" />
                                    )}
                                    {resetStatus === "confirming"
                                        ? "Confirm? Clear entities & indexes (keep vault files)"
                                        : resetStatus === "running"
                                            ? "Resetting…"
                                            : resetStatus === "done"
                                                ? "Done!"
                                                : resetStatus === "error"
                                                    ? "Failed — check logs"
                                                    : "Reset Ingestion Data"}
                                </button>
                                <p className="px-1 text-[11px] text-white/30">
                                    Clears entities, relationships, and search indexes for this KB.
                                    Vault markdown files stay; notes are marked unprocessed.
                                </p>

                                <button
                                    onClick={handleEmptyKB}
                                    disabled={emptyStatus === "running"}
                                    className={`flex items-center gap-2 rounded-xl border px-4 py-2.5 text-sm transition disabled:opacity-50 ${emptyStatus === "confirming"
                                            ? "border-red-500/50 bg-red-500/10 text-red-300 hover:bg-red-500/20"
                                            : "border-white/10 bg-white/5 text-white/70 hover:border-red-500/30 hover:bg-red-500/5 hover:text-red-300"
                                        }`}
                                >
                                    {emptyStatus === "running" ? (
                                        <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                                    ) : emptyStatus === "done" ? (
                                        <Check className="h-4 w-4 shrink-0 text-green-400" />
                                    ) : emptyStatus === "error" ? (
                                        <AlertCircle className="h-4 w-4 shrink-0 text-red-400" />
                                    ) : (
                                        <Trash2 className="h-4 w-4 shrink-0" />
                                    )}
                                    {emptyStatus === "confirming"
                                        ? "Confirm? Empty entire KB (notes + vault + finance)"
                                        : emptyStatus === "running"
                                            ? "Emptying…"
                                            : emptyStatus === "done"
                                                ? "Done!"
                                                : emptyStatus === "error"
                                                    ? "Failed — check logs"
                                                    : "Empty this knowledge base"}
                                </button>
                                <p className="px-1 text-[11px] text-white/30">
                                    Always deletes notes, vault files, indexes, and Firefly admin.
                                    The KB itself remains.
                                </p>

                                <button
                                    onClick={handleClearFinance}
                                    disabled={financeClearStatus === "running"}
                                    className={`flex items-center gap-2 rounded-xl border px-4 py-2.5 text-sm transition disabled:opacity-50 ${financeClearStatus === "confirming"
                                            ? "border-red-500/50 bg-red-500/10 text-red-300 hover:bg-red-500/20"
                                            : "border-white/10 bg-white/5 text-white/70 hover:border-red-500/30 hover:bg-red-500/5 hover:text-red-300"
                                        }`}
                                >
                                    {financeClearStatus === "running" ? (
                                        <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                                    ) : financeClearStatus === "done" ? (
                                        <Check className="h-4 w-4 shrink-0 text-green-400" />
                                    ) : financeClearStatus === "error" ? (
                                        <AlertCircle className="h-4 w-4 shrink-0 text-red-400" />
                                    ) : (
                                        <Trash2 className="h-4 w-4 shrink-0" />
                                    )}
                                    {financeClearStatus === "confirming"
                                        ? "Confirm? Destroy Firefly admin for this KB"
                                        : financeClearStatus === "running"
                                            ? "Clearing…"
                                            : financeClearStatus === "done"
                                                ? "Done!"
                                                : financeClearStatus === "error"
                                                    ? "Failed — check logs"
                                                    : "Clear finance data"}
                                </button>
                            </div>
                            <div className="flex flex-wrap gap-3 pt-1 text-xs">
                                <Link href="/notes" className="text-purple-300/90 hover:text-purple-200 underline-offset-2 hover:underline">
                                    Notes — batch select &amp; delete
                                </Link>
                                <Link href="/kb" className="text-purple-300/90 hover:text-purple-200 underline-offset-2 hover:underline">
                                    Knowledge bases — empty / delete
                                </Link>
                                <Link href="/finance" className="text-purple-300/90 hover:text-purple-200 underline-offset-2 hover:underline">
                                    Finance
                                </Link>
                            </div>
                        </div>

                        {/* Data guide */}
                        <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 space-y-4">
                            <div className="flex items-center gap-2">
                                <BookOpen className="h-4 w-4 text-white/50" />
                                <h2 className="text-sm font-semibold text-white/70 uppercase tracking-wide">
                                    Data guide
                                </h2>
                            </div>
                            <div className="space-y-3 text-xs text-white/45 leading-relaxed">
                                <p>
                                    <span className="text-white/70 font-medium">Reset Ingestion Data</span> —
                                    graph + vectors + keyword index gone; <code className="text-white/55">.md</code> files stay.
                                </p>
                                <p>
                                    <span className="text-white/70 font-medium">Empty / Delete KB</span> —
                                    always removes notes, vault files, indexes, and Firefly admin for that KB.
                                    Delete also unregisters the KB (not allowed for default — use Empty instead).
                                </p>
                                <p>
                                    <span className="text-white/70 font-medium">Where data lives</span> —
                                    your chosen data directory holds SQLite, Qdrant, Meilisearch, Kuzu, Firefly, binaries, and logs.
                                </p>
                                {paths?.data_dir && (
                                    <p className="rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-mono text-[11px] text-teal-200/80 break-all">
                                        data_dir: {paths.data_dir}
                                    </p>
                                )}
                                <p>
                                    <span className="text-white/70 font-medium">Where models live</span> —
                                    GGUF chat/embed/rerank plus Florence / Whisper / Marlin snapshots.
                                </p>
                                {paths?.models_dir && (
                                    <p className="rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-mono text-[11px] text-teal-200/80 break-all">
                                        models_dir: {paths.models_dir}
                                    </p>
                                )}
                                <p>
                                    <span className="text-white/70 font-medium">How to delete models</span> —
                                    quit Orb, delete files inside <code className="text-white/55">models_dir</code>{" "}
                                    (or the whole folder), relaunch, then use Setup to re-download what you need.
                                    Do not delete models while the app is running — they may be memory-mapped.
                                </p>
                                <p>
                                    <span className="text-white/70 font-medium">Wipe all app data</span> —
                                    quit Orb, delete the entire <code className="text-white/55">data_dir</code>, relaunch
                                    (wizard if needed). The app binary stays. Models are untouched unless you also clear{" "}
                                    <code className="text-white/55">models_dir</code>.
                                </p>
                                <p>
                                    <span className="text-white/70 font-medium">paths.json</span> —
                                    records data_dir and models_dir from the first-run wizard.
                                </p>
                                {paths?.paths_json && (
                                    <p className="rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-mono text-[11px] text-teal-200/80 break-all">
                                        {paths.paths_json}
                                    </p>
                                )}
                            </div>
                        </div>

                        {/* Error */}
                        {error && (
                            <div className="flex items-center gap-3 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
                                <AlertCircle className="h-4 w-4 shrink-0" />
                                {error}
                            </div>
                        )}

                        {/* Save */}
                        <div className="flex justify-end">
                            <button
                                onClick={handleSave}
                                disabled={saving || saved}
                                className="inline-flex w-36 items-center justify-center gap-2 rounded-xl bg-purple-600 px-6 py-2.5 text-sm font-medium text-white transition hover:bg-purple-500 disabled:opacity-60"
                            >
                                {saving ? (
                                    <><Loader2 className="h-4 w-4 animate-spin" /> Saving…</>
                                ) : saved ? (
                                    <><Check className="h-4 w-4" /> Saved</>
                                ) : (
                                    "Save changes"
                                )}
                            </button>
                        </div>
                    </motion.div>
                )}
            </div>
        </div>
    );
}
