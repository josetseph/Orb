"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Box,
  Building2,
  Circle,
  FileText,
  Lightbulb,
  Loader2,
  Network,
  UserCircle2,
} from "lucide-react";
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

function TypeIcon({ type }: { type?: string }) {
  const cls = "h-[18px] w-[18px] text-accent-100";
  switch ((type || "").toLowerCase()) {
    case "person":
      return <UserCircle2 className={cls} />;
    case "organization":
      return <Building2 className={cls} />;
    case "concept":
      return <Lightbulb className={cls} />;
    case "tool":
      return <Box className={cls} />;
    default:
      return <Circle className={cls} />;
  }
}

/**
 * Entity view for a side column. Renders inline — the host (notes right
 * panel, chat aside) decides where it sits. Nothing when `nodeId` is null.
 */
export function EntityDetailPanel({
  nodeId,
  name,
  kb = "default",
  onClose,
}: EntityDetailPanelProps) {
  const [detail, setDetail] = useState<EntityDetail | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (!nodeId) return;
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
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

  if (!nodeId) return null;

  const hasBody =
    Boolean(detail?.description) ||
    Boolean(detail?.isolated_contexts?.length) ||
    Boolean(detail?.facts?.length) ||
    Boolean(detail?.connections?.length) ||
    Boolean(detail?.related_notes?.length);

  return (
    <div className="flex h-full min-h-0 flex-col animate-rise">
      <div className="flex items-center gap-2 px-3 pb-2 pt-3">
        <button
          type="button"
          onClick={onClose}
          className="btn btn-ghost btn-icon h-[26px] w-[26px]"
          aria-label="Close entity"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
        </button>
        <span className="kicker text-accent">Entity</span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
        <div className="mb-1.5 flex items-start gap-2.5">
          <span className="grid h-[34px] w-[34px] shrink-0 place-items-center rounded-[9px] bg-accent-700">
            <TypeIcon type={detail?.node_type} />
          </span>
          <div className="min-w-0">
            <h5 className="truncate text-[17px] font-medium">{detail?.name ?? name ?? "…"}</h5>
            <div className="text-[11.5px] text-n-500">
              {detail?.node_type ?? "Entity"}
              {(detail?.community_name || detail?.community_id) &&
                ` · ${detail.community_name || detail.community_id}`}
            </div>
          </div>
        </div>

        {isLoading && (
          <div className="flex justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-n-500" />
          </div>
        )}

        {!isLoading && detail && (
          <>
            {detail.description && (
              <p className="my-3 text-[13px] text-n-300">{detail.description}</p>
            )}

            {detail.isolated_contexts?.length > 0 && (
              <>
                <div className="kicker mb-1.5">Contexts</div>
                {detail.isolated_contexts.slice(0, 6).map((ctx, i) => (
                  <p key={i} className="mb-1.5 border-l-2 border-accent-700 pl-2.5 text-[12px] text-n-300">
                    {ctx}
                  </p>
                ))}
              </>
            )}

            {detail.facts?.length > 0 && (
              <>
                <div className="kicker mb-1.5 mt-3.5">Facts</div>
                {detail.facts.map((fact, i) => (
                  <div key={i} className="flex gap-2 py-1 text-[12.5px] text-n-200">
                    <span className="text-accent-700">•</span>
                    <span>{fact}</span>
                  </div>
                ))}
              </>
            )}

            {detail.domain && (
              <>
                <div className="kicker mb-1.5 mt-3.5">Domain</div>
                <p className="text-[12.5px] text-n-300">{detail.domain}</p>
              </>
            )}

            {(detail.connections?.length ?? 0) > 0 && (
              <>
                <div className="kicker mb-1.5 mt-3.5">Connections</div>
                {detail.connections!.slice(0, 10).map((conn) => (
                  <div
                    key={conn.node_id}
                    className="flex items-center gap-2 py-1.5 text-[12.5px]"
                  >
                    <span className="min-w-20 truncate text-[11px] text-n-500">
                      {conn.direction === "incoming" ? "← " : ""}
                      {conn.relationship || "related"}
                    </span>
                    <span className="truncate text-accent-300">{conn.name}</span>
                  </div>
                ))}
              </>
            )}

            {(detail.related_notes?.length ?? 0) > 0 && (
              <>
                <div className="kicker mb-1.5 mt-3.5">Mentioned in</div>
                {detail.related_notes!.map((note) => (
                  <div
                    key={note.note_id}
                    className="flex items-center gap-2 py-1.5 text-[12.5px] text-n-200"
                  >
                    <FileText className="h-3.5 w-3.5 shrink-0 text-n-500" />
                    <span className="truncate">{note.name}</span>
                  </div>
                ))}
              </>
            )}

            {!hasBody && (
              <p className="py-6 text-center text-[12.5px] text-n-500">
                Nothing stored for this entity yet. Re-ingest the note if ingestion just
                finished.
              </p>
            )}

            <Link href="/graph-3d" className="btn btn-secondary mt-4 w-full no-underline">
              <Network className="h-3.5 w-3.5" /> Open in graph
            </Link>
          </>
        )}

        {!isLoading && !detail && (
          <p className="py-8 text-center text-[12.5px] text-n-500">Entity details unavailable</p>
        )}
      </div>
    </div>
  );
}
