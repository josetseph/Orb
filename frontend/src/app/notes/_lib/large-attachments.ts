// Ingestion parks an attachment over the size threshold as
// `<!-- orb:extract src="…" mode="pending" -->` and graphs the rest of the note.

export type AttachmentMode = "graph" | "summary" | "index";

const PENDING_RE = /<!-- orb:extract src="([^"]*)" mode="pending" -->([\s\S]*?)<!-- \/orb:extract -->/g;

export interface PendingAttachment {
  link: string;
  name: string;
  /** Rough size of the extracted text: ~4 characters per token. */
  tokens: number;
}

export function pendingAttachments(content: string): PendingAttachment[] {
  return [...(content ?? "").matchAll(PENDING_RE)].map((m) => {
    let name = m[1].split("/").pop() ?? m[1];
    try {
      name = decodeURIComponent(name);
    } catch {
      /* keep the raw name */
    }
    // Upload names end in "-<8 hex>" before the extension.
    return { link: m[1], name: name.replace(/-[0-9a-f]{8}(\.[^.]+)$/i, "$1"), tokens: Math.round(m[2].length / 4) };
  });
}
