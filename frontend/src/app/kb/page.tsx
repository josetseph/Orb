"use client";

import { useState, useEffect, useCallback } from "react";
import {
    Plus,
    Trash2,
    Database,
    Check,
    Loader2,
    X,
    FolderOpen,
    Pencil,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { api } from "@/lib/api";
import { useKB } from "@/lib/kb-context";
import { cn } from "@/lib/utils";
import { ShaderBackground } from "@/components/shader-background";
import { getDesktopBridge, pickDesktopDirectory } from "@/lib/desktop";
import type { KnowledgeBase } from "@/lib/types";
import { KBModelPanel } from "./_components/KBModelPanel";

export default function KBPage() {
    const { currentKB, setCurrentKB, setCurrentKBName } = useKB();
    const [kbs, setKBs] = useState<KnowledgeBase[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [isCreating, setIsCreating] = useState(false);
    const [showForm, setShowForm] = useState(false);
    const [newName, setNewName] = useState("");
    const [newVaultPath, setNewVaultPath] = useState("");
    const [deletingId, setDeletingId] = useState<string | null>(null);
    const [renamingId, setRenamingId] = useState<string | null>(null);
    const [renameValue, setRenameValue] = useState("");
    const [isRenaming, setIsRenaming] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const canBrowse = Boolean(getDesktopBridge()?.pickDirectory);

    const fetchKBs = useCallback(async () => {
        try {
            const data = await api.listKBs();
            setKBs(data.knowledge_bases);
        } catch {
            setError("Failed to load knowledge bases. Is the backend running?");
        } finally {
            setIsLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchKBs();
    }, [fetchKBs]);

    async function browseVaultFolder() {
        const dir = await pickDesktopDirectory({
            title: "Choose notes vault folder for this knowledge base",
            defaultPath: newVaultPath || undefined,
        });
        if (dir) setNewVaultPath(dir);
    }

    async function handleCreate(e: React.FormEvent) {
        e.preventDefault();
        const name = newName.trim();
        const vault = newVaultPath.trim();
        if (!name) return;
        if (!vault) {
            setError(
                "Choose a notes vault folder — where markdown files for this KB will be saved.",
            );
            return;
        }
        setIsCreating(true);
        setError(null);
        try {
            await api.createKB(name, vault);
            setNewName("");
            setNewVaultPath("");
            setShowForm(false);
            await fetchKBs();
        } catch (err: unknown) {
            let msg = "Failed to create knowledge base";
            if (err && typeof err === "object" && "response" in err) {
                const detail = (
                    err as { response?: { data?: { detail?: string } } }
                ).response?.data?.detail;
                if (detail) msg = detail;
            } else if (err instanceof Error && err.message) {
                msg = err.message;
            }
            setError(msg);
            // Create may have partially succeeded — refresh so the list matches disk/DB
            await fetchKBs();
        } finally {
            setIsCreating(false);
        }
    }

    async function handleRename(kb: KnowledgeBase) {
        const name = renameValue.trim();
        if (!name || name === kb.name) {
            setRenamingId(null);
            return;
        }
        setIsRenaming(true);
        setError(null);
        try {
            await api.renameKB(kb.id, name);
            // If this is the active KB, update the display name in context.
            if (isActive(kb)) setCurrentKBName(name);
            setRenamingId(null);
            await fetchKBs();
        } catch {
            setError(`Failed to rename "${kb.name}".`);
        } finally {
            setIsRenaming(false);
        }
    }

    function startRename(kb: KnowledgeBase) {
        setRenamingId(kb.id);
        setRenameValue(kb.name);
    }

    async function handleDelete(kb: KnowledgeBase) {
        if (
            !window.confirm(
                `Permanently delete knowledge base "${kb.name}"?\n\n` +
                    `This always removes:\n` +
                    `• All notes and vault files\n` +
                    `  ${kb.vault_path || "(no path)"}\n` +
                    `• Graph, vector, and search indexes\n` +
                    `• Firefly finance administration for this KB\n\n` +
                    `This cannot be undone.`,
            )
        ) {
            return;
        }

        setDeletingId(kb.id);
        setError(null);
        try {
            await api.deleteKB(kb.id);
            if (currentKB === kb.name || currentKB === kb.slug) {
                setCurrentKB("default");
            }
            await fetchKBs();
        } catch {
            setError(`Failed to delete "${kb.name}".`);
        } finally {
            setDeletingId(null);
        }
    }

    async function handleEmpty(kb: KnowledgeBase) {
        if (
            !window.confirm(
                `Empty knowledge base "${kb.name}"?\n\n` +
                    `This always removes all notes, vault files, indexes, and Firefly ` +
                    `data for this KB. The knowledge base itself stays.\n\n` +
                    `Vault: ${kb.vault_path || "(no path)"}`,
            )
        ) {
            return;
        }
        setDeletingId(kb.id);
        setError(null);
        try {
            const slug =
                kb.id === "default"
                    ? "default"
                    : (kb.slug ?? kb.name.toLowerCase().replace(/\s+/g, "_"));
            await api.emptyKB(slug);
            await fetchKBs();
        } catch {
            setError(`Failed to empty "${kb.name}".`);
        } finally {
            setDeletingId(null);
        }
    }

    async function handleDeleteAllNonDefault() {
        const extras = kbs.filter((k) => k.id !== "default");
        if (extras.length === 0) {
            setError("There are no other knowledge bases to delete.");
            return;
        }
        if (
            !window.confirm(
                `Delete all ${extras.length} non-default knowledge base(s)?\n\n` +
                    `Each one will lose notes, vault files, indexes, and Firefly data.\n` +
                    `The default knowledge base is kept (use Empty on it separately).`,
            )
        ) {
            return;
        }
        setDeletingId("__all__");
        setError(null);
        try {
            const result = await api.deleteAllNonDefaultKBs();
            if (currentKB !== "default") setCurrentKB("default");
            await fetchKBs();
            if (result.errors?.length) {
                setError(
                    `Deleted ${result.removed_count}; ${result.errors.length} failed.`,
                );
            }
        } catch {
            setError("Failed to delete non-default knowledge bases.");
        } finally {
            setDeletingId(null);
        }
    }

    function handleSelect(kb: KnowledgeBase) {
        const slug = kb.slug ?? kb.name.toLowerCase().replace(/\s+/g, "_");
        setCurrentKB(kb.id === "default" ? "default" : slug, kb.name);
    }

    function isActive(kb: KnowledgeBase): boolean {
        if (kb.id === "default") return currentKB === "default";
        const slug = kb.slug ?? kb.name.toLowerCase().replace(/\s+/g, "_");
        return currentKB === slug || currentKB === kb.name;
    }

    return (
        <div className="relative min-h-screen bg-black text-white">
            <ShaderBackground />

            <div className="relative z-10 max-w-3xl mx-auto px-6 py-16">
                {/* Header */}
                <motion.div
                    initial={{ opacity: 0, y: -10 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="mb-10"
                >
                    <div className="flex items-center gap-3 mb-2">
                        <Database className="h-7 w-7 text-purple-400" />
                        <h1 className="text-3xl font-bold bg-gradient-to-r from-white to-white/60 bg-clip-text text-transparent">
                            Knowledge Bases
                        </h1>
                    </div>
                    <p className="text-white/50 text-sm">
                        Each knowledge base has its own notes vault folder, graph, and search
                        index. Every KB follows the chat model from Settings unless you pin a
                        different one below; embedding, reranking, and media models from Setup
                        are always shared.
                    </p>
                </motion.div>

                {/* Error banner */}
                <AnimatePresence>
                    {error && (
                        <motion.div
                            initial={{ opacity: 0, y: -4 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0 }}
                            className="mb-6 flex items-center gap-3 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300"
                        >
                            <span className="flex-1">{error}</span>
                            <button onClick={() => setError(null)}>
                                <X className="h-4 w-4" />
                            </button>
                        </motion.div>
                    )}
                </AnimatePresence>

                {/* Create form */}
                <AnimatePresence>
                    {showForm && (
                        <motion.form
                            initial={{ opacity: 0, y: -8 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -8 }}
                            onSubmit={handleCreate}
                            className="mb-6 space-y-3 rounded-2xl border border-white/10 bg-white/[0.03] p-4"
                        >
                            <div>
                                <label className="text-xs text-white/45">Name</label>
                                <input
                                    autoFocus
                                    type="text"
                                    placeholder="e.g. Work, Personal, Research"
                                    value={newName}
                                    onChange={(e) => setNewName(e.target.value)}
                                    className="mt-1.5 w-full rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 text-sm text-white placeholder-white/30 outline-none focus:border-purple-500/50 focus:ring-1 focus:ring-purple-500/30"
                                />
                            </div>
                            <div>
                                <label className="text-xs text-white/45">
                                    Notes vault folder
                                </label>
                                <p className="mt-1 text-xs text-white/35">
                                    Where markdown notes for this KB are saved. Use a different
                                    folder from your other vaults (e.g. a separate OneDrive or
                                    Documents path).
                                </p>
                                <div className="mt-1.5 flex gap-2">
                                    <input
                                        type="text"
                                        required
                                        placeholder="/path/to/this-vault/notes"
                                        value={newVaultPath}
                                        onChange={(e) => setNewVaultPath(e.target.value)}
                                        className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 font-mono text-sm text-white placeholder-white/30 outline-none focus:border-purple-500/50"
                                    />
                                    {canBrowse && (
                                        <button
                                            type="button"
                                            onClick={() => void browseVaultFolder()}
                                            className="shrink-0 rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm text-white/70 transition hover:border-white/25 hover:text-white"
                                        >
                                            Browse…
                                        </button>
                                    )}
                                </div>
                            </div>
                            <div className="flex justify-end gap-2 pt-1">
                                <button
                                    type="button"
                                    onClick={() => {
                                        setShowForm(false);
                                        setNewName("");
                                        setNewVaultPath("");
                                    }}
                                    className="rounded-xl border border-white/10 px-3 py-2.5 text-sm text-white/50 transition hover:text-white"
                                >
                                    Cancel
                                </button>
                                <button
                                    type="submit"
                                    disabled={
                                        isCreating || !newName.trim() || !newVaultPath.trim()
                                    }
                                    className="flex items-center gap-2 rounded-xl bg-purple-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-purple-500 disabled:opacity-50"
                                >
                                    {isCreating ? (
                                        <Loader2 className="h-4 w-4 animate-spin" />
                                    ) : (
                                        <Check className="h-4 w-4" />
                                    )}
                                    Create vault
                                </button>
                            </div>
                        </motion.form>
                    )}
                </AnimatePresence>

                {/* KB list */}
                {isLoading ? (
                    <div className="flex items-center justify-center py-16">
                        <Loader2 className="h-6 w-6 animate-spin text-white/30" />
                    </div>
                ) : (
                    <div className="space-y-3">
                        <AnimatePresence initial={false}>
                            {kbs.map((kb) => {
                                const active = isActive(kb);
                                const isDefault = kb.id === "default";
                                const isDeleting = deletingId === kb.id;

                                return (
                                    <motion.div
                                        key={kb.id}
                                        initial={{ opacity: 0, y: 6 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        exit={{ opacity: 0, scale: 0.97 }}
                                        className={cn(
                                            "group relative flex items-center gap-4 rounded-2xl border p-4 transition-all duration-200",
                                            active
                                                ? "border-purple-500/40 bg-purple-500/10 shadow-lg shadow-purple-500/10"
                                                : "border-white/10 bg-white/5 hover:border-white/20 hover:bg-white/8"
                                        )}
                                    >
                                        {/* Icon */}
                                        <div
                                            className={cn(
                                                "flex h-11 w-11 items-center justify-center rounded-xl shrink-0",
                                                active
                                                    ? "bg-purple-500/20 text-purple-400"
                                                    : "bg-white/5 text-white/40"
                                            )}
                                        >
                                            {isDefault ? (
                                                <Database className="h-5 w-5" />
                                            ) : (
                                                <FolderOpen className="h-5 w-5" />
                                            )}
                                        </div>

                                        {/* Info */}
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2">
                                                {renamingId === kb.id ? (
                                                    <form
                                                        onSubmit={(e) => { e.preventDefault(); handleRename(kb); }}
                                                        className="flex items-center gap-2 flex-1"
                                                    >
                                                        <input
                                                            autoFocus
                                                            type="text"
                                                            value={renameValue}
                                                            onChange={(e) => setRenameValue(e.target.value)}
                                                            onBlur={() => handleRename(kb)}
                                                            onKeyDown={(e) => e.key === "Escape" && setRenamingId(null)}
                                                            className="flex-1 min-w-0 rounded-lg border border-purple-500/40 bg-white/5 px-2 py-0.5 text-sm text-white outline-none focus:ring-1 focus:ring-purple-500/40"
                                                        />
                                                        <button
                                                            type="submit"
                                                            disabled={isRenaming}
                                                            className="shrink-0 text-purple-400 hover:text-purple-300"
                                                        >
                                                            {isRenaming ? (
                                                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                            ) : (
                                                                <Check className="h-3.5 w-3.5" />
                                                            )}
                                                        </button>
                                                        <button
                                                            type="button"
                                                            onClick={() => setRenamingId(null)}
                                                            className="shrink-0 text-white/30 hover:text-white/60"
                                                        >
                                                            <X className="h-3.5 w-3.5" />
                                                        </button>
                                                    </form>
                                                ) : (
                                                    <>
                                                        <span className="font-semibold text-white truncate">
                                                            {kb.name}
                                                        </span>
                                                        {isDefault && (
                                                            <span className="shrink-0 rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-white/40">
                                                                built-in
                                                            </span>
                                                        )}
                                                        {active && (
                                                            <span className="shrink-0 flex items-center gap-1 rounded-full bg-purple-500/20 px-2 py-0.5 text-[10px] font-medium text-purple-300">
                                                                <Check className="h-2.5 w-2.5" />
                                                                active
                                                            </span>
                                                        )}
                                                    </>
                                                )}
                                            </div>
                                            {kb.created_at && (
                                                <p className="text-xs text-white/30 mt-0.5">
                                                    Created{" "}
                                                    {new Date(kb.created_at).toLocaleDateString(undefined, {
                                                        year: "numeric",
                                                        month: "short",
                                                        day: "numeric",
                                                    })}
                                                </p>
                                            )}
                                            {kb.vault_path && (
                                                <p className="truncate font-mono text-[10px] text-white/25 mt-0.5" title={kb.vault_path}>
                                                    {kb.vault_path}
                                                </p>
                                            )}
                                            {isDefault && (
                                                <p className="text-xs text-white/30 mt-0.5">
                                                    Original knowledge base — always available
                                                </p>
                                            )}
                                            <KBModelPanel kb={kb} onSaved={fetchKBs} onError={setError} />
                                        </div>

                                        {/* Actions */}
                                        <div className="flex items-center gap-2 shrink-0">
                                            {!active && (
                                                <button
                                                    onClick={() => handleSelect(kb)}
                                                    className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs font-medium text-white/70 transition hover:border-purple-500/40 hover:bg-purple-500/10 hover:text-purple-300"
                                                >
                                                    Switch
                                                </button>
                                            )}
                                            <button
                                                onClick={() => void handleEmpty(kb)}
                                                disabled={isDeleting || deletingId === "__all__"}
                                                className="rounded-lg border border-amber-500/25 bg-amber-500/5 px-2 py-1.5 text-xs text-amber-300/80 transition hover:border-amber-500/40 hover:bg-amber-500/10 disabled:opacity-40"
                                                title={`Empty "${kb.name}" (keep KB)`}
                                            >
                                                {isDeleting ? (
                                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                ) : (
                                                    "Empty"
                                                )}
                                            </button>
                                            {!isDefault && renamingId !== kb.id && (
                                                <button
                                                    onClick={() => startRename(kb)}
                                                    className="rounded-lg border border-white/10 bg-white/5 px-2 py-1.5 text-xs text-white/40 transition hover:border-white/20 hover:text-white/70"
                                                    title={`Rename "${kb.name}"`}
                                                >
                                                    <Pencil className="h-3.5 w-3.5" />
                                                </button>
                                            )}
                                            {!isDefault && (
                                                <button
                                                    onClick={() => handleDelete(kb)}
                                                    disabled={isDeleting || deletingId === "__all__"}
                                                    className="rounded-lg border border-red-500/20 bg-red-500/5 px-2 py-1.5 text-xs text-red-400/70 transition hover:border-red-500/40 hover:bg-red-500/10 hover:text-red-300 disabled:opacity-40"
                                                    title={`Delete "${kb.name}"`}
                                                >
                                                    {isDeleting ? (
                                                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                    ) : (
                                                        <Trash2 className="h-3.5 w-3.5" />
                                                    )}
                                                </button>
                                            )}
                                        </div>
                                    </motion.div>
                                );
                            })}
                        </AnimatePresence>
                    </div>
                )}

                {/* Create button */}
                {!showForm && (
                    <motion.button
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        onClick={() => setShowForm(true)}
                        className="mt-6 flex w-full items-center justify-center gap-2 rounded-2xl border border-dashed border-white/15 bg-white/2 py-4 text-sm text-white/40 transition hover:border-purple-500/30 hover:bg-purple-500/5 hover:text-purple-300"
                    >
                        <Plus className="h-4 w-4" />
                        New knowledge base
                    </motion.button>
                )}

                {kbs.some((k) => k.id !== "default") && (
                    <button
                        type="button"
                        disabled={deletingId === "__all__"}
                        onClick={() => void handleDeleteAllNonDefault()}
                        className="mt-3 flex w-full items-center justify-center gap-2 rounded-2xl border border-red-500/20 bg-red-500/5 py-3 text-sm text-red-300/80 transition hover:border-red-500/40 hover:bg-red-500/10 disabled:opacity-50"
                    >
                        {deletingId === "__all__" ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                            <Trash2 className="h-4 w-4" />
                        )}
                        Delete all non-default knowledge bases
                    </button>
                )}

                {/* Usage hint */}
                <p className="mt-8 text-center text-xs text-white/20">
                    The active KB is used for chat, ingestion, and graph exploration.
                    Switch any time — existing data is never moved.
                </p>
            </div>
        </div>
    );
}
