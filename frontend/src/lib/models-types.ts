/** Shapes returned by GET /api/v1/models — everything the Models page renders. */

import type { EffectiveLLM, KBLLMConfig } from "@/lib/types";

export interface HardwareProfile {
  ram_gb: number;
  usable_model_gb: number;
  platform: string;
  machine: string;
  accel: { backend: string; reason?: string };
}

/** A model found on this machine, in any layout. */
export interface InstalledModel {
  /** What a KB stores: a MODELS_DIR-relative or absolute path. */
  ref: string;
  path: string;
  label: string;
  architecture: string;
  size_gb: number;
  context_length: number | null;
  shards: number;
  warnings: string[];
}

/** A curated model that can be downloaded. */
export interface DownloadableModel {
  id: string;
  label: string;
  size_gb: number;
  params?: string;
  family?: string;
  fits_budget: boolean;
  downloaded: boolean;
  recommended: boolean;
}

export interface ModelsPageState {
  global: EffectiveLLM & { mode: "local" | "cloud"; configured: boolean };
  kb: (Pick<KBLLMConfig, "override" | "effective"> & { id: string; name: string }) | null;
  local: {
    models_dir: string;
    installed: InstalledModel[];
    installed_refs: string[];
    downloadable: DownloadableModel[];
    hardware: HardwareProfile;
    budget_note: string;
    embed: { label: string; size_gb: number } | null;
    reranker: { label: string; size_gb: number } | null;
    /** Vision route, transcription and Marlin — what runs on attachments. */
    media: Array<{
      kind: string;
      label: string;
      purpose: string;
      name: string;
      installed: boolean;
      engine?: string | null;
      engine_note?: string | null;
      /** Why it is not installed and how to get it; null once present. */
      hint?: string | null;
    }>;
  };
  cloud: { endpoints: string[]; providers: string[]; all_providers: string[] };
}

/** POST /api/v1/models/inspect */
export interface InspectedModel {
  ref: string;
  path: string;
  name: string;
  size_gb: number;
  warnings: string[];
}
