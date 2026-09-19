import { useCallback, useEffect, useRef, useState } from "react";
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

  const cancel = useCallback(
    (rawUrl: string) => {
      if (!noteId) return;
      setJobs((prev) => ({ ...prev, [rawUrl]: { status: "cancelled", error: null } }));
      api.cancelAttachment(noteId, rawUrl, currentKB).catch(() => {});
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
  // The poller compares against what it last saw; reading state inside the
  // setJobs updater ran too late (React applies updaters lazily), so the
  // refresh that pulls the new description into the note never fired.
  const jobsRef = useRef(jobs);
  useEffect(() => {
    jobsRef.current = jobs;
  }, [jobs]);

  useEffect(() => {
    if (!noteId || !running) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const { jobs: fresh } = await api.getAttachmentJobs(noteId, currentKB);
        if (cancelled) return;
        const before = jobsRef.current;
        const finished = Object.entries(fresh).some(
          ([url, job]) => before[url]?.status === "running" && job.status === "done",
        );
        // Job records live in the API's memory. One we think is running that
        // the server no longer knows about died with a restart.
        const lost: Record<string, AttachmentJob> = {};
        for (const [url, job] of Object.entries(before)) {
          if (job.status === "running" && !(url in fresh)) {
            lost[url] = { status: "failed", error: "Lost when the backend restarted — press Redo" };
          }
        }
        setJobs((prev) => ({ ...prev, ...fresh, ...lost }));
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

  return { jobs, start, cancel };
}
