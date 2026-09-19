import { useEffect, useState } from "react";

export { clsx as cn } from "clsx";

/** Read a JSON value from localStorage; `fallback` on missing/invalid/blocked. */
export function loadJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

/** Write a JSON value to localStorage; silently drops on quota/privacy errors. */
export function saveJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* a lost preference is not worth an error */
  }
}

/** `value`, but only updated once it has held still for `ms`. */
export function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

/** FastAPI `detail` from an API error (string or validation list), else the Error message, else `fallback`. */
export function errMessage(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (typeof d === "object" && d && "msg" in d ? String((d as { msg: string }).msg) : String(d)))
      .join("; ");
  }
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

/**
 * Resolve attachment / vault file URLs for the browser.
 * Supports /vault-files/… and attachments/… URLs.
 */
export function resolveFileUrl(url: string, kbId = "default"): string {
  if (!url) return url;
  if (url.startsWith("/vault-files/")) return url;
  if (url.startsWith("attachments/")) {
    return `/vault-files/${encodeURIComponent(kbId)}/${url}`;
  }
  return url;
}

/** Returns true if the URL points to an image file. */
export function isImageUrl(url: string): boolean {
  // Chromium decodes all of these. HEIC/HEIF are deliberately absent — Safari
  // shows them, Chromium does not, so an <img> would just break.
  return /\.(jpg|jpeg|png|gif|webp|svg|avif|bmp|ico)(\?|$)/i.test(
    decodeURIComponentSafe(url),
  );
}

/** Returns true if the URL points to a video file. */
export function isVideoUrl(url: string): boolean {
  // .mkv and .avi are intentionally excluded: ingestion accepts them, but
  // Chromium cannot demux Matroska or AVI, so a <video> element renders a
  // permanently broken player. They stay as download chips instead.
  return /\.(mp4|webm|mov|m4v|ogv)(\?|$)/i.test(decodeURIComponentSafe(url));
}

/** Returns true if the URL points to an audio file. */
export function isAudioUrl(url: string): boolean {
  return /\.(m4a|m4b|mp3|wav|ogg|oga|opus|aac|flac|weba)(\?|$)/i.test(
    decodeURIComponentSafe(url),
  );
}

/** Returns true if the URL points to a PDF. */
export function isPdfUrl(url: string): boolean {
  return /\.pdf(\?|$)/i.test(decodeURIComponentSafe(url));
}

/** Plain-text-ish files we can show inline without a parser. */
export function isTextUrl(url: string): boolean {
  return /\.(txt|md|markdown|log|json|ya?ml|xml|ini|cfg|toml)(\?|$)/i.test(
    decodeURIComponentSafe(url),
  );
}

/** Delimited files worth rendering as a table rather than raw text. */
export function isTabularUrl(url: string): boolean {
  return /\.(csv|tsv)(\?|$)/i.test(decodeURIComponentSafe(url));
}


/** Convert a YouTube watch/share URL into an embeddable iframe src, or null. */
export function youtubeEmbedUrl(url: string): string | null {
  const raw = (url || "").trim();
  if (!raw) return null;
  try {
    const u = new URL(raw);
    const host = u.hostname.replace(/^www\./, "").toLowerCase();
    let id: string | null = null;
    if (host === "youtu.be") {
      id = u.pathname.replace(/^\//, "").split("/")[0] || null;
    } else if (host === "youtube.com" || host === "m.youtube.com" || host === "youtube-nocookie.com") {
      if (u.pathname.startsWith("/embed/")) {
        id = u.pathname.split("/")[2] || null;
      } else if (u.pathname.startsWith("/shorts/")) {
        id = u.pathname.split("/")[2] || null;
      } else {
        id = u.searchParams.get("v");
      }
    }
    if (!id || !/^[\w-]{6,}$/.test(id)) return null;
    return `https://www.youtube-nocookie.com/embed/${id}`;
  } catch {
    return null;
  }
}

/** Convert a Vimeo URL into an embeddable iframe src, or null. */
export function vimeoEmbedUrl(url: string): string | null {
  const raw = (url || "").trim();
  if (!raw) return null;
  try {
    const u = new URL(raw);
    const host = u.hostname.replace(/^www\./, "").toLowerCase();
    if (host !== "vimeo.com" && host !== "player.vimeo.com") return null;
    const parts = u.pathname.split("/").filter(Boolean);
    const id = parts.find((p) => /^\d+$/.test(p));
    if (!id) return null;
    return `https://player.vimeo.com/video/${id}`;
  } catch {
    return null;
  }
}

function decodeURIComponentSafe(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

/**
 * Percent-encode one path segment for use inside a markdown link.
 *
 * ``encodeURIComponent`` deliberately leaves ``!'()*`` alone, but an unescaped
 * ``)`` closes a ``[label](url)`` link early — so a file called
 * ``Report (2026).pdf`` produced a link that pointed at ``Report (2026`` and
 * rendered as raw text. Brackets get the same treatment for the label's sake.
 */
function encodePathSegment(segment: string): string {
  return encodeURIComponent(segment)
    .replace(/\(/g, "%28")
    .replace(/\)/g, "%29")
    .replace(/\[/g, "%5B")
    .replace(/\]/g, "%5D");
}

/** Encode a freshly uploaded `/vault-files/<kb>/<raw path>` URL for a markdown link. */
export function encodeFileUrl(url: string): string {
  if (!url.startsWith("/vault-files/")) return url;
  const rest = url.slice("/vault-files/".length);
  const slash = rest.indexOf("/");
  if (slash < 0) return url;
  const kb = rest.slice(0, slash);
  const path = rest.slice(slash + 1);
  return `/vault-files/${encodeURIComponent(kb)}/${path.split("/").map(encodePathSegment).join("/")}`;
}

/**
 * Fetch a local vault media URL as a blob object URL so HTML5 video/audio can play
 * even when the file has moov-at-end or proxies mishandle Range requests.
 * External http(s) URLs are returned as-is (do not blob-fetch them).
 */
export async function fetchMediaObjectUrl(
  url: string,
  kbId = "default",
): Promise<string> {
  const trimmed = (url || "").trim();
  if (/^https?:\/\//i.test(trimmed)) {
    return trimmed;
  }
  const href = resolveFileUrl(trimmed, kbId);
  const res = await fetch(href);
  if (!res.ok) {
    throw new Error(`Failed to load media (${res.status})`);
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}
