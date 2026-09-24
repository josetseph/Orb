import { api } from "@/lib/api";

/**
 * Desktop shell bridge. Only what the shell alone can do lives here (native
 * pickers, restarting the runtime); everything else is an API call.
 * Absent in a plain browser.
 */
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
  /** OS print dialog for the current page. */
  printPage?: () => Promise<void>;
  /** Kill and relaunch the Python runtime. */
  restartBackend?: () => Promise<{ ok: boolean; error?: string }>;
  /** OS notification; resolves false when unavailable or denied. */
  notify?: (title: string, body: string) => Promise<boolean>;
};

export function getDesktopBridge(): OrbDesktopBridge | null {
  if (typeof window === "undefined") return null;
  const w = window as Window & { orbDesktop?: OrbDesktopBridge };
  return w.orbDesktop || null;
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

export async function pickDesktopFile(opts?: {
  title?: string;
  defaultPath?: string;
  filters?: Array<{ name: string; extensions: string[] }>;
}): Promise<string | null> {
  const bridge = getDesktopBridge();
  if (!bridge?.pickFile) return null;
  return bridge.pickFile(opts);
}

/** Print the page: the shell's native dialog in the app, the browser's otherwise. */
export async function printPage(): Promise<void> {
  const bridge = getDesktopBridge();
  if (bridge?.printPage) await bridge.printPage();
  else window.print();
}

/** Notify through the OS when the window is not focused (the UI itself shows the change otherwise). */
export function notifyIfUnfocused(title: string, body: string): void {
  if (typeof document !== "undefined" && document.hasFocus()) return;
  getDesktopBridge()?.notify?.(title, body).catch(() => {});
}

/** Show a vault / data / models file on disk. Throws with the API's reason when refused. */
export async function revealInFolder(filePath: string): Promise<boolean> {
  const result = await api.revealInFolder(filePath);
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
