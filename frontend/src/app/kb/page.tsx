"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { Cpu, Loader2, Plus, Wallet } from "lucide-react";
import { api } from "@/lib/api";
import { kbSlug, useKB } from "@/lib/kb-context";
import { cn } from "@/lib/utils";
import { SettingRow, SettingsShell, Toggle } from "@/components/settings-shell";
import { getDesktopBridge, pickDesktopDirectory } from "@/lib/desktop";
import type { KnowledgeBase } from "@/lib/types";

const SWATCHES = ["bg-accent-700", "bg-accent-800", "bg-n-700", "bg-accent-600"];

function modelSummary(kb: KnowledgeBase): string {
    const eff = kb.effective_llm;
    if (!eff) return "not set";
    let where = "on this device";
    if (eff.provider === "openai_compat") {
        try {
            where = eff.base_url ? new URL(eff.base_url).host : "no endpoint";
        } catch {
            where = eff.base_url || "no endpoint";
        }
    }
    return `${eff.inherited ? "inherits" : "pinned"} · ${where} · ${eff.model ?? "not set"}`;
}

function errText(err: unknown, fallback: string): string {
    if (err && typeof err === "object" && "response" in err) {
        const detail = (err as { response?: { data?: { detail?: string } } }).response?.data?.detail;
        if (detail) return detail;
    }
    return err instanceof Error && err.message ? err.message : fallback;
}

export default function KBPage() {
    const { currentKB, currentKBRecord, kbs, refreshKBs, setCurrentKB, setCurrentKBName } = useKB();
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [name, setName] = useState("");
    const [syncedName, setSyncedName] = useState<string | undefined>(undefined);
    const [showForm, setShowForm] = useState(
        () => typeof window !== "undefined" && new URLSearchParams(window.location.search).get("new") === "1",
    );
    const [newName, setNewName] = useState("");
    const [newVaultPath, setNewVaultPath] = useState("");
    const canBrowse = Boolean(getDesktopBridge()?.pickDirectory);

    const current =
        currentKBRecord ?? kbs.find((k) => kbSlug(k) === currentKB || k.name === currentKB) ?? null;
    const isDefault = current?.id === "default";

    // Re-seed the draft when the record's name changes (KB switch / rename).
    if (syncedName !== current?.name) {
        setSyncedName(current?.name);
        setName(current?.name ?? "");
    }

    useEffect(() => {
        if (window.location.search.includes("new=1")) window.history.replaceState({}, "", "/kb");
    }, []);

    async function run(key: string, fn: () => Promise<void>, fallback: string) {
        setBusy(key);
        setError(null);
        try {
            await fn();
            await refreshKBs();
        } catch (err) {
            setError(errText(err, fallback));
            await refreshKBs();
        } finally {
            setBusy(null);
        }
    }

    async function rename() {
        const next = name.trim();
        if (!current || !next || next === current.name) {
            setName(current?.name ?? "");
            return;
        }
        await run("rename", async () => {
            await api.renameKB(current.id, next);
            setCurrentKBName(next);
        }, `Failed to rename "${current.name}".`);
    }

    async function handleCreate(e: FormEvent) {
        e.preventDefault();
        const n = newName.trim();
        const vault = newVaultPath.trim();
        if (!n) return;
        if (!vault) {
            setError("Choose a notes vault folder — where markdown files for this workspace will be saved.");
            return;
        }
        await run("create", async () => {
            await api.createKB(n, vault);
            setNewName("");
            setNewVaultPath("");
            setShowForm(false);
        }, "Failed to create workspace");
    }

    async function handleEmpty(kb: KnowledgeBase) {
        if (!window.confirm(`Empty "${kb.name}"?\n\nRemoves all notes, vault files, indexes and finance data. The workspace itself stays.\n\nVault: ${kb.vault_path || "(no path)"}`)) return;
        await run(`empty-${kb.id}`, async () => {
            await api.emptyKB(kbSlug(kb));
        }, `Failed to empty "${kb.name}".`);
    }

    async function handleDelete(kb: KnowledgeBase) {
        if (!window.confirm(`Permanently delete "${kb.name}"?\n\nRemoves all notes and vault files (${kb.vault_path || "(no path)"}), every index, and finance data. This cannot be undone.`)) return;
        await run(`delete-${kb.id}`, async () => {
            await api.deleteKB(kb.id);
            if (currentKB === kb.name || currentKB === kbSlug(kb)) setCurrentKB("default");
        }, `Failed to delete "${kb.name}".`);
    }

    async function handleDeleteAll() {
        const extras = kbs.filter((k) => k.id !== "default");
        if (!window.confirm(`Delete all ${extras.length} non-default workspace(s)?\n\nEach loses notes, vault files, indexes and finance data. The default workspace is kept.`)) return;
        await run("delete-all", async () => {
            const result = await api.deleteAllNonDefaultKBs();
            if (currentKB !== "default") setCurrentKB("default");
            if (result.errors?.length) {
                throw new Error(`Deleted ${result.removed_count}; ${result.errors.length} failed.`);
            }
        }, "Failed to delete non-default workspaces.");
    }

    return (
        <SettingsShell
            title="Workspace"
            intro="Each workspace is one vault folder with its own graph and index. Switch from the sidebar."
        >
            {error && (
                <div className="card mb-4 flex items-center gap-3 text-[12.5px] text-danger-text">
                    <span className="flex-1">{error}</span>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => setError(null)}>
                        Dismiss
                    </button>
                </div>
            )}

            {current ? (
                <div className="max-w-[560px] space-y-3.5">
                    <div className="field">
                        <label>Name</label>
                        <input
                            className="input max-w-[360px]"
                            value={name}
                            disabled={busy === "rename"}
                            onChange={(e) => setName(e.target.value)}
                            onBlur={() => void rename()}
                            onKeyDown={(e) => {
                                if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                                if (e.key === "Escape") setName(current.name);
                            }}
                        />
                    </div>
                    <div className="field">
                        <label>Vault folder — the Markdown files Orb reads and writes</label>
                        <div className="flex gap-2">
                            <input className="input input-mono" value={current.vault_path || ""} readOnly />
                            <button type="button" className="btn btn-secondary" disabled title="Vault paths are fixed after creation">
                                Choose…
                            </button>
                        </div>
                    </div>
                    <SettingRow
                        icon={<Wallet className="h-[18px] w-[18px]" />}
                        title="Finance"
                        description="Adds the Finance surface for this workspace. Turning it off hides it; nothing is deleted."
                    >
                        <Toggle
                            label="Finance"
                            on={current.finance_enabled !== false}
                            disabled={busy === "finance"}
                            onChange={(next) =>
                                void run("finance", async () => {
                                    await api.setKBFinance(current.id, next);
                                }, `Failed to turn finance ${next ? "on" : "off"}.`)
                            }
                        />
                    </SettingRow>
                    <SettingRow icon={<Cpu className="h-[18px] w-[18px]" />} title="Model" description={modelSummary(current)}>
                        <Link href="/models" className="btn btn-secondary btn-sm no-underline">
                            Change
                        </Link>
                    </SettingRow>
                    <div className="flex gap-2 pt-3">
                        <button type="button" className="btn btn-secondary" disabled={busy !== null} onClick={() => void handleEmpty(current)}>
                            {busy === `empty-${current.id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                            Empty workspace…
                        </button>
                        {!isDefault && (
                            <button type="button" className="btn btn-danger" disabled={busy !== null} onClick={() => void handleDelete(current)}>
                                Delete workspace…
                            </button>
                        )}
                    </div>
                </div>
            ) : (
                <div className="flex items-center gap-2 text-[12.5px] text-n-500">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading…
                </div>
            )}

            <div className="kicker mb-2 mt-7">All workspaces</div>
            <div className="max-w-[560px] space-y-2">
                {kbs.map((kb, i) => {
                    const active = current?.id === kb.id;
                    return (
                        <div key={kb.id} className="flex items-center gap-3 rounded-md px-3.5 py-2.5 shadow-sm">
                            <span
                                className={cn(
                                    "grid h-[22px] w-[22px] shrink-0 place-items-center rounded-[6px] text-[11px] font-medium text-accent-100",
                                    SWATCHES[i % SWATCHES.length],
                                )}
                            >
                                {kb.name.slice(0, 1).toUpperCase()}
                            </span>
                            <div className="min-w-0 flex-1">
                                <div className="flex items-center gap-2 text-[13px]">
                                    <span className="truncate">{kb.name}</span>
                                    {kb.id === "default" && <span className="tag tag-neutral">built-in</span>}
                                    {active && <span className="tag tag-accent">active</span>}
                                </div>
                                <div className="truncate font-mono text-[11px] text-n-500" title={kb.vault_path}>
                                    {kb.vault_path || "—"}
                                </div>
                            </div>
                            {!active && (
                                <button type="button" className="btn btn-secondary btn-sm" onClick={() => setCurrentKB(kbSlug(kb), kb.name)}>
                                    Switch
                                </button>
                            )}
                        </div>
                    );
                })}

                {showForm ? (
                    <form onSubmit={handleCreate} className="card-outline space-y-3">
                        <div className="field">
                            <label>Name</label>
                            <input
                                autoFocus
                                className="input"
                                placeholder="e.g. Work, Personal, Research"
                                value={newName}
                                onChange={(e) => setNewName(e.target.value)}
                            />
                        </div>
                        <div className="field">
                            <label>Vault folder — a folder of Markdown files, separate from your other vaults</label>
                            <div className="flex gap-2">
                                <input
                                    required
                                    className="input input-mono"
                                    placeholder="/path/to/this-vault/notes"
                                    value={newVaultPath}
                                    onChange={(e) => setNewVaultPath(e.target.value)}
                                />
                                {canBrowse && (
                                    <button
                                        type="button"
                                        className="btn btn-secondary"
                                        onClick={() =>
                                            void pickDesktopDirectory({
                                                title: "Choose notes vault folder for this workspace",
                                                defaultPath: newVaultPath || undefined,
                                            }).then((dir) => dir && setNewVaultPath(dir))
                                        }
                                    >
                                        Browse…
                                    </button>
                                )}
                            </div>
                        </div>
                        <div className="flex justify-end gap-2">
                            <button
                                type="button"
                                className="btn btn-secondary"
                                onClick={() => {
                                    setShowForm(false);
                                    setNewName("");
                                    setNewVaultPath("");
                                }}
                            >
                                Cancel
                            </button>
                            <button type="submit" className="btn btn-primary" disabled={busy === "create" || !newName.trim() || !newVaultPath.trim()}>
                                {busy === "create" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                                Create
                            </button>
                        </div>
                    </form>
                ) : (
                    <button
                        type="button"
                        onClick={() => setShowForm(true)}
                        className="flex w-full items-center justify-center gap-2 rounded-md border border-dashed border-n-700 py-3 text-[12.5px] text-n-400 hover:border-accent hover:text-accent"
                    >
                        <Plus className="h-3.5 w-3.5" /> New workspace
                    </button>
                )}

                {kbs.some((k) => k.id !== "default") && (
                    <div className="pt-2">
                        <button type="button" className="btn btn-danger btn-sm" disabled={busy !== null} onClick={() => void handleDeleteAll()}>
                            {busy === "delete-all" ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
                            Delete all non-default workspaces
                        </button>
                    </div>
                )}
            </div>
        </SettingsShell>
    );
}
