/**
 * Shared markdown entity-link pipeline used by chat messages and note
 * rendering. This module is the single source of truth for:
 *
 * - `urlTransform` — the security boundary that keeps `javascript:` (and any
 *   other unexpected scheme) out of rendered links while allowing our
 *   `entity://` pseudo-links through,
 * - `injectEntityLinks` — turning scanned entity mentions into `entity://`
 *   markdown links without corrupting existing links/wikilinks,
 * - `useScannedEntities` — fetching + caching entity scans,
 * - small helpers shared by the link renderers.
 *
 * Chat and notes previously carried drifting copies of all of this.
 */

import React, { useEffect, useState } from "react";
import { api } from "@/lib/api";

export type ScannedEntity = { node_id: string; name: string; node_type: string };

/** Allow entity:// pseudo-links through react-markdown's URL sanitizer. */
export function urlTransform(url: string): string {
  if (url.startsWith("entity://")) return url;
  // Reproduce react-markdown's defaultUrlTransform for all other schemes.
  return /^(https?|ircs?|mailto|xmpp):/i.test(url) || !url.includes(":") ? url : "";
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Injects entity:// pseudo-links directly into plain text for ReactMarkdown.
 * Uses a single-pass range-collection approach so no entity name is ever
 * matched inside an already-injected link (avoids nested/broken markdown).
 * Existing markdown links and `[[wikilinks]]` are preserved untouched
 * (`[[name|id]]` is converted to an entity link).
 */
export function injectEntityLinks(text: string, entities: ScannedEntity[]): string {
  if (!entities.length) return text;
  const sorted = [...entities].sort((a, b) => b.name.length - a.name.length);

  function replacePlain(plain: string): string {
    // Collect non-overlapping ranges against the ORIGINAL text, longest-match wins
    const ranges: { start: number; end: number; name: string; node_id: string }[] = [];
    for (const { name, node_id } of sorted) {
      const re = new RegExp(`\\b${escapeRegex(name)}\\b`, "gi");
      let m: RegExpExecArray | null;
      while ((m = re.exec(plain)) !== null) {
        const start = m.index;
        const end = start + m[0].length;
        if (ranges.some((r) => start < r.end && end > r.start)) continue;
        ranges.push({ start, end, name, node_id });
      }
    }
    // Single left-to-right substitution pass
    ranges.sort((a, b) => a.start - b.start);
    let result = "";
    let last = 0;
    for (const { start, end, name, node_id } of ranges) {
      result += plain.slice(last, start);
      result += `[${name}](entity://${node_id})`;
      last = end;
    }
    return result + plain.slice(last);
  }

  // Split on existing links/markers so attachment links never get nested
  // entity markdown injected into their label or URL.
  const parts = text.split(/(\[[^\]]+\]\([^)]+\)|\[\[[^\]]*\]\])/);
  return parts
    .map((part, i) => {
      if (i % 2 === 1) {
        if (/^\[[^\]]+\]\([^)]+\)$/.test(part)) return part;
        const m = part.match(/\[\[([^\]|]+)\|([^\]]+)\]\]/);
        return m ? `[${m[1]}](entity://${m[2]})` : part;
      }
      return replacePlain(part);
    })
    .join("");
}

// Bounded FIFO cache so re-rendering a conversation / reopening a note does
// not re-POST the same text to /graph/entities/scan-text.
const scanCache = new Map<string, ScannedEntity[]>();
const SCAN_CACHE_MAX = 200;
const NO_ENTITIES: ScannedEntity[] = [];

/**
 * Scans text for entity mentions.
 *
 * - `enabled: false` skips the network call entirely (used to limit chat
 *   scanning to recent messages).
 * - `cacheKey` (e.g. a message id) caches results across mounts/KB switches;
 *   the key is scoped by `kb` internally.
 */
export function useScannedEntities(
  content: string,
  kb: string,
  options?: { enabled?: boolean; cacheKey?: string },
): ScannedEntity[] {
  const enabled = options?.enabled ?? true;
  const cacheKey = options?.cacheKey ? `${kb}:${options.cacheKey}` : null;
  // Keyed result so a KB/content switch never shows the previous request's
  // entities. Cached results are derived at render, not set via effect.
  const requestKey = `${kb}\u0000${cacheKey ?? content}`;
  const [fetched, setFetched] = useState<{
    key: string;
    entities: ScannedEntity[];
  } | null>(null);

  useEffect(() => {
    if (!enabled || !content.trim()) return;
    if (cacheKey && scanCache.has(cacheKey)) return;
    let cancelled = false;
    const controller = new AbortController();
    api
      .scanTextEntities(content, kb, { signal: controller.signal })
      .then((e) => {
        if (cacheKey) {
          if (scanCache.size >= SCAN_CACHE_MAX) {
            const oldest = scanCache.keys().next().value;
            if (oldest !== undefined) scanCache.delete(oldest);
          }
          scanCache.set(cacheKey, e);
        }
        if (!cancelled) setFetched({ key: requestKey, entities: e });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [content, kb, enabled, cacheKey, requestKey]);

  const cached = cacheKey ? scanCache.get(cacheKey) : undefined;
  if (cached) return cached;
  return fetched?.key === requestKey ? fetched.entities : NO_ENTITIES;
}

/** Extract display text from react-markdown link children (handles nested nodes). */
export function flattenLinkText(children: React.ReactNode): string {
  if (children == null) return "";
  if (typeof children === "string" || typeof children === "number") {
    return String(children);
  }
  if (Array.isArray(children)) {
    return children.map(flattenLinkText).join("");
  }
  if (typeof children === "object" && "props" in children) {
    return flattenLinkText(
      (children as React.ReactElement<{ children?: React.ReactNode }>).props
        .children,
    );
  }
  return "";
}

/** True when href points at an uploaded vault attachment. */
export function isAttachmentHref(href: string): boolean {
  return (
    /\/files\//.test(href) ||
    /\/uploads\//.test(href) ||
    /\/vault-files\//.test(href) ||
    /^attachments\//.test(href)
  );
}

/**
 * Fallback anchor for non-entity, non-attachment links. External http(s)
 * links open outside the app window — web-ingested content must not be able
 * to navigate the Electron renderer away from Orb.
 */
export function MarkdownAnchor({
  href,
  children,
  ...props
}: React.ComponentPropsWithoutRef<"a">) {
  const isExternal = /^https?:\/\//i.test(href || "");
  return (
    <a
      href={href}
      {...(isExternal ? { target: "_blank", rel: "noopener noreferrer" } : {})}
      {...props}
    >
      {children}
    </a>
  );
}
