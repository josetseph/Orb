"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, CircleDashed, Clock, Cpu, Eye, Loader2, AudioLines } from "lucide-react";
import { api } from "@/lib/api";
import { useKB } from "@/lib/kb-context";
import { cn } from "@/lib/utils";

type StatusTone = "idle" | "ingest" | "community" | "digest" | "error";
type Payload = Awaited<ReturnType<typeof api.getMaintenanceStatus>>;

interface StatusView {
  tone: StatusTone;
  label: string;
  meta: string;
  detail: string;
  busy: boolean;
}

function buildStatus(payload: Payload | null): StatusView {
  if (!payload) {
    return { tone: "idle", label: "Checking…", meta: "", detail: "Waiting for backend status.", busy: false };
  }
  const ingestActive = Number(payload.ingestion?.active || 0);
  if (ingestActive > 0) {
    return {
      tone: "ingest",
      label: `Ingesting ${ingestActive} note${ingestActive === 1 ? "" : "s"}`,
      meta: "running",
      detail: "Extracting entities and links into the graph.",
      busy: true,
    };
  }
  if (payload.community_detection?.running) {
    const pending = Number(payload.community_detection.pending_nodes || 0);
    return {
      tone: "community",
      label: "Rebuilding communities",
      meta: pending > 0 ? `${pending} nodes` : "running",
      detail: "Community detection is running over the graph.",
      busy: true,
    };
  }
  if (payload.temporal_digests?.running) {
    return { tone: "digest", label: "Building digests", meta: "running", detail: "Temporal digest build in progress.", busy: true };
  }
  if (payload.community_detection?.timer_armed) {
    const secs = payload.community_detection.idle_seconds ?? 120;
    return {
      tone: "community",
      label: "Community rebuild queued",
      meta: `~${secs}s`,
      detail: "Starts once ingestion has settled.",
      busy: false,
    };
  }
  const last = payload.ingestion?.last_completed_at;
  return {
    tone: "idle",
    label: "Ready",
    meta: "idle",
    detail: last
      ? `Last ingest finished ${new Date(last).toLocaleString()}.`
      : "No ingestion or community jobs running.",
    busy: false,
  };
}

function useMaintenanceStatus(kb: string) {
  const [payload, setPayload] = useState<Payload | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    // Poll fast only while a job is running; back off when idle and pause
    // while the window is hidden — this mounts on every page.
    const isActive = (data: Payload | null) =>
      Boolean(
        data &&
          (Number(data.ingestion?.active || 0) > 0 ||
            data.community_detection?.running ||
            data.community_detection?.timer_armed ||
            data.temporal_digests?.running),
      );

    const schedule = (active: boolean) => {
      if (cancelled) return;
      timer = window.setTimeout(poll, active ? 4000 : 30000);
    };

    const poll = async () => {
      if (cancelled) return;
      if (document.visibilityState === "hidden") {
        schedule(false);
        return;
      }
      try {
        const data = await api.getMaintenanceStatus(kb);
        if (cancelled) return;
        setPayload(data);
        setFailed(false);
        schedule(isActive(data));
      } catch {
        if (cancelled) return;
        setFailed(true);
        schedule(false);
      }
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        window.clearTimeout(timer);
        void poll();
      }
    };

    document.addEventListener("visibilitychange", onVisibility);
    void poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [kb]);

  return useMemo<StatusView>(() => {
    if (failed) {
      return {
        tone: "error",
        label: "Backend offline",
        meta: "",
        detail: "Could not reach the API. It may be restarting.",
        busy: false,
      };
    }
    return buildStatus(payload);
  }, [payload, failed]);
}

const DOT: Record<StatusTone, string> = {
  idle: "bg-accent-700",
  ingest: "bg-accent-300 animate-pulse",
  community: "bg-accent-300 animate-pulse",
  digest: "bg-accent-300 animate-pulse",
  error: "bg-danger",
};

/** Sidebar activity row with a popover of what is running. */
export function ActivityStatus() {
  const { currentKB } = useKB();
  const view = useMaintenanceStatus(currentKB);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full flex-col gap-1.5 rounded-md px-2.5 py-2 text-left hover:bg-n-900"
        aria-label={`${view.label}. ${view.detail}`}
      >
        <span className="flex items-center gap-2 text-[12px]">
          <span className={cn("dot", DOT[view.tone])} />
          <span className="flex-1 truncate text-n-300">{view.label}</span>
          <span className="text-[11px] text-n-600">{view.meta}</span>
        </span>
        {view.busy && (
          <span className="block h-0.5 overflow-hidden rounded bg-n-900">
            <span className="block h-full w-1/2 animate-pulse rounded bg-accent" />
          </span>
        )}
      </button>

      {open && (
        <div className="popover absolute bottom-0 left-full z-40 ml-3 w-[300px] p-3">
          <div className="mb-2 flex items-baseline justify-between">
            <span className="text-[13px] font-medium">Activity</span>
            <span className="text-[11px] text-n-500">All local · nothing leaves this machine</span>
          </div>
          <div className="flex items-start gap-2.5 py-2">
            {view.busy ? (
              <Loader2 className="mt-0.5 h-[15px] w-[15px] animate-spin text-accent-300" />
            ) : view.tone === "error" ? (
              <CircleDashed className="mt-0.5 h-[15px] w-[15px] text-danger" />
            ) : view.meta.startsWith("~") ? (
              <Clock className="mt-0.5 h-[15px] w-[15px] text-n-500" />
            ) : (
              <CheckCircle2 className="mt-0.5 h-[15px] w-[15px] text-n-600" />
            )}
            <div className="min-w-0 flex-1">
              <div className="text-[12.5px]">{view.label}</div>
              <div className="text-[11px] text-n-500">{view.detail}</div>
            </div>
          </div>
          <div className="my-2 h-px bg-divider" />
          <div className="flex gap-3.5 text-[11px] text-n-500">
            <span className="inline-flex items-center gap-1"><Cpu className="h-3 w-3" /> Chat</span>
            <span className="inline-flex items-center gap-1"><AudioLines className="h-3 w-3" /> Qwen3-ASR</span>
            <span className="inline-flex items-center gap-1"><Eye className="h-3 w-3" /> Vision via chat model</span>
            <span className="ml-auto truncate">{currentKB}</span>
          </div>
        </div>
      )}
    </div>
  );
}
