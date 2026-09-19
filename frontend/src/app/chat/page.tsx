import React, { useState, useRef, useEffect, useCallback, useMemo } from "react";
import {
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Download,
  FileText,
  Layers,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/lib/api";
import { cn, isAudioUrl, isImageUrl, isVideoUrl, resolveFileUrl } from "@/lib/utils";
import { revealInFolder } from "@/lib/desktop";
import { useKB } from "@/lib/kb-context";
import { useChat } from "@/lib/chat-context";
import type { Message } from "@/lib/chat-context";
import { SegmentedNoteContent } from "@/components/segmented-note-content";
import { EntityDetailPanel } from "@/components/entity-detail-panel";
import { FilePreviewModal } from "@/app/notes/_components/FilePreviewModal";
import type { FilePreview, NotePreview } from "@/lib/types";
import {
  MarkdownAnchor,
  flattenLinkText,
  injectEntityLinks,
  urlTransform,
  useScannedEntities,
} from "@/lib/markdown-entities";

// Only the most recent assistant messages get an entity scan — long
// conversations used to fire one POST /graph/entities/scan-text per message
// on load. Older messages render without highlights (cached ones still show).
const ENTITY_SCAN_RECENT_LIMIT = 5;

const SUGGESTIONS = [
  "What are my recent thoughts?",
  "Summarize my notes from this week",
  "What concepts am I exploring?",
];

/**
 * react-markdown `components` map: entity:// links → entity buttons,
 * 📎/🎤 links → attachment chips, everything else → normal anchor.
 */
function makeLinkRenderer(
  handleFileClick: (url: string, filename: string) => void,
  onEntityClick?: (nodeId: string, name: string) => void,
) {
  return {
    a: ({
      node: _node,
      children,
      href,
      ...props
    }: React.ComponentPropsWithoutRef<"a"> & { node?: unknown }) => {
      const text = flattenLinkText(children).trim();
      if (href?.startsWith("entity://") && onEntityClick) {
        const nodeId = href.slice("entity://".length);
        return (
          <button
            type="button"
            onClick={() => onEntityClick(nodeId, text)}
            className="cursor-pointer text-accent-200 underline decoration-dotted decoration-accent-600 underline-offset-[3px] hover:text-accent-100"
          >
            {text}
          </button>
        );
      }
      if (href && (text.startsWith("📎") || text.startsWith("🎤"))) {
        const filename = text.replace(/^[📎🎤]\s*/u, "");
        return (
          <button
            type="button"
            onClick={() => handleFileClick(href, filename)}
            className="inline-flex items-center gap-1.5 rounded-[6px] bg-surface px-2 py-0.5 text-[12px] text-n-200 no-underline shadow-sm hover:shadow-md"
          >
            {text}
          </button>
        );
      }
      return (
        <MarkdownAnchor href={href} {...props}>
          {children}
        </MarkdownAnchor>
      );
    },
  };
}

/** One assistant message: thinking toggle, entity-highlighted body, sources. */
function AssistantMessageBody({
  message,
  kb,
  scanEnabled,
  onEntityClick,
  onFileClick,
  onOpenNote,
  expanded,
  onToggleThinking,
}: {
  message: Message;
  kb: string;
  scanEnabled: boolean;
  onEntityClick: (nodeId: string, name: string) => void;
  onFileClick: (url: string, filename: string) => void;
  onOpenNote: (noteId: string) => void;
  expanded: boolean;
  onToggleThinking: () => void;
}) {
  const scannedEntities = useScannedEntities(message.content, kb, {
    enabled: scanEnabled,
    cacheKey: message.id,
  });

  const processContent = useCallback(
    (text: string) => (scannedEntities.length ? injectEntityLinks(text, scannedEntities) : text),
    [scannedEntities],
  );

  const linkRenderer = useMemo(
    () => makeLinkRenderer(onFileClick, onEntityClick),
    [onFileClick, onEntityClick],
  );

  const sources = message.sources ?? [];

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-3">
      {message.thinking && (
        <div>
          <button
            type="button"
            onClick={onToggleThinking}
            className="inline-flex items-center gap-1.5 text-[12px] text-n-500 hover:text-accent-300"
          >
            {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
            Model thinking
          </button>
          {expanded && (
            <pre className="mt-2 animate-rise whitespace-pre-wrap rounded-md bg-surface px-3 py-2 font-mono text-[12px] leading-relaxed text-n-300 shadow-sm">
              {message.thinking}
            </pre>
          )}
        </div>
      )}

      <div className="prose-orb">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={linkRenderer} urlTransform={urlTransform}>
          {processContent(message.content)}
        </ReactMarkdown>
      </div>

      {sources.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-0.5 text-[11px] text-n-500">Sources</span>
          {sources.map((s, i) => (
            <button
              key={s.id}
              type="button"
              onClick={() => onOpenNote(s.id)}
              className="inline-flex items-center gap-1.5 rounded-[6px] border border-n-800 py-1 pl-1.5 pr-2 text-[12px] text-n-200 hover:border-accent hover:text-accent-200"
            >
              <span className="grid h-4 w-4 place-items-center rounded bg-accent-800 text-[10px] text-accent-100">
                {i + 1}
              </span>
              {s.title}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ChatPage() {
  const { currentKB, currentKBName } = useKB();
  const {
    messages,
    conversations,
    activeConversationId,
    isLoading,
    loadingStage,
    loadingModel,
    sendMessage,
    selectConversation,
    startNewConversation,
    deleteActiveConversation,
    initializeForKb,
  } = useChat();
  // ?q= from "Ask about this" on the graph seeds the composer.
  const [input, setInput] = useState(
    () => new URLSearchParams(window.location.search).get("q") ?? "",
  );
  const [previewNote, setPreviewNote] = useState<NotePreview | null>(null);
  const [filePreview, setFilePreview] = useState<FilePreview | null>(null);
  const [expandedThinking, setExpandedThinking] = useState<Set<string>>(new Set());
  const [entityPanelNodeId, setEntityPanelNodeId] = useState<string | null>(null);
  const [entityPanelName, setEntityPanelName] = useState<string | undefined>();
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const handleEntityClick = useCallback((nodeId: string, name: string) => {
    setEntityPanelNodeId(nodeId);
    setEntityPanelName(name);
  }, []);

  const scannableMessageIds = useMemo(() => {
    const ids = new Set<string>();
    for (let i = messages.length - 1; i >= 0 && ids.size < ENTITY_SCAN_RECENT_LIMIT; i--) {
      if (messages[i].role === "assistant") ids.add(messages[i].id);
    }
    return ids;
  }, [messages]);

  useEffect(() => {
    void initializeForKb(currentKB);
  }, [currentKB, initializeForKb]);

  // ?q= means a fresh thread with the question waiting in the composer, not
  // sent until you press Enter.
  useEffect(() => {
    if (!new URLSearchParams(window.location.search).get("q")) return;
    window.history.replaceState({}, "", "/chat");
    startNewConversation();
  }, [startNewConversation]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = () => {
    if (!input.trim() || isLoading) return;
    sendMessage(input, currentKB);
    setInput("");
  };

  const openNote = useCallback(
    (noteId: string) => {
      api
        .getNote(noteId, currentKB)
        .then((n) =>
          setPreviewNote({ id: n.id, title: n.title || "Untitled", content: n.content }),
        )
        .catch(console.error);
    },
    [currentKB],
  );

  const handleFileClick = useCallback(
    (url: string, filename: string) => {
      const resolvedUrl = resolveFileUrl(url, currentKB);
      let type: FilePreview["type"] = "other";
      if (isImageUrl(resolvedUrl) || isImageUrl(filename)) type = "image";
      else if (/\.pdf(\?|$)/i.test(resolvedUrl) || /\.pdf$/i.test(filename)) type = "pdf";
      else if (isVideoUrl(resolvedUrl) || isVideoUrl(filename)) type = "video";
      else if (isAudioUrl(resolvedUrl) || isAudioUrl(filename)) type = "audio";
      setFilePreview({ url: resolvedUrl, filename, type });
    },
    [currentKB],
  );

  const handleRevealPreviewFile = async () => {
    if (!filePreview) return;
    try {
      const { local_path } = await api.resolveVaultLocalPath(filePreview.url, currentKB);
      const ok = await revealInFolder(local_path);
      if (!ok) window.open(filePreview.url, "_blank", "noopener,noreferrer");
    } catch (error) {
      console.error("Reveal failed:", error);
      alert(`Could not reveal this file on disk: ${error instanceof Error ? error.message : "unknown error"}`);
    }
  };

  const deleteConversation = async (id: string) => {
    if (!window.confirm("Delete this conversation? This cannot be undone.")) return;
    if (id !== activeConversationId) await selectConversation(id, currentKB);
    await deleteActiveConversation(currentKB);
  };

  const exportChat = async () => {
    if (!activeConversationId) return;
    try {
      const md = await api.exportChat(activeConversationId, "markdown");
      const url = URL.createObjectURL(new Blob([md], { type: "text/markdown" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `chat-${activeConversationId.slice(0, 8)}.md`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      /* ignore */
    }
  };

  return (
    <div className="screen">
      {/* Threads */}
      <div className="flex w-[248px] shrink-0 flex-col border-r border-n-900">
        <div className="pane-header">
          <h5 className="pane-title">Ask</h5>
          {activeConversationId && (
            <button type="button" onClick={() => void exportChat()} className="btn btn-ghost btn-sm" title="Export as Markdown">
              <Download className="h-3.5 w-3.5" />
            </button>
          )}
          <button type="button" onClick={startNewConversation} className="btn btn-primary btn-icon" title="New conversation">
            <Plus className="h-[15px] w-[15px]" />
          </button>
        </div>
        <div className="flex-1 overflow-auto px-1.5">
          <div className="kicker px-2 pb-1 pt-2">Recent</div>
          {conversations.length === 0 && (
            <p className="px-2 py-2 text-[12px] text-n-500">No conversations yet</p>
          )}
          {conversations.map((conv) => (
            <div
              key={conv.id}
              className={cn(
                "group flex w-full items-start gap-1 rounded-md px-2 py-2 hover:bg-n-900",
                conv.id === activeConversationId && "bg-surface",
              )}
            >
              <button
                type="button"
                onClick={() => void selectConversation(conv.id, currentKB)}
                className="min-w-0 flex-1 text-left"
              >
                <div className="truncate text-[13px]">{conv.title}</div>
                <div className="text-[11px] text-n-500">
                  {conv.updated_at ? new Date(conv.updated_at).toLocaleDateString() : ""}
                </div>
              </button>
              <button
                type="button"
                onClick={() => void deleteConversation(conv.id)}
                className="grid h-6 w-6 shrink-0 place-items-center rounded text-n-500 opacity-0 hover:bg-n-800 hover:text-danger-text group-hover:opacity-100"
                title="Delete conversation"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Main */}
      <div className="relative flex min-w-0 flex-1 flex-col">
        <div className="flex-1 overflow-auto pb-6 pt-8">
          <div className="mx-auto flex max-w-[760px] flex-col gap-6 px-8">
            {messages.length === 0 && (
              <div className="flex flex-col gap-2.5 pb-2.5 pt-10">
                <h4 className="text-[24px]">Ask across everything in {currentKBName}</h4>
                <p className="text-[14px] text-n-400">
                  Answers cite the notes they came from. Everything runs on this machine.
                </p>
                <div className="mt-2.5 flex flex-wrap gap-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => setInput(s)}
                      className="rounded-md border border-divider px-3 py-1.5 text-[12.5px] text-n-200 hover:border-accent hover:text-accent-200"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((message) =>
              message.role === "user" ? (
                <div key={message.id} className="flex justify-end">
                  <div className="max-w-[78%] whitespace-pre-wrap rounded-lg bg-surface px-3.5 py-2.5 text-[14.5px] leading-[1.55] shadow-sm">
                    {message.content}
                  </div>
                </div>
              ) : (
                <div key={message.id} className="flex animate-rise gap-3.5">
                  <img src="/logo-icon.png" alt="" width={24} height={24} className="mt-1 h-6 w-6 shrink-0 rounded-[7px]" />
                  <AssistantMessageBody
                    message={message}
                    kb={currentKB}
                    scanEnabled={scannableMessageIds.has(message.id)}
                    onEntityClick={handleEntityClick}
                    onFileClick={handleFileClick}
                    onOpenNote={openNote}
                    expanded={expandedThinking.has(message.id)}
                    onToggleThinking={() =>
                      setExpandedThinking((prev) => {
                        const next = new Set(prev);
                        if (next.has(message.id)) next.delete(message.id);
                        else next.add(message.id);
                        return next;
                      })
                    }
                  />
                </div>
              ),
            )}

            {isLoading && (
              <div className="flex items-center gap-3.5">
                <img src="/logo-icon.png" alt="" width={24} height={24} className="h-6 w-6 animate-pulse rounded-[7px]" />
                <span className="text-[13px] text-n-400">{loadingStage || "Thinking…"}</span>
                {loadingModel && (
                  <span className="text-[11px] text-n-600">· {loadingModel}, on this machine</span>
                )}
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Composer */}
        <div className="px-8 pb-6">
          <div
            className={cn(
              "mx-auto flex max-w-[760px] flex-col gap-2 rounded-lg bg-surface p-3",
              input ? "shadow-md" : "shadow-sm",
            )}
          >
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              rows={2}
              placeholder="Ask about anything you've written, recorded or filed…"
              className="w-full resize-none bg-transparent px-1 py-0.5 text-[14.5px] leading-[1.5] outline-none placeholder:text-n-600"
            />
            <div className="flex items-center gap-1.5">
              <span className="inline-flex h-[26px] items-center gap-1.5 rounded-[6px] border border-n-800 px-2 text-[11.5px] text-n-300">
                <Layers className="h-3 w-3" /> Whole workspace
              </span>
              <span className="flex-1" />
              <span className="text-[11px] text-n-600">↵ to send · ⇧↵ newline</span>
              <button
                type="button"
                onClick={send}
                disabled={!input.trim() || isLoading}
                className={cn("btn btn-primary btn-icon", input.trim() && "bg-accent/14")}
                title="Send"
                aria-label="Send"
              >
                <ArrowUp className="h-[15px] w-[15px]" />
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Entity aside */}
      {entityPanelNodeId && (
        <aside className="flex w-[320px] shrink-0 animate-rise flex-col border-l border-n-900 bg-bg-deep/40">
          <div className="flex items-center gap-2 px-3 pb-2 pt-3">
            <span className="kicker flex-1 text-accent">Entity</span>
            <button
              type="button"
              onClick={() => setEntityPanelNodeId(null)}
              className="grid h-[26px] w-[26px] place-items-center rounded-[6px] text-n-400 hover:bg-n-900"
              aria-label="Close entity panel"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <EntityDetailPanel
            nodeId={entityPanelNodeId}
            name={entityPanelName}
            kb={currentKB}
            onClose={() => setEntityPanelNodeId(null)}
          />
        </aside>
      )}

      {/* Note preview */}
      {previewNote && (
        <dialog
          ref={(el) => {
            if (el && !el.open) el.showModal();
          }}
          onClose={() => setPreviewNote(null)}
          onClick={(e) => e.target === e.currentTarget && setPreviewNote(null)}
          className="dialog max-h-[80vh] max-w-3xl overflow-hidden"
        >
          <div className="flex items-center gap-2.5">
            <FileText className="h-4 w-4 text-accent-300" />
            <div className="dialog-title flex-1 truncate">{previewNote.title}</div>
            <button
              type="button"
              onClick={() => setPreviewNote(null)}
              className="grid h-[26px] w-[26px] place-items-center rounded-[6px] text-n-400 hover:bg-n-900"
              aria-label="Close preview"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="min-h-0 overflow-y-auto">
            <SegmentedNoteContent
              content={previewNote.content || "*Empty note*"}
              onFileClick={handleFileClick}
              onEntityClick={handleEntityClick}
              kb={currentKB}
              proseClassName="prose-orb"
            />
          </div>
        </dialog>
      )}

      {filePreview && (
        <FilePreviewModal
          filePreview={filePreview}
          currentKB={currentKB}
          onClose={() => setFilePreview(null)}
          onReveal={() => void handleRevealPreviewFile()}
        />
      )}
    </div>
  );
}
