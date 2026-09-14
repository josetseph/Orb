"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { SettingsShell } from "@/components/settings-shell";
import type { SetupStatus } from "@/lib/types";

const STACK: Array<[string, string]> = [
    ["Relational", "SQLite"],
    ["Graph", "Kuzu"],
    ["Vectors", "Qdrant"],
    ["Search", "Meilisearch"],
    ["Network", "No outbound connections unless a cloud endpoint is added"],
];

const GUIDE: Array<[string, string]> = [
    ["Reset indexes", "Graph, vectors and keyword index are cleared; your .md files stay and notes are marked unprocessed. Lives in Storage."],
    ["Empty / Delete workspace", "Always removes notes, vault files, indexes and finance data for that workspace. Delete also unregisters it (not allowed for the built-in one — use Empty). Lives in Workspace."],
    ["Where data lives", "The data folder holds SQLite, Qdrant, Meilisearch, Kuzu, Firefly, binaries and logs."],
    ["Where models live", "GGUF chat / embed / rerank files, the chat model's vision projector, plus Whisper and Marlin snapshots."],
    ["How to delete models", "Quit Orb, delete files inside the models folder (or the whole folder), relaunch, then re-download from Models. Never delete models while Orb runs — they may be memory-mapped."],
    ["Wipe all app data", "Quit Orb, delete the entire data folder, relaunch. Models are untouched unless you also clear the models folder."],
    ["paths.json", "Records the data and models folders chosen in the first-run wizard."],
];

export default function SettingsPage() {
    const [paths, setPaths] = useState<SetupStatus | null>(null);

    useEffect(() => {
        api.getSetupStatus().then(setPaths).catch(() => {});
    }, []);

    const rows: Array<[string, React.ReactNode]> = [
        ...STACK,
        ...(paths
            ? ([
                  ["Data folder", <span key="d" className="font-mono text-[11px] break-all">{paths.data_dir}</span>],
                  ["Models folder", <span key="m" className="font-mono text-[11px] break-all">{paths.models_dir}</span>],
                  ...(paths.paths_json
                      ? [["paths.json", <span key="p" className="font-mono text-[11px] break-all">{paths.paths_json}</span>] as [string, React.ReactNode]]
                      : []),
              ] as Array<[string, React.ReactNode]>)
            : []),
    ];

    return (
        <SettingsShell title="About" intro="Orb · a local, graph-based memory for your notes.">
            <div className="grid max-w-[560px] grid-cols-[140px_1fr] gap-x-4 gap-y-2 text-[12.5px]">
                {rows.map(([k, v]) => (
                    <div key={k} className="contents">
                        <span className="text-n-500">{k}</span>
                        <span>{v}</span>
                    </div>
                ))}
            </div>

            <div className="kicker mb-2 mt-7">Data guide</div>
            <div className="max-w-[560px] space-y-3">
                {GUIDE.map(([title, body]) => (
                    <div key={title}>
                        <div className="text-[13px]">{title}</div>
                        <div className="text-[12px] text-n-500">{body}</div>
                    </div>
                ))}
            </div>
        </SettingsShell>
    );
}
