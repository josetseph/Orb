"use client";

import { useEffect, useState } from "react";
import { X, Loader2, ExternalLink } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { api } from "@/lib/api";

interface EntityConnection {
  node_id: string;
  name: string;
  kind?: string;
  relationship?: string;
  direction?: string;
}

interface EntityDetail {
  node_id: string;
  name: string;
  node_type: string;
  description: string;
  isolated_contexts: string[];
  facts: string[];
  domain?: string;
  status?: string;
  community_id?: string;
  community_name?: string;
  connections?: EntityConnection[];
  related_notes?: { note_id: string; name: string }[];
}

interface EntityDetailPanelProps {
  nodeId: string | null;
  name?: string;
  kb?: string;
  onClose: () => void;
}

export function EntityDetailPanel({
  nodeId,
  name,
  kb = "default",
  onClose,
}: EntityDetailPanelProps) {
  const [detail, setDetail] = useState<EntityDetail | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [prevNodeId, setPrevNodeId] = useState<string | null>(nodeId);

  if (prevNodeId !== nodeId) {
    setPrevNodeId(nodeId);
    setDetail(null);
    setIsLoading(Boolean(nodeId));
  }

  useEffect(() => {
    if (!nodeId) {
      setDetail(null);
      return;
    }
    if (!nodeId) return;
    let cancelled = false;
    setIsLoading(true);
    setDetail(null);
    api
      .getNodeDetail(nodeId, kb)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch(() => {
        // Panel silently handles not-found nodes
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [nodeId, kb]);

  const hasBody =
    Boolean(detail?.description) ||
    Boolean(detail?.isolated_contexts?.length) ||
    Boolean(detail?.facts?.length) ||
    Boolean(detail?.domain) ||
    Boolean(detail?.community_id) ||
    Boolean(detail?.connections?.length) ||
    Boolean(detail?.related_notes?.length);

  return (
    <AnimatePresence>
      {nodeId && (
        <motion.div
          initial={{ opacity: 0, x: 20 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: 20 }}
          transition={{ duration: 0.2, ease: "easeOut" }}
          className="absolute right-0 top-0 bottom-0 z-40 flex w-80 flex-col border-l border-white/10 bg-black/90 backdrop-blur-xl shadow-2xl"
        >
          {/* Header */}
          <div className="flex items-start justify-between gap-2 border-b border-white/10 px-4 py-3">
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold uppercase tracking-widest text-blue-400/80 mb-0.5">
                Entity
              </p>
              <h3 className="truncate text-base font-bold text-white">
                {detail?.name ?? name ?? "…"}
              </h3>
              {detail?.node_type && (
                <span className="mt-1 inline-block rounded bg-blue-500/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-blue-300 border border-blue-500/20">
                  {detail.node_type}
                </span>
              )}
            </div>
            <button
              onClick={onClose}
              className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-white/5 text-white/40 transition-colors hover:bg-white/10 hover:text-white"
              aria-label="Close entity panel"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
            {isLoading && (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-5 w-5 animate-spin text-white/30" />
              </div>
            )}

            {!isLoading && detail && (
              <>
                {detail.description && (
                  <div>
                    <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-white/40">
                      Description
                    </p>
                    <p className="text-sm leading-relaxed text-white/80">
                      {detail.description}
                    </p>
                  </div>
                )}

                {detail.isolated_contexts &&
                  detail.isolated_contexts.length > 0 && (
                    <div>
                      <p className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-white/40">
                        Contexts
                      </p>
                      <div className="space-y-2">
                        {detail.isolated_contexts.slice(0, 6).map((ctx, i) => (
                          <div
                            key={i}
                            className="rounded-lg border border-white/8 bg-white/4 px-3 py-2 text-xs text-white/70 leading-relaxed"
                          >
                            {ctx}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                {detail.facts && detail.facts.length > 0 && (
                  <div>
                    <p className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-white/40">
                      Facts
                    </p>
                    <ul className="space-y-1.5">
                      {detail.facts.map((fact, i) => (
                        <li
                          key={i}
                          className="flex gap-2 text-xs text-white/70 leading-relaxed"
                        >
                          <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-blue-400/60" />
                          {fact}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {detail.domain && (
                  <div>
                    <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-white/40">
                      Domain
                    </p>
                    <p className="text-sm text-white/70">{detail.domain}</p>
                  </div>
                )}

                {(detail.community_name || detail.community_id) && (
                  <div>
                    <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-white/40">
                      Community
                    </p>
                    <p className="text-sm text-white/70">
                      {detail.community_name || detail.community_id}
                    </p>
                  </div>
                )}

                {detail.connections && detail.connections.length > 0 && (
                  <div>
                    <p className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-white/40">
                      Connections
                    </p>
                    <ul className="space-y-1.5">
                      {detail.connections.slice(0, 10).map((conn) => (
                        <li
                          key={conn.node_id}
                          className="rounded-lg border border-white/8 bg-white/4 px-3 py-2 text-xs text-white/75"
                        >
                          <span className="font-medium text-white/90">
                            {conn.name}
                          </span>
                          {conn.relationship && (
                            <span className="mt-0.5 block text-[10px] uppercase tracking-wide text-white/35">
                              {conn.direction === "incoming" ? "← " : "→ "}
                              {conn.relationship}
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {detail.related_notes && detail.related_notes.length > 0 && (
                  <div>
                    <p className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-white/40">
                      Mentioned in notes
                    </p>
                    <ul className="space-y-1.5">
                      {detail.related_notes.map((note) => (
                        <li
                          key={note.note_id}
                          className="text-xs text-teal-200/80 truncate"
                        >
                          {note.name}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {!hasBody && (
                  <p className="py-6 text-center text-sm text-white/35 leading-relaxed">
                    No stored contexts yet for this entity. Re-ingest the note
                    if ingestion just finished, or wait for background indexing
                    to catch up.
                  </p>
                )}
              </>
            )}

            {!isLoading && !detail && (
              <p className="py-8 text-center text-sm text-white/30">
                Entity details unavailable
              </p>
            )}
          </div>

          {/* Footer: link to graph */}
          {detail && (
            <div className="border-t border-white/10 px-4 py-3">
              <a
                href="/graph-3d"
                className="flex items-center gap-2 text-xs text-white/40 transition-colors hover:text-white/70"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                View in graph explorer
              </a>
            </div>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
