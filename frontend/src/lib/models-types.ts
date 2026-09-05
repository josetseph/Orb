/** Shapes returned by GET /api/v1/models — everything the Models page renders. */

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
  /** "gguf" | "mlx" | "transformers" */
  format: string;
  /** False when this machine cannot run the layout. */
  runnable: boolean;
  unsupported_reason: string | null;
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

export interface ModelChoice {
  provider: string;
  model: string | null;
  ingestion_model: string | null;
  base_url?: string | null;
  inherited?: boolean;
}

export interface ModelsPageState {
  global: ModelChoice & { mode: "local" | "cloud"; configured: boolean };
  kb: {
    id: string;
    name: string;
    override: {
      provider: string | null;
      model: string | null;
      ingestion_model: string | null;
      base_url: string | null;
    };
    effective: ModelChoice;
  } | null;
  local: {
    models_dir: string;
    installed: InstalledModel[];
    installed_refs: string[];
    downloadable: DownloadableModel[];
    hardware: HardwareProfile;
    budget_note: string;
    embed: { label: string; size_gb: number } | null;
    reranker: { label: string; size_gb: number } | null;
  };
  cloud: { endpoints: string[]; providers: string[]; all_providers: string[] };
}

/** POST /api/v1/models/inspect */
export interface InspectedModel {
  ref: string;
  path: string;
  name: string;
  format: string;
  size_gb: number;
  warnings: string[];
}
