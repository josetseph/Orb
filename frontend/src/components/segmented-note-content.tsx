import React, { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Image as ImageIcon, FileText, Mic, Film } from "lucide-react";
import { resolveFileUrl, isImageUrl, isVideoUrl, isPdfUrl } from "@/lib/utils";
import { BlobMediaPlayer } from "@/components/blob-media-player";
import {
    MarkdownAnchor,
    flattenLinkText,
    injectEntityLinks,
    isAttachmentHref,
    urlTransform,
    useScannedEntities,
} from "@/lib/markdown-entities";

// ── Segment types ────────────────────────────────────────────────────────────

type SegmentType = "text" | "image" | "pdf" | "audio" | "video";

interface Segment {
    type: SegmentType;
    label: string; // e.g. "Votex 365 Ad", "Offer Letter.pdf", "Voice Recording"
    content: string;
}

// ── Block parser ──────────────────────────────────────────────────────────────
// Ingestion wraps every extraction in `<!-- orb:extract src="…" -->` …
// `<!-- /orb:extract -->`; the first line inside is `[<Kind> (<name>)]:` or
// `[Image: <title>]`. Splitting on the delimiters means no header list to keep
// in step with the backend.

const BLOCK_RE = /<!-- orb:extract src="[^"]*" -->([\s\S]*?)<!-- \/orb:extract -->/g;
const HEADER_RE = /^\s*\[([^\]:(]+?)(?::\s*([^\]]*)|\s*\(([^)]*)\))?\]:?/;

function segmentTypeFor(kind: string): Exclude<SegmentType, "text"> {
    const k = kind.toLowerCase();
    if (k.startsWith("image")) return "image";
    if (k.startsWith("video")) return "video";
    if (k.includes("transcript")) return "audio";
    return "pdf";
}

function parseSegments(content: string): Segment[] {
    const segments: Segment[] = [];
    let last = 0;
    for (const m of content.matchAll(BLOCK_RE)) {
        const before = content.slice(last, m.index).trim();
        if (before) segments.push({ type: "text", label: "", content: before });
        const inner = m[1];
        const h = inner.match(HEADER_RE);
        const kind = h?.[1]?.trim() ?? "Extraction";
        segments.push({
            type: h ? segmentTypeFor(kind) : "pdf",
            label: (h?.[2] ?? h?.[3] ?? kind).trim() || kind,
            content: (h ? inner.slice(h[0].length) : inner).trim(),
        });
        last = (m.index ?? 0) + m[0].length;
    }
    const tail = content.slice(last).trim();
    if (tail) segments.push({ type: "text", label: "", content: tail });
    return segments;
}

// ── Divider header ────────────────────────────────────────────────────────────

const SEGMENT_ICONS: Record<Exclude<SegmentType, "text">, React.ReactNode> = {
    image: <ImageIcon className="h-3.5 w-3.5" />,
    pdf: <FileText className="h-3.5 w-3.5" />,
    audio: <Mic className="h-3.5 w-3.5" />,
    video: <Film className="h-3.5 w-3.5" />,
};

function SegmentDivider({
    type,
    label,
}: {
    type: Exclude<SegmentType, "text">;
    label: string;
}) {
    return (
        <div className="not-prose my-4 flex items-center gap-3">
            <div className="hr-fade flex-1" />
            <div className="flex items-center gap-1.5 rounded-[6px] bg-surface px-2.5 py-1 text-[11.5px] text-n-300 shadow-sm">
                <span className="text-accent-300">{SEGMENT_ICONS[type]}</span>
                <span className="max-w-[280px] truncate">{label}</span>
            </div>
            <div className="hr-fade flex-1" />
        </div>
    );
}

// ── Link renderer ─────────────────────────────────────────────────────────────

function makeLinkComponent(
    onFileClick: (url: string, filename: string) => void,
    onEntityClick: ((nodeId: string, name: string) => void) | undefined,
    kbId: string,
) {
    return function LinkComponent({
        children,
        href,
        ...props
    }: React.ComponentPropsWithoutRef<"a"> & { node?: unknown }) {
        const text = flattenLinkText(children).trim();
        const resolvedUrl = href ? resolveFileUrl(href, kbId) : "";
        const isAttachment =
            Boolean(href) &&
            (text.startsWith("📎") ||
                text.startsWith("🎤") ||
                isAttachmentHref(href!));
        const filename =
            text.replace(/^[📎🎤]\s*/u, "").trim() ||
            (href ? decodeURIComponent(href.split("/").pop() ?? "file") : "file");

        // Entity mention pseudo-link: entity://node_id
        if (href?.startsWith("entity://")) {
            const nodeId = href.slice("entity://".length);
            return (
                <button
                    type="button"
                    onClick={() => onEntityClick?.(nodeId, text)}
                    className="cursor-pointer text-accent-200 underline decoration-dotted decoration-accent-600 underline-offset-[3px] hover:text-accent-100"
                >
                    {text}
                </button>
            );
        }
        // Inline image/video/PDF rendering for 📎 file attachments
        if (href && isAttachment) {
            if (isImageUrl(href) || isImageUrl(resolvedUrl)) {
                return (
                    <span className="not-prose my-4 block">
                        <img
                            src={resolvedUrl}
                            alt={filename}
                            className="max-w-full cursor-zoom-in rounded-md shadow-sm"
                            onClick={() => onFileClick(resolvedUrl, filename)}
                        />
                        <span className="mt-1 block text-[11.5px] text-n-500">{filename}</span>
                    </span>
                );
            }
            if (isVideoUrl(href) || isVideoUrl(resolvedUrl)) {
                return (
                    <span className="not-prose my-4 block">
                        <BlobMediaPlayer url={resolvedUrl} kbId={kbId} kind="video" />
                        <span className="mt-1 block text-[11.5px] text-n-500">{filename}</span>
                    </span>
                );
            }
            if (isPdfUrl(href) || isPdfUrl(resolvedUrl)) {
                return (
                    <span className="not-prose my-4 block">
                        <iframe
                            src={resolvedUrl}
                            title={filename}
                            className="h-[480px] max-h-[70vh] w-full max-w-3xl rounded-md bg-bg-deep shadow-sm"
                        />
                        <button
                            type="button"
                            onClick={() => onFileClick(resolvedUrl, filename)}
                            className="mt-1 block text-[11.5px] text-n-500 hover:text-accent-300"
                        >
                            {filename} — open full preview
                        </button>
                    </span>
                );
            }
        }
        if (href && isAttachment) {
            return (
                <button
                    type="button"
                    onClick={() => onFileClick(resolvedUrl, filename)}
                    className="inline-flex items-center gap-1.5 rounded-[6px] bg-surface px-2 py-0.5 text-[12px] text-n-200 no-underline shadow-sm hover:shadow-md"
                >
                    {text || `📎 ${filename}`}
                </button>
            );
        }
        return (
            <MarkdownAnchor href={href} {...props}>
                {children}
            </MarkdownAnchor>
        );
    };
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
    content: string;
    onFileClick: (url: string, filename: string) => void;
    onEntityClick?: (nodeId: string, name: string) => void;
    proseClassName: string;
    /** Knowledge base key used to scan for entity mentions. Defaults to "default". */
    kb?: string;
}

export function SegmentedNoteContent({
    content,
    onFileClick,
    onEntityClick,
    proseClassName,
    kb = "default",
}: Props) {
    const scannedEntities = useScannedEntities(content || "", kb, {
        enabled: Boolean(onEntityClick),
    });

    const segments = useMemo(() => parseSegments(content || "*Empty note*"), [content]);
    const LinkComponent = useMemo(
        () => makeLinkComponent(onFileClick, onEntityClick, kb),
        [onFileClick, onEntityClick, kb],
    );

    return (
        <div className={proseClassName}>
            {segments.map((seg, idx) => (
                <React.Fragment key={idx}>
                    {seg.type !== "text" && (
                        <SegmentDivider
                            type={seg.type as Exclude<SegmentType, "text">}
                            label={seg.label}
                        />
                    )}
                    <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{ a: LinkComponent }}
                        urlTransform={urlTransform}
                    >
                        {injectEntityLinks(
                            seg.content || (idx === 0 && !seg.content ? "*Empty note*" : ""),
                            scannedEntities,
                        )}
                    </ReactMarkdown>
                </React.Fragment>
            ))}
        </div>
    );
}
