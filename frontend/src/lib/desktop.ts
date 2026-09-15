/** Desktop Electron bridge (preload). Absent in browser/dev. */
export type OrbDesktopBridge = {
  isDesktop?: boolean;
  pickDirectory?: (opts?: {
    title?: string;
    buttonLabel?: string;
    defaultPath?: string;
  }) => Promise<string | null>;
  /** Pick a single file (e.g. a .gguf outside the models directory). */
  pickFile?: (opts?: {
    title?: string;
    buttonLabel?: string;
    defaultPath?: string;
    filters?: Array<{ name: string; extensions: string[] }>;
  }) => Promise<string | null>;
  /** Which providers have a stored key. Never returns key material. */
  listCredentials?: () => Promise<{
    encryptionAvailable: boolean;
    /** "keychain" (macOS), "dpapi" (Windows), or the Linux backend name. */
    encryptionBackend?: string;
    /** Why encryption is unavailable, when it is. */
    encryptionReason?: string;
    providers: Array<{ provider: string; configured: boolean }>;
    endpoints?: string[];
    error?: string;
  }>;
  /** Encrypt a key into the OS keychain and push it to the running API. */
  setCredential?: (
    provider: string,
    apiKey: string,
  ) => Promise<{
    ok: boolean;
    provider?: string;
    /** False when the key works now but could not be written to disk. */
    persisted?: boolean;
    warning?: string;
    error?: string;
  }>;
  deleteCredential?: (provider: string) => Promise<{ ok: boolean; error?: string }>;
  /** Same, for an OpenAI-compatible endpoint keyed by its URL. */
  setEndpointCredential?: (
    baseUrl: string,
    apiKey: string,
  ) => Promise<{
    ok: boolean;
    baseUrl?: string;
    persisted?: boolean;
    warning?: string;
    error?: string;
  }>;
  deleteEndpointCredential?: (
    baseUrl: string,
  ) => Promise<{ ok: boolean; error?: string }>;
  /** Kill and relaunch the FastAPI process (keys are re-pushed after). */
  restartBackend?: () => Promise<{ ok: boolean; error?: string }>;
  /** Direct FastAPI base, e.g. http://127.0.0.1:17401/api/v1 */
  getApiBaseUrl?: () => Promise<string>;
  /** Reveal a local file in Finder / Explorer (vault / data / models only) */
  revealInFolder?: (filePath: string) => Promise<{ ok: boolean; error?: string }>;
  /** Splash boot status (shell pages); unused by Next UI */
  onStatus?: (cb: (message: string) => void) => void;
  getDefaultPaths?: () => Promise<{ data_dir: string; models_dir: string }>;
  getAppInfo?: () => Promise<{ version: string; packaged: boolean }>;
  saveWizard?: (payload: {
    data_dir: string;
    models_dir: string;
    default_vault_path?: string;
    ai_setup_mode?: string;
  }) => Promise<{ ok: boolean; pathsFile: string }>;
  wizardDone?: () => void;
};

export function getDesktopBridge(): OrbDesktopBridge | null {
  if (typeof window === "undefined") return null;
  const w = window as Window & {
    orbDesktop?: OrbDesktopBridge;
    /** @deprecated former LifeOS / LiveOS bridge name */
    liveosDesktop?: OrbDesktopBridge;
  };
  return w.orbDesktop || w.liveosDesktop || null;
}

export function isDesktopApp(): boolean {
  return Boolean(getDesktopBridge()?.isDesktop);
}

export async function pickDesktopDirectory(opts?: {
  title?: string;
  defaultPath?: string;
}): Promise<string | null> {
  const bridge = getDesktopBridge();
  if (!bridge?.pickDirectory) return null;
  return bridge.pickDirectory(opts);
}

let cachedDesktopApiBase: string | null | undefined;

/** Prefer the desktop API port so large uploads skip the Next.js 10MB proxy limit. */
export async function resolveApiBaseUrl(fallback: string): Promise<string> {
  if (cachedDesktopApiBase !== undefined) {
    return cachedDesktopApiBase || fallback;
  }
  const bridge = getDesktopBridge();
  if (bridge?.getApiBaseUrl) {
    try {
      const url = (await bridge.getApiBaseUrl())?.replace(/\/$/, "") || null;
      cachedDesktopApiBase = url;
      return url || fallback;
    } catch {
      // Bridge not ready yet (e.g. early startup) — don't cache the failure,
      // so the next call retries instead of routing every upload through the
      // Next proxy for the whole session.
      return fallback;
    }
  }
  cachedDesktopApiBase = null;
  return fallback;
}

export async function pickDesktopFile(opts?: {
  title?: string;
  defaultPath?: string;
  filters?: Array<{ name: string; extensions: string[] }>;
}): Promise<string | null> {
  const bridge = getDesktopBridge();
  if (!bridge?.pickFile) return null;
  return bridge.pickFile(opts);
}

export async function revealInFolder(filePath: string): Promise<boolean> {
  const bridge = getDesktopBridge();
  if (!bridge?.revealInFolder) return false;
  const result = await bridge.revealInFolder(filePath);
  return Boolean(result?.ok);
}

/** Label for the reveal action on the current platform. */
export function revealInFolderLabel(): string {
  if (typeof navigator === "undefined") return "Reveal in folder";
  const ua = navigator.userAgent || "";
  if (/Mac|iPhone|iPad/i.test(ua)) return "Reveal in Finder";
  if (/Win/i.test(ua)) return "Reveal in Explorer";
  return "Reveal in folder";
}
