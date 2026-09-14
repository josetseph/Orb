"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AttachmentJob, Note } from "@/lib/types";

type Args = {
  currentKB: string;
  selectedNote: Note | null;
  refreshSelectedNote: (noteId: string) => Promise<void>;
};

/** Per-attachment "process this item" jobs for the open note. */
export function useAttachmentJobs({ currentKB, selectedNote, refreshSelectedNote }: Args) {
  const noteId = selectedNote?.id ?? null;
  const [jobs, setJobs] = useState<Record<string, AttachmentJob>>({});

  // A new note means a new job list; the server keeps the old one.
  const [jobsFor, setJobsFor] = useState(noteId);
  if (jobsFor !== noteId) {
    setJobsFor(noteId);
    setJobs({});
  }

  const start = useCallback(
    (rawUrl: string, force = false) => {
      if (!noteId) return;
      setJobs((prev) => ({ ...prev, [rawUrl]: { status: "running", error: null } }));
      api.processAttachment(noteId, rawUrl, force, currentKB).catch((err) => {
        const detail =
          (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
          (err instanceof Error ? err.message : "Could not start");
        setJobs((prev) => ({ ...prev, [rawUrl]: { status: "failed", error: detail } }));
      });
    },
    [noteId, currentKB],
  );

  // Coming back to a note: the server still knows what is running, the
  // local list does not. One fetch seeds it, which also restarts polling.
  useEffect(() => {
    if (!noteId) return;
    let cancelled = false;
    api
      .getAttachmentJobs(noteId, currentKB)
      .then(({ jobs: fresh }) => {
        if (!cancelled && Object.keys(fresh).length) setJobs((prev) => ({ ...fresh, ...prev }));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [noteId, currentKB]);

  const running = Object.values(jobs).some((j) => j.status === "running");

  useEffect(() => {
    if (!noteId || !running) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const { jobs: fresh } = await api.getAttachmentJobs(noteId, currentKB);
        if (cancelled) return;
        let finished = false;
        setJobs((prev) => {
          const next = { ...prev };
          for (const [url, job] of Object.entries(fresh)) {
            if (prev[url]?.status === "running" && job.status === "done") finished = true;
            next[url] = job;
          }
          return next;
        });
        if (finished) void refreshSelectedNote(noteId);
      } catch {
        /* backend hiccup — try again next tick */
      }
    };
    const timer = window.setInterval(() => void tick(), 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [noteId, currentKB, running, refreshSelectedNote]);

  return { jobs, start };
}
