import {
    createContext,
    useContext,
    useState,
    useCallback,
    useEffect,
    type ReactNode,
} from "react";
import { api } from "@/lib/api";
import type { KnowledgeBase } from "@/lib/types";
import { saveJson } from "@/lib/utils";

const STORAGE_KEY = "orb_current_kb";

interface StoredKB {
    slug: string;
    name: string;
}

interface KBContextValue {
    /** Slug of the active KB — used as the `?kb=` query param. */
    currentKB: string;
    /** Human-readable display name of the active KB. */
    currentKBName: string;
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

const KBContext = createContext<KBContextValue | null>(null);

/** Slug the app stores for a workspace record. */
export function kbSlug(kb: KnowledgeBase): string {
    if (kb.id === "default") return "default";
    return kb.slug;
}

function readStorage(): StoredKB {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return { slug: "default", name: "default" };
        const parsed = JSON.parse(raw) as StoredKB;
        return { slug: parsed.slug || "default", name: parsed.name || parsed.slug || "default" };
    } catch {
        return { slug: "default", name: "default" };
    }
}

export function KBProvider({ children }: { children: ReactNode }) {
    const [current, setCurrent] = useState<StoredKB>(readStorage);
    const [kbs, setKBs] = useState<KnowledgeBase[]>([]);

    const refreshKBs = useCallback(
        () =>
            api
                .listKBs()
                .then((data) => setKBs(data.knowledge_bases))
                // Backend not up yet — the sidebar shows the stored name meanwhile.
                .catch(() => {}),
        [],
    );

    useEffect(() => {
        void refreshKBs();
    }, [refreshKBs]);

    const setCurrentKB = useCallback((slug: string, displayName?: string) => {
        const normalized = slug.trim() || "default";
        const name = displayName?.trim() || normalized;
        const kb: StoredKB = { slug: normalized, name };
        setCurrent(kb);
        saveJson(STORAGE_KEY, kb);
    }, []);

    const setCurrentKBName = useCallback((name: string) => {
        setCurrent((prev) => {
            const updated = { ...prev, name: name.trim() || prev.slug };
            saveJson(STORAGE_KEY, updated);
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
    const ctx = useContext(KBContext);
    if (!ctx) throw new Error("useKB must be used inside <KBProvider>");
    return ctx;
}
