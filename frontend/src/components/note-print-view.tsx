import { useEffect } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { printPage } from "@/lib/desktop";
import { resolveFileUrl } from "@/lib/utils";
import { flattenLinkText } from "@/lib/markdown-entities";
import { slugOf } from "@/components/markdown-editor/livePreviewHideMarks";

export interface PrintJob {
  title: string;
  content: string;
  createdAt?: string | null;
}

// Headings carry the slug `[text](#slug)` links use, so a table of contents
// jumps within the PDF instead of pointing at the app's URL.
const heading =
  (Tag: "h1" | "h2" | "h3" | "h4" | "h5" | "h6") =>
  ({ children }: { children?: React.ReactNode }) => <Tag id={slugOf(flattenLinkText(children))}>{children}</Tag>;
const headings = { h1: heading("h1"), h2: heading("h2"), h3: heading("h3"), h4: heading("h4"), h5: heading("h5"), h6: heading("h6") };

// The editor only renders the lines on screen, so a note is printed from a
// full rendering that exists only on paper: `#print-root` is hidden on screen
// and the only thing shown in print (globals.css).
export function NotePrintView({ job, kb, onDone }: { job: PrintJob; kb: string; onDone: () => void }) {
  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      // Fonts (KaTeX) and images must be in before the page is captured.
      await document.fonts.ready;
      const images = Array.from(document.querySelectorAll<HTMLImageElement>("#print-root img"));
      await Promise.all(images.map((img) => (img.complete ? null : img.decode().catch(() => null))));
      if (!cancelled) await printPage();
    };
    run().catch((e) => {
      alert(`Could not open the print dialog: ${e instanceof Error ? e.message : String(e)}`);
      onDone();
    });
    window.addEventListener("afterprint", onDone);
    return () => {
      cancelled = true;
      window.removeEventListener("afterprint", onDone);
    };
  }, [job, onDone]);

  // Extraction markers are comments the renderer would drop anyway; the
  // summary/transcript inside them prints as part of the note.
  // `[[note#heading|alias]]` reads as its alias (or note name) on paper.
  const body = job.content
    .replace(/<!-- \/?orb:extract[^>]*-->/g, "")
    .replace(/!?\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]/g, (_, note: string, alias?: string) => (alias ?? note).trim());
  const date = job.createdAt ? new Date(job.createdAt).toLocaleDateString() : "";

  return createPortal(
    <div id="print-root">
      <h1>{job.title || "Untitled"}</h1>
      {date && <p className="print-date">{date}</p>}
      <div className="prose max-w-none">
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={[rehypeKatex]}
          components={{
            ...headings,
            img: ({ src, alt }) => <img src={resolveFileUrl(String(src ?? ""), kb)} alt={alt ?? ""} />,
            // Attachments are files on disk: name them rather than link to nowhere.
            a: ({ href, children }) =>
              href && !/^(https?|mailto):/i.test(href) && !href.startsWith("#") ? (
                <span>{children}</span>
              ) : (
                <a href={href}>{children}</a>
              ),
          }}
        >
          {body}
        </ReactMarkdown>
      </div>
    </div>,
    document.body,
  );
}
