"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Box,
  Building2,
  Circle,
  Lightbulb,
  Loader2,
  MessageCircle,
  User,
  X,
} from "lucide-react";
import { api } from "@/lib/api";
import type { KnowledgeNode } from "@/components/graph3d/types";
import { nodeColor } from "@/components/graph3d/nodeColors";

type Detail = KnowledgeNode & {
  community_name?: string;
  connections?: { node_id: string; name: string; relationship?: string; direction?: string }[];
  related_notes?: { note_id: string; name: string }[];
};

function TypeIcon({ type, className }: { type: string; className?: string }) {
  const t = type.toLowerCase();
  if (t === "person" || t === "character") return <User className={className} />;
  if (t === "organization" || t === "team" || t === "group" || t === "band")
    return <Building2 className={className} />;
  if (t === "concept") return <Lightbulb className={className} />;
  if (t === "product" || t === "reference") return <Box className={className} />;
  return <Circle className={className} />;
}

/** Entity panel rendered inline in the graph's right aside. */
export function NodeDetailModal({
  node,
  onClose,
  kb,
  onSelectNodeId,
}: {
  node: KnowledgeNode;
  onClose: () => void;
  kb: string;
  /** Select a connected node by id, when the graph has it. */
  onSelectNodeId?: (nodeId: string) => void;
}) {
  const router = useRouter();
  const color = nodeColor(node.node_type);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [fetching, setFetching] = useState(true);

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFetching(true);
    api
      .getNodeDetail(node.node_id, kb)
      .then((d) => {
        if (!cancelled) setDetail({ ...node, ...d });
      })
      .catch(() => {
        if (!cancelled) setDetail(node);
      })
      .finally(() => {
        if (!cancelled) setFetching(false);
      });
    return () => {
      cancelled = true;
    };
  }, [node.node_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const d: Detail = detail ?? node;
  const firstNote = d.related_notes?.[0];

  return (
    <>
      <div className="flex items-center gap-2 px-3 pb-2 pt-3">
        <span className="kicker flex-1 text-accent">Entity</span>
        <button
          type="button"
          onClick={onClose}
          className="grid h-[26px] w-[26px] place-items-center rounded-[6px] text-n-400 hover:bg-n-900"
          aria-label="Close"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="flex-1 overflow-auto px-4 pb-4">
        <div className="mb-1.5 flex items-start gap-2.5">
          <span
            className="grid h-[34px] w-[34px] shrink-0 place-items-center rounded-[9px]"
            style={{ background: `color-mix(in srgb, ${color} 55%, var(--color-surface))` }}
          >
            <TypeIcon type={d.node_type} className="h-[18px] w-[18px] text-accent-100" />
          </span>
          <div>
            <h5 className="text-[17px] font-medium">{d.name}</h5>
            <div className="text-[11.5px] text-n-500">
              {d.node_type}
              {d.community_name || d.community_id ? ` · ${d.community_name || d.community_id}` : ""}
            </div>
          </div>
        </div>
        {fetching && (
          <div className="flex items-center gap-1.5 py-2 text-[12px] text-n-500">
            <Loader2 className="h-3 w-3 animate-spin" /> Loading details…
          </div>
        )}
        {d.description && <p className="my-2.5 text-[13px] text-n-300">{d.description}</p>}

        {d.facts && d.facts.length > 0 && (
          <>
            <div className="kicker mb-1.5 mt-3.5">Facts</div>
            {d.facts.map((f, i) => (
              <div key={i} className="flex gap-2 py-1 text-[12.5px] text-n-200">
                <span className="text-accent-700">•</span>
                <span>{f}</span>
              </div>
            ))}
          </>
        )}

        {d.isolated_contexts && d.isolated_contexts.length > 0 && (
          <>
            <div className="kicker mb-1.5 mt-3.5">Contexts</div>
            {d.isolated_contexts.slice(0, 4).map((ctx, i) => (
              <div
                key={i}
                className="my-1.5 border-l-2 border-accent-700 pl-2.5 text-[12.5px] text-n-300"
              >
                {ctx}
              </div>
            ))}
          </>
        )}

        {d.connections && d.connections.length > 0 && (
          <>
            <div className="kicker mb-1.5 mt-3.5">Connections</div>
            {d.connections.slice(0, 12).map((c) => (
              <button
                key={c.node_id}
                type="button"
                onClick={() => onSelectNodeId?.(c.node_id)}
                className="row -mx-2 py-1.5"
              >
                <span className="min-w-20 text-[11px] text-n-500">
                  {c.direction === "incoming" ? "← " : ""}
                  {c.relationship || "related"}
                </span>
                <span className="text-accent-300">{c.name}</span>
              </button>
            ))}
          </>
        )}

        {d.related_notes && d.related_notes.length > 0 && (
          <>
            <div className="kicker mb-1.5 mt-3.5">Mentioned in</div>
            {d.related_notes.map((n) => (
              <Link
                key={n.note_id}
                href={`/notes?note=${encodeURIComponent(n.note_id)}`}
                className="row -mx-2 py-1.5 text-text no-underline"
              >
                {n.name}
              </Link>
            ))}
          </>
        )}

        {!fetching && !d.description && !d.isolated_contexts?.length && !d.connections?.length && (
          <p className="py-4 text-[12px] text-n-500">
            No stored contexts yet for this node. Re-ingest related notes if content should
            appear here.
          </p>
        )}

        <div className="mt-4 flex gap-2">
          <button
            type="button"
            className="btn btn-primary flex-1"
            onClick={() => router.push("/chat")}
          >
            <MessageCircle className="h-3.5 w-3.5" /> Ask about this
          </button>
          {firstNote && (
            <Link
              href={`/notes?note=${encodeURIComponent(firstNote.note_id)}`}
              className="btn btn-secondary no-underline"
            >
              Open note
            </Link>
          )}
        </div>
      </div>
    </>
  );
}
