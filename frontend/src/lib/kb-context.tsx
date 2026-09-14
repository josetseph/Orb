"use client";

import {
    createContext,
    useContext,
    useState,
    useCallback,
    useEffect,
    useSyncExternalStore,
    type ReactNode,
} from "react";
import { api } from "@/lib/api";
import type { KnowledgeBase } from "@/lib/types";

const STORAGE_KEY = "orb_current_kb";
const LEGACY_STORAGE_KEYS = ["lifeos_current_kb", "liveos_current_kb"];

interface StoredKB {
    slug: string;
    name: string;
}

interface KBContextValue {
    /** Slug of the active KB — used as the `?kb=` query param. */
    currentKB: string;
    /** Human-readable display name of the active KB. */
    currentKBName: string;
    /** True once localStorage has been read on the client. Pages should wait for this before fetching. */
    isHydrated: boolean;
    /** Every workspace on this machine; refreshed by `refreshKBs`. */
    kbs: KnowledgeBase[];
    /** The active workspace's record, when the list has loaded. */
    currentKBRecord: KnowledgeBase | null;
    refreshKBs: () => Promise<void>;
    /** Switch to a KB by slug and display name. */
    setCurrentKB: (slug: string, displayName?: string) => void;
    /** Update only the display name (e.g. after a rename). */
    setCurrentKBName: (name: string) => void;
}

const KBContext = createContext<KBContextValue>({
    currentKB: "default",
    currentKBName: "default",
    isHydrated: false,
    kbs: [],
    currentKBRecord: null,
    refreshKBs: async () => { },
    setCurrentKB: () => { },
    setCurrentKBName: () => { },
});

/** Slug the app stores for a workspace record. */
export function kbSlug(kb: KnowledgeBase): string {
    if (kb.id === "default") return "default";
    return kb.slug ?? kb.name.toLowerCase().replace(/\s+/g, "_");
}

function readStorage(): StoredKB {
    if (typeof window === "undefined") return { slug: "default", name: "default" };
    try {
        let raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) {
            for (const key of LEGACY_STORAGE_KEYS) {
                raw = localStorage.getItem(key);
                if (raw) {
                    localStorage.setItem(STORAGE_KEY, raw);
                    localStorage.removeItem(key);
                    break;
                }
            }
        }
        if (!raw) return { slug: "default", name: "default" };
        // Handle old format (plain string slug).
        if (!raw.startsWith("{")) return { slug: raw, name: raw };
        const parsed = JSON.parse(raw) as StoredKB;
        return { slug: parsed.slug || "default", name: parsed.name || parsed.slug || "default" };
    } catch {
        return { slug: "default", name: "default" };
    }
}

function writeStorage(kb: StoredKB) {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(kb));
    } catch {
        // Ignore write failures.
    }
}

const emptySubscribe = () => () => {};

export function KBProvider({ children }: { children: ReactNode }) {
    const [current, setCurrent] = useState<StoredKB>(readStorage);
    const [kbs, setKBs] = useState<KnowledgeBase[]>([]);
    const isHydrated = useSyncExternalStore(emptySubscribe, () => true, () => false);

    const refreshKBs = useCallback(async () => {
        try {
            const data = await api.listKBs();
            setKBs(data.knowledge_bases);
        } catch {
            // Backend not up yet — the sidebar shows the stored name meanwhile.
        }
    }, []);

    useEffect(() => {
        if (isHydrated) void refreshKBs();
    }, [isHydrated, refreshKBs]);

    const setCurrentKB = useCallback((slug: string, displayName?: string) => {
        const normalized = slug.trim() || "default";
        const name = displayName?.trim() || normalized;
        const kb: StoredKB = { slug: normalized, name };
        setCurrent(kb);
        writeStorage(kb);
    }, []);

    const setCurrentKBName = useCallback((name: string) => {
        setCurrent((prev) => {
            const updated = { ...prev, name: name.trim() || prev.slug };
            writeStorage(updated);
            return updated;
        });
    }, []);

    const currentKBRecord =
        kbs.find((k) => kbSlug(k) === current.slug || k.name === current.slug) ?? null;

    return (
        <KBContext.Provider
            value={{
                currentKB: current.slug,
                currentKBName: currentKBRecord?.name ?? current.name,
                isHydrated,
                kbs,
                currentKBRecord,
                refreshKBs,
                setCurrentKB,
                setCurrentKBName,
            }}
        >
            {children}
        </KBContext.Provider>
    );
}

/** Hook: returns the current KB slug, display name, and setters. */
export function useKB(): KBContextValue {
    return useContext(KBContext);
}
