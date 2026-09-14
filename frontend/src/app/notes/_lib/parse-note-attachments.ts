import type { NoteAttachment } from "./types";

// A markdown URL may contain balanced parentheses — "Report (2026).pdf" is a
// valid link target. Matching [^)]+ stopped at the first ")" and dropped the
// attachment entirely, so a note with two PDFs rendered only the one whose
// name had no brackets. One nesting level covers real filenames.
const URL_PART = "(?:[^()\\n]|\\([^()\\n]*\\))+";
const ATTACHMENT_REGEX = new RegExp(
  `(?:!\\[([^\\]]*)\\]\\((${URL_PART})\\)|\\[([📎🖇🎤][^\\]]+)\\]\\((${URL_PART})\\))`,
  "g",
);

export function parseNoteAttachments(content: string): NoteAttachment[] {
  const attachments: NoteAttachment[] = [];
  let m: RegExpExecArray | null;
  const regex = new RegExp(ATTACHMENT_REGEX.source, ATTACHMENT_REGEX.flags);
  while ((m = regex.exec(content)) !== null) {
    const label = (m[1] ?? m[3] ?? "file").replace(/^[📎🖇🎤]\s*/u, "");
    const url = m[2] ?? m[4] ?? "";
    if (!url) continue;
    attachments.push({ label, url, raw: m[0] });
  }
  return attachments;
}
