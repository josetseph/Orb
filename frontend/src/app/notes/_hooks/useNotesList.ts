import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import { api, isRequestCancelled } from "@/lib/api";
import { notifyIfUnfocused } from "@/lib/desktop";
import { useDebounced } from "@/lib/utils";
import type { Note } from "@/lib/types";
import { isActiveProcessingNote } from "../_lib/processing-status";
import type { ProcessedFilter, VaultFileEntry } from "../_lib/types";

export type VaultListing = {
  folders: string[];
  attachments: VaultFileEntry[];
  vault_name?: string;
};

type UseNotesListArgs = {
  currentKB: string;
  syncSelectedNoteFromList: (data: Note[]) => void;
  setIngestingNoteIds: Dispatch<SetStateAction<Set<string>>>;
  onVaultListing: (listing: VaultListing) => void;
  clearSelectionForKBSwitch: () => void;
};

export function useNotesList({
  currentKB,
  syncSelectedNoteFromList,
  setIngestingNoteIds,
  onVaultListing,
  clearSelectionForKBSwitch,
}: UseNotesListArgs) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [processedFilter, setProcessedFilter] =
    useState<ProcessedFilter>("all");
  const [isLoading, setIsLoading] = useState(true);
  const fetchNotesRequestRef = useRef(0);
  const fetchAbortRef = useRef<AbortController | null>(null);

  const [loadedKb, setLoadedKb] = useState(currentKB);
  if (loadedKb !== currentKB) {
    setLoadedKb(currentKB);
    setIsLoading(true);
    clearSelectionForKBSwitch();
  }

  const fetchNotes = useCallback(
    async (search?: string, filter?: ProcessedFilter) => {
      const requestId = ++fetchNotesRequestRef.current;
      // Abort the superseded request so the backend stops scanning the vault
      // for a response that will be discarded anyway.
      fetchAbortRef.current?.abort();
      const controller = new AbortController();
      fetchAbortRef.current = controller;
      try {
        setIsLoading(true);
        let processed: boolean | undefined;
        let failed: boolean | undefined;
        if (filter === "needs") {
          processed = false;
        } else if (filter === "ingested") {
          processed = true;
        } else if (filter === "saved") {
          processed = false;
          failed = false;
        } else if (filter === "failed") {
          failed = true;
        } else if (filter === "ingesting") {
          // fetch unprocessed+non-failed candidates; client-side filtered below
          processed = false;
          failed = false;
        }
        // "all" → no filters
        const data = await api.getNotes(search, processed, failed, currentKB, {
          signal: controller.signal,
        });
        if (requestId !== fetchNotesRequestRef.current) return;
        setNotes(data);
        syncSelectedNoteFromList(data);
        try {
          const folderRes = await api.listVaultFolders(currentKB);
          if (requestId === fetchNotesRequestRef.current) {
            onVaultListing({
              folders: folderRes.folders || [],
              attachments: folderRes.attachments || [],
              vault_name: folderRes.vault_name,
            });
          }
        } catch {
          /* folders optional */
        }
        // Poll every note the server says is mid-pipeline. The set lives in page
        // state, so it is empty after navigating away and back while an ingest
        // runs; rebuilding it from the list is what keeps the badge moving.
        // isActiveProcessingNote ignores autosave / vault-watcher markers.
        setIngestingNoteIds((prev) => {
          for (const id of prev) {
            const note = data.find((n: Note) => n.id === id);
            if (note && !isActiveProcessingNote(note)) {
              notifyIfUnfocused(note.failed ? "Ingestion failed" : "Note ingested", note.title || "Untitled");
            }
          }
          return new Set(data.filter(isActiveProcessingNote).map((n: Note) => n.id));
        });
      } catch (error) {
        if (!isRequestCancelled(error)) {
          console.error("Error fetching notes:", error);
        }
      } finally {
        if (requestId === fetchNotesRequestRef.current) {
          setIsLoading(false);
        }
      }
    },
    [
      currentKB,
      syncSelectedNoteFromList,
      onVaultListing,
      setIngestingNoteIds,
    ],
  );

  // Fetch notes on mount and re-fetch on KB switch. isLoading is already true
  // here (initial state / the KB block above); every other setState in
  // fetchNotes runs after an await.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- only async setState reaches here
    void fetchNotes(undefined, processedFilter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentKB]);

  // Search (debounced while typing) and filter. Skips its first run: the KB
  // effect above already fetched, so running here too would double the
  // initial notes fetch.
  const debouncedSearch = useDebounced(searchQuery, 300);
  const searchEffectRanRef = useRef(false);
  useEffect(() => {
    if (!searchEffectRanRef.current) {
      searchEffectRanRef.current = true;
      return;
    }
    void fetchNotes(debouncedSearch, processedFilter);
    // fetchNotes reads currentKB; KB changes are handled by the dedicated effect above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch, processedFilter]);

  return {
    notes,
    setNotes,
    searchQuery,
    setSearchQuery,
    processedFilter,
    setProcessedFilter,
    isLoading,
    fetchNotes,
  };
}
