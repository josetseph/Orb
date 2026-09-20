import { describe, expect, it } from "vitest";
import type { Note } from "@/lib/types";
import { isActiveProcessingNote } from "./processing-status";

const note = (processing_stage: string, extra: Partial<Note> = {}) =>
  ({ id: "n", processed: false, failed: false, processing_stage, ...extra }) as Note;

describe("isActiveProcessingNote", () => {
  it("is true for pipeline stages, so a reloaded page resumes polling", () => {
    for (const stage of ["Queued for ingestion", "Starting ingestion", "Extracting knowledge graph"]) {
      expect(isActiveProcessingNote(note(stage))).toBe(true);
    }
  });

  it("is false for save / watcher markers and finished notes", () => {
    for (const stage of ["Saved", "Changed on disk — re-ingest when ready", "Ingestion complete", ""]) {
      expect(isActiveProcessingNote(note(stage))).toBe(false);
    }
    expect(isActiveProcessingNote(note("Extracting knowledge graph", { processed: true }))).toBe(false);
  });
});
