/**
 * Friendly names for OpenAI-compatible endpoints.
 *
 * Kept in localStorage rather than the credential store: a label is not a
 * secret, needs no encryption, and storing it beside the key would mean a new
 * IPC method and a migration for something the user can retype in seconds.
 * Falls back to the host, so an unnamed endpoint still reads as "openrouter.ai"
 * rather than a full URL.
 */

const KEY = "orb.endpointNames";

function readAll(): Record<string, string> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

/** Host of a URL, or the raw string when it will not parse. */
export function endpointHost(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

export function endpointName(url: string): string {
  return readAll()[url]?.trim() || endpointHost(url);
}

/** Empty name clears the override and falls back to the host. */
export function setEndpointName(url: string, name: string): void {
  if (typeof window === "undefined") return;
  const all = readAll();
  const trimmed = name.trim();
  if (trimmed) all[url] = trimmed;
  else delete all[url];
  try {
    window.localStorage.setItem(KEY, JSON.stringify(all));
  } catch {
    // A name is a convenience; losing it must never break saving an endpoint.
  }
}
