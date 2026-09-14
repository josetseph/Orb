import {
  Decoration,
  ViewPlugin,
  WidgetType,
  type DecorationSet,
  type ViewUpdate,
  EditorView,
} from "@codemirror/view";
import {
  isImageUrl,
  isVideoUrl,
  isAudioUrl,
  isPdfUrl,
  isTextUrl,
  isTabularUrl,
  resolveFileUrl,
  encodeFileUrl,
  fetchMediaObjectUrl,
  youtubeEmbedUrl,
  vimeoEmbedUrl,
} from "@/lib/utils";
import { visibleLineChunks } from "./visibleLineChunks";
import type { AttachmentJob } from "@/lib/types";
import {
  extractKey,
  extractNoun,
  extractedKeys,
  toggleExtractBlock,
} from "./extractMarkerExtension";

export type MediaEmbedOptions = {
  /** Per-attachment jobs keyed by the raw markdown url. */
  jobs?: Record<string, AttachmentJob>;
  /** Start "process this item only" for one attachment (force = redo). */
  onProcess?: (rawUrl: string, force: boolean) => void;
};

/** The one-click action for an attachment kind, or null when Orb cannot read it. */
function processVerb(kind: MediaKind): string | null {
  if (kind === "audio" || kind === "video") return "Transcribe";
  if (kind === "image") return "Describe";
  if (kind === "pdf" || kind === "table" || kind === "text") return "Extract";
  return null;
}

function processingLabel(kind: MediaKind): string {
  if (kind === "audio" || kind === "video") return "Transcribing…";
  if (kind === "image") return "Describing…";
  return "Extracting…";
}

/** Markdown images + paperclip/mic attachment links + plain links to embeddable video.
 * URLs may contain spaces (unencoded filenames) — match until `)`.
 * Accepts 📎 (paperclip) and 🖇 (paperclips) markers used by older/newer inserts.
 */
// A markdown URL may hold balanced parentheses ("Report (2026).pdf"), so
// [^)\n]+ stopped mid-filename and the embed silently did not render.
const MEDIA_URL = "(?:[^()\\n]|\\([^()\\n]*\\))+";
const MEDIA_RE = new RegExp(
  `(?:!\\[([^\\]]*)\\]\\((${MEDIA_URL})\\)|\\[([📎🖇🎤]?[^\\]]*)\\]\\((${MEDIA_URL})\\))`,
  "g",
);

type MediaKind =
  | "image"
  | "video"
  | "audio"
  | "pdf"
  | "youtube"
  | "vimeo"
  | "text"
  | "table";

function kindForUrl(url: string): MediaKind | null {
  const cleaned = url.trim();
  if (youtubeEmbedUrl(cleaned)) return "youtube";
  if (vimeoEmbedUrl(cleaned)) return "vimeo";
  if (isImageUrl(cleaned)) return "image";
  if (isVideoUrl(cleaned)) return "video";
  if (isAudioUrl(cleaned)) return "audio";
  if (isPdfUrl(cleaned)) return "pdf";
  // Ingested but previously invisible: a .csv or .md attachment was a chip you
  // could not look at without leaving the app.
  if (isTabularUrl(cleaned)) return "table";
  if (isTextUrl(cleaned)) return "text";
  return null;
}

/**
 * True when this plugin turns the whole `[label](url)` into a media widget.
 *
 * The live-preview extension asks before hiding a link's syntax: two plugins
 * replacing the same range is a rendering bug, and a rule copied into both
 * files would drift apart on the first change.
 */
export function mediaEmbedClaimsLink(
  isImage: boolean,
  rawLabel: string,
  rawUrl: string,
): boolean {
  const kind = kindForUrl(rawUrl);
  if (!kind) return false;
  if (isImage) return true;
  // 📎/🖇/🎤 marks an attachment Orb inserted; those always embed.
  if (/^[📎🖇🎤]/u.test(rawLabel)) return true;
  return kind === "youtube" || kind === "vimeo";
}

/** Cap the preview so a huge log file cannot lock up the editor. */
const TEXT_PREVIEW_BYTES = 64 * 1024;
const TABLE_PREVIEW_ROWS = 50;

function buildTable(text: string, sep: string): HTMLElement {
  const table = document.createElement("table");
  table.className = "cm-media-embed-table";
  const rows = text.split(/\r?\n/).filter((r) => r.length > 0);
  const shown = rows.slice(0, TABLE_PREVIEW_ROWS);
  shown.forEach((row, i) => {
    const tr = document.createElement("tr");
    for (const cell of row.split(sep)) {
      const td = document.createElement(i === 0 ? "th" : "td");
      td.textContent = cell.replace(/^"|"$/g, "");
      tr.appendChild(td);
    }
    table.appendChild(tr);
  });
  if (rows.length > shown.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.className = "cm-media-embed-more";
    td.textContent = `… ${rows.length - shown.length} more rows`;
    tr.appendChild(td);
    table.appendChild(tr);
  }
  return table;
}

/** Fetch a text-ish attachment and show it inline; never throws. */
function renderTextPreview(src: string, tabular: boolean, host: HTMLElement) {
  void (async () => {
    try {
      const res = await fetch(src);
      if (!res.ok) throw new Error(String(res.status));
      const full = await res.text();
      const text = full.slice(0, TEXT_PREVIEW_BYTES);
      host.textContent = "";
      if (tabular) {
        host.appendChild(buildTable(text, src.toLowerCase().includes(".tsv") ? "\t" : ","));
      } else {
        const pre = document.createElement("pre");
        pre.className = "cm-media-embed-pre";
        pre.textContent = text;
        host.appendChild(pre);
      }
      if (full.length > text.length) {
        const note = document.createElement("div");
        note.className = "cm-media-embed-more";
        note.textContent = "… truncated preview";
        host.appendChild(note);
      }
    } catch {
      host.textContent = "Could not load this file.";
      host.classList.add("cm-media-embed-error");
    }
  })();
}

class MediaWidget extends WidgetType {
  constructor(
    readonly kind: MediaKind,
    readonly src: string,
    readonly label: string,
    readonly kbId: string,
    readonly rawUrl: string,
    readonly hasBlock: boolean,
    readonly job: AttachmentJob | undefined,
    readonly onProcess: MediaEmbedOptions["onProcess"],
  ) {
    super();
  }

  eq(other: MediaWidget) {
    return (
      this.kind === other.kind &&
      this.src === other.src &&
      this.label === other.label &&
      this.kbId === other.kbId &&
      this.rawUrl === other.rawUrl &&
      this.hasBlock === other.hasBlock &&
      this.job?.status === other.job?.status &&
      this.job?.error === other.job?.error &&
      this.onProcess === other.onProcess
    );
  }

  /** Filename · status · [Transcript ▾] [Transcribe] — below every readable attachment. */
  private footer(view: EditorView): HTMLElement | null {
    const verb = processVerb(this.kind);
    if (!verb) return null;
    const noun = extractNoun(this.rawUrl);
    const bar = document.createElement("div");
    bar.className = "cm-media-embed-bar";

    const name = document.createElement("span");
    name.className = "cm-media-embed-bar-name";
    name.textContent = this.label;
    bar.appendChild(name);

    const status = document.createElement("span");
    status.className = "cm-media-embed-bar-status";
    if (this.job?.status === "running") {
      status.classList.add("cm-media-embed-bar-busy");
      status.textContent = processingLabel(this.kind);
    } else if (this.job?.status === "failed") {
      status.classList.add("cm-media-embed-bar-failed");
      status.textContent = `Failed: ${this.job.error || "unknown error"}`;
      status.title = this.job.error || "";
    } else if (this.hasBlock) {
      status.classList.add("cm-media-embed-bar-ready");
      status.textContent = `${noun} ready`;
    } else {
      status.textContent = "Not processed";
    }
    bar.appendChild(status);

    if (this.hasBlock) {
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "cm-media-embed-btn";
      toggle.textContent = `${noun} ▾`;
      toggle.title = `Show or hide the ${noun.toLowerCase()}`;
      toggle.addEventListener("mousedown", (e) => {
        e.preventDefault();
        toggleExtractBlock(view, this.rawUrl);
      });
      bar.appendChild(toggle);
    }

    if (this.onProcess) {
      const run = document.createElement("button");
      run.type = "button";
      run.className = "cm-media-embed-btn" + (this.hasBlock ? "" : " cm-media-embed-btn-primary");
      run.disabled = this.job?.status === "running";
      run.textContent = this.hasBlock ? "Redo" : verb;
      run.title = this.hasBlock
        ? `Run this attachment again and replace its ${noun.toLowerCase()}`
        : `${verb} only this attachment now — ingestion will not redo it`;
      run.addEventListener("mousedown", (e) => {
        e.preventDefault();
        this.onProcess?.(this.rawUrl, this.hasBlock);
      });
      bar.appendChild(run);
    }
    return bar;
  }

  toDOM(view: EditorView) {
    const wrap = document.createElement("div");
    wrap.className = "cm-media-embed";
    wrap.setAttribute("contenteditable", "false");
    const footer = this.footer(view);
    // Every branch below ends with `return done()`: the footer is appended
    // last, after that kind's media element, and the wrapper is handed back.
    const done = () => {
      if (footer) wrap.appendChild(footer);
      return wrap;
    };

    if (this.kind === "youtube" || this.kind === "vimeo") {
      const iframe = document.createElement("iframe");
      iframe.src = this.src;
      iframe.title = this.label || (this.kind === "youtube" ? "YouTube" : "Vimeo");
      iframe.className = "cm-media-embed-iframe";
      iframe.allow =
        "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share";
      iframe.allowFullscreen = true;
      iframe.loading = "lazy";
      iframe.referrerPolicy = "strict-origin-when-cross-origin";
      wrap.appendChild(iframe);
      return done();
    }

    if (this.kind === "pdf") {
      const iframe = document.createElement("iframe");
      iframe.src = this.src;
      iframe.title = this.label || "PDF";
      iframe.className = "cm-media-embed-pdf";
      iframe.loading = "lazy";
      wrap.appendChild(iframe);
      return done();
    }

    if (this.kind === "text" || this.kind === "table") {
      const body = document.createElement("div");
      body.className = "cm-media-embed-text";
      body.textContent = "Loading…";
      wrap.appendChild(body);
      renderTextPreview(this.src, this.kind === "table", body);
      return done();
    }

    if (this.kind === "image") {
      const img = document.createElement("img");
      img.src = this.src;
      img.alt = this.label || "attachment";
      img.title = this.label;
      img.loading = "lazy";
      img.className = "cm-media-embed-img";
      img.addEventListener("error", () => {
        wrap.classList.add("cm-media-embed-error");
        wrap.textContent = `Could not load image: ${this.label || this.src}`;
      });
      wrap.appendChild(img);
      return done();
    }

    if (this.kind === "video") {
      const video = document.createElement("video");
      video.controls = true;
      video.playsInline = true;
      video.preload = "metadata";
      video.className = "cm-media-embed-video";
      video.title = this.label;
      wrap.appendChild(video);

      const showError = () => {
        wrap.classList.add("cm-media-embed-error");
        wrap.textContent = `Could not load video: ${this.label || this.src}`;
      };

      // Prefer direct playback (Range / faststart); blob-fetch as fallback.
      video.src = this.src;
      let triedBlob = false;
      video.addEventListener("error", () => {
        if (triedBlob || /^https?:\/\//i.test(this.src)) {
          showError();
          return;
        }
        triedBlob = true;
        void fetchMediaObjectUrl(this.src, this.kbId)
          .then((url) => {
            video.src = url;
            video.load();
          })
          .catch(showError);
      });
      return done();
    }

    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "metadata";
    audio.className = "cm-media-embed-audio";
    audio.title = this.label;
    audio.src = this.src;
    // No caption: the footer below already names the file and its state.
    wrap.appendChild(audio);
    return done();
  }

  ignoreEvent() {
    // Allow interacting with media controls / iframe
    return true;
  }
}

/**
 * Live-preview embeds for images / video / audio / PDF / YouTube / Vimeo.
 * Inactive lines: replace markdown with the media widget.
 * Active (cursor) line: leave raw markdown for editing.
 */
export function createMediaEmbedDecorations(
  kbId = "default",
  options: MediaEmbedOptions = {},
) {
  // Jobs are keyed by the raw url as typed; match on the backend's identity.
  const jobsByKey = new Map<string, AttachmentJob>();
  for (const [url, job] of Object.entries(options.jobs ?? {})) {
    jobsByKey.set(extractKey(url), job);
  }
  return ViewPlugin.fromClass(
    class {
      decorations: DecorationSet;

      constructor(view: EditorView) {
        this.decorations = this.build(view);
      }

      update(update: ViewUpdate) {
        if (
          update.docChanged ||
          update.viewportChanged ||
          update.selectionSet
        ) {
          this.decorations = this.build(update.view);
        }
      }

      build(view: EditorView): DecorationSet {
        const marks: ReturnType<Decoration["range"]>[] = [];
        const activeLine = view.state.doc.lineAt(
          view.state.selection.main.head,
        ).number;
        // ponytail: whole-doc scan per rebuild to learn which attachments
        // already have a block; index it if notes ever get large.
        const done = extractedKeys(view.state.doc.toString());

        // Only scan the visible viewport — media markdown is single-line.
        for (const chunk of visibleLineChunks(view)) {
          MEDIA_RE.lastIndex = 0;
          let m: RegExpExecArray | null;
          while ((m = MEDIA_RE.exec(chunk.text)) !== null) {
            const from = chunk.offset + m.index;
            const to = from + m[0].length;
            const isMdImage = m[0].startsWith("![");
            const label = (isMdImage ? m[1] : m[3] || "").replace(
              /^[📎🖇🎤]\s*/u,
              "",
            );
            const rawUrl = (isMdImage ? m[2] : m[4] || "").trim();
            if (!rawUrl) continue;

            const kind = kindForUrl(rawUrl);
            if (!kind) continue;

            // Plain markdown links (no 📎/🖇/🎤 / image) only embed for
            // YouTube/Vimeo; the rest are hidden-syntax links instead.
            if (!mediaEmbedClaimsLink(isMdImage, m[3] || "", rawUrl)) continue;

            const line = view.state.doc.lineAt(from).number;
            if (line === activeLine) continue;

            let src: string;
            if (kind === "youtube") {
              src = youtubeEmbedUrl(rawUrl) || rawUrl;
            } else if (kind === "vimeo") {
              src = vimeoEmbedUrl(rawUrl) || rawUrl;
            } else {
              src = encodeFileUrl(resolveFileUrl(rawUrl.trim(), kbId));
            }

            marks.push(
              Decoration.replace({
                widget: new MediaWidget(
                  kind,
                  src,
                  label || rawUrl.trim(),
                  kbId,
                  rawUrl.trim(),
                  done.has(extractKey(rawUrl)),
                  jobsByKey.get(extractKey(rawUrl)),
                  options.onProcess,
                ),
                block: false,
              }).range(from, to),
            );
          }
        }

        return Decoration.set(marks, true);
      }
    },
    { decorations: (v) => v.decorations },
  );
}
