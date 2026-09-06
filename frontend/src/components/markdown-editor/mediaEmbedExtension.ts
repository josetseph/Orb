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
  resolveFileUrl,
  encodeFileUrl,
  fetchMediaObjectUrl,
  youtubeEmbedUrl,
  vimeoEmbedUrl,
} from "@/lib/utils";
import { visibleLineChunks } from "./visibleLineChunks";

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

type MediaKind = "image" | "video" | "audio" | "pdf" | "youtube" | "vimeo";

function kindForUrl(url: string): MediaKind | null {
  const cleaned = url.trim();
  if (youtubeEmbedUrl(cleaned)) return "youtube";
  if (vimeoEmbedUrl(cleaned)) return "vimeo";
  if (isImageUrl(cleaned)) return "image";
  if (isVideoUrl(cleaned)) return "video";
  if (isAudioUrl(cleaned)) return "audio";
  if (isPdfUrl(cleaned)) return "pdf";
  return null;
}

class MediaWidget extends WidgetType {
  constructor(
    readonly kind: MediaKind,
    readonly src: string,
    readonly label: string,
    readonly kbId: string,
  ) {
    super();
  }

  eq(other: MediaWidget) {
    return (
      this.kind === other.kind &&
      this.src === other.src &&
      this.label === other.label &&
      this.kbId === other.kbId
    );
  }

  toDOM() {
    const wrap = document.createElement("div");
    wrap.className = "cm-media-embed";
    wrap.setAttribute("contenteditable", "false");

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
      return wrap;
    }

    if (this.kind === "pdf") {
      const caption = document.createElement("div");
      caption.className = "cm-media-embed-caption";
      caption.textContent = this.label || "PDF";
      const iframe = document.createElement("iframe");
      iframe.src = this.src;
      iframe.title = this.label || "PDF";
      iframe.className = "cm-media-embed-pdf";
      iframe.loading = "lazy";
      wrap.appendChild(caption);
      wrap.appendChild(iframe);
      return wrap;
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
      return wrap;
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
      return wrap;
    }

    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "metadata";
    audio.className = "cm-media-embed-audio";
    audio.title = this.label;
    audio.src = this.src;
    const caption = document.createElement("div");
    caption.className = "cm-media-embed-caption";
    caption.textContent = this.label || "Audio";
    wrap.appendChild(caption);
    wrap.appendChild(audio);
    return wrap;
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
export function createMediaEmbedDecorations(kbId = "default") {
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

        // Only scan the visible viewport — media markdown is single-line.
        for (const chunk of visibleLineChunks(view)) {
          MEDIA_RE.lastIndex = 0;
          let m: RegExpExecArray | null;
          while ((m = MEDIA_RE.exec(chunk.text)) !== null) {
            const from = chunk.offset + m.index;
            const to = from + m[0].length;
            const isMdImage = m[0].startsWith("![");
            const label = (isMdImage ? m[1] : m[3] || "").replace(
              /^[📎🖇🎤]\s*/,
              "",
            );
            const rawUrl = (isMdImage ? m[2] : m[4] || "").trim();
            if (!rawUrl) continue;

            const kind = kindForUrl(rawUrl);
            if (!kind) continue;

            // Plain markdown links (no 📎/🖇/🎤 / image) only embed for YouTube/Vimeo
            if (
              !isMdImage &&
              !(m[3] || "").match(/^[📎🖇🎤]/) &&
              kind !== "youtube" &&
              kind !== "vimeo"
            ) {
              continue;
            }

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
