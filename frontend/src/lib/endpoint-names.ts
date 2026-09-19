/**
 * Names for OpenAI-compatible endpoints.
 *
 * A name given when the endpoint is added travels in the URL fragment
 * ("…/v1#Personal") and is part of the endpoint's identity, which is what
 * lets one server hold several keys. Endpoints added before that carried no
 * name; for those a label lives in localStorage, falling back to the host.
 */

import { loadJson, saveJson } from "@/lib/utils";

const KEY = "orb.endpointNames";

function readAll(): Record<string, string> {
  const parsed = loadJson<unknown>(KEY, {});
  return parsed && typeof parsed === "object" ? (parsed as Record<string, string>) : {};
}

/** Host of a URL, or the raw string when it will not parse. */
export function endpointHost(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/** The profile carried in the URL fragment ("…/v1#Work" → "Work"), or "". */
export function endpointProfile(url: string): string {
  const hash = url.indexOf("#");
  if (hash < 0) return "";
  try {
    return decodeURIComponent(url.slice(hash + 1)).trim();
  } catch {
    return url.slice(hash + 1).trim();
  }
}

/** The URL without its profile name — what requests are sent to. */
export function endpointRequestUrl(url: string): string {
  const hash = url.indexOf("#");
  return hash < 0 ? url : url.slice(0, hash);
}

export function endpointName(url: string): string {
  return endpointProfile(url) || readAll()[url]?.trim() || endpointHost(endpointRequestUrl(url));
}

/** Empty name clears the override and falls back to the host. */
export function setEndpointName(url: string, name: string): void {
  const all = readAll();
  const trimmed = name.trim();
  if (trimmed) all[url] = trimmed;
  else delete all[url];
  saveJson(KEY, all);
}
