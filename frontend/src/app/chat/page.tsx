"use client";

import React, { useState, useRef, useEffect, useCallback, useMemo } from "react";
import {
  Send,
  User,
  Loader2,
  Sparkles,
  Database,
  Network,
  Cpu,
  X,
  FileText,
  Trash2,
  Search,
  Layers,
  ChevronDown,
  ChevronUp,
  MessageSquarePlus,
  MessagesSquare,
  Download,
  FolderOpen,
} from "lucide-react";
import Image from "next/image";
import { motion, AnimatePresence } from "framer-motion";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/lib/api";
import { cn, encodeFileUrl, isAudioUrl, isImageUrl, isVideoUrl, resolveFileUrl } from "@/lib/utils";
import { BlobMediaPlayer } from "@/components/blob-media-player";
import {
  isDesktopApp,
  revealInFolder,
  revealInFolderLabel,
} from "@/lib/desktop";
import { ShaderBackground } from "@/components/shader-background";
import { useKB } from "@/lib/kb-context";
import { useChat } from "@/lib/chat-context";
import type { Message } from "@/lib/chat-context";
import { SegmentedNoteContent } from "@/components/segmented-note-content";
import { EntityDetailPanel } from "@/components/entity-detail-panel";
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

/** Prose classes shared by the two inline message renderers. */
const PROSE_CLASSNAME =
  "prose prose-invert max-w-none prose-headings:font-bold prose-headings:text-white prose-h1:text-2xl prose-h2:text-xl prose-h3:text-lg prose-p:my-2 prose-p:leading-relaxed prose-p:text-white/90 prose-strong:text-white prose-em:text-white/90 prose-a:text-purple-400 prose-code:text-pink-400 prose-ul:text-white/90 prose-ol:text-white/90 prose-li:text-white/90";

/**
 * Returns a react-markdown `components` map that handles:
 * - entity:// links → clickable entity highlight button
 * - 📎/🎤 links → file/audio attachment buttons
 * - all other anchors → normal <a>
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
            onClick={() => onEntityClick(nodeId, text)}
            className="inline cursor-pointer rounded px-0.5 font-medium text-blue-400 underline decoration-dashed underline-offset-2 transition-colors hover:text-blue-300"
          >
            {text}
          </button>
        );
      }
      if (href && (text.startsWith("📎") || text.startsWith("🎤"))) {
        const filename = text.replace(/^[📎🎤]\s*/, "");
        return (
          <button
            onClick={() => handleFileClick(href, filename)}
            className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg bg-purple-500/10 border border-purple-500/30 text-purple-300 hover:bg-purple-500/20 transition-all text-sm no-underline"
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

/** Renders a single assistant message with entity scanning + highlighting. */
function AssistantMessageBody({
  message,
  kb,
  scanEnabled,
  onEntityClick,
  onFileClick,
  expandedThinking,
  onToggleThinking,
}: {
  message: Message;
  kb: string;
  /** Whether this message may issue an entity scan (recent messages only). */
  scanEnabled: boolean;
  onEntityClick: (nodeId: string, name: string) => void;
  onFileClick: (url: string, filename: string) => void;
  expandedThinking: Set<string>;
  onToggleThinking: (id: string) => void;
}) {
  const scannedEntities = useScannedEntities(message.content, kb, {
    enabled: scanEnabled,
    cacheKey: message.id,
  });

  const processContent = useCallback(
    (text: string) => {
      if (!scannedEntities.length) return text;
      return injectEntityLinks(text, scannedEntities);
    },
    [scannedEntities],
  );

  const linkRenderer = useMemo(
    () => makeLinkRenderer(onFileClick, onEntityClick),
    [onFileClick, onEntityClick],
  );

  const refMatch = message.content.match(/###?\s*References[:\s]*\n([\s\S]+?)$/i);

  return (
    <>
      {/* Thinking dropdown */}
      {message.thinking && (
        <div className="mb-3">
          <button
            onClick={() => onToggleThinking(message.id)}
            className="flex items-center gap-1.5 text-xs text-purple-400/80 hover:text-purple-300 transition-colors"
          >
            {expandedThinking.has(message.id) ? (
              <ChevronUp className="h-3.5 w-3.5" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5" />
            )}
            <span>Model thinking</span>
          </button>
          <AnimatePresence initial={false}>
            {expandedThinking.has(message.id) && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="overflow-hidden"
              >
                <div className="mt-2 rounded-lg border border-purple-500/20 bg-purple-500/5 px-3 py-2">
                  <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-purple-300/70">
                    {message.thinking}
                  </pre>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      {/* Message body */}
      {refMatch ? (
        <>
          <div className={PROSE_CLASSNAME}>
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={linkRenderer} urlTransform={urlTransform}>
              {processContent(message.content.substring(0, refMatch.index))}
            </ReactMarkdown>
          </div>
          <div className="mt-4 pt-3 border-t border-white/10">
            <p className="text-sm font-semibold text-white/60 mb-2">References:</p>
            <div className="flex flex-wrap gap-2">
              {refMatch[1]
                .split("\n")
                .map((t) => t.trim())
                .filter((t) => t && !t.match(/^[\*\s]*$/))
                .map((title, i) => {
                  const linkMatch = title.match(/\[([^\]]+)\]\(\/notes\/([^)]+)\)/);
                  if (!linkMatch) return null;
                  return (
                    <button
                      key={i}
                      onClick={() => {
                        const noteId = linkMatch[2];
                        api.getNote(noteId, kb).then((n) =>
                          (window as unknown as { __chatSetPreview?: (n: NotePreview) => void }).__chatSetPreview?.({
                            id: n.id,
                            title: n.title || "Untitled",
                            content: n.content,
                          })
                        ).catch(console.error);
                      }}
                      className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg bg-purple-500/10 border border-purple-500/30 text-purple-300 hover:bg-purple-500/20 transition-all text-sm no-underline"
                    >
                      <FileText className="h-3.5 w-3.5" />
                      {linkMatch[1]}
                    </button>
                  );
                })}
            </div>
          </div>
        </>
      ) : (
        <div className={PROSE_CLASSNAME}>
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={linkRenderer} urlTransform={urlTransform}>
            {processContent(message.content)}
          </ReactMarkdown>
        </div>
      )}
    </>
  );
}

export default function ChatPage() {
  const { currentKB, currentKBName, isHydrated } = useKB();
  const {
    messages,
    conversations,
    activeConversationId,
    isLoading,
    isLoadingConversations,
    loadingStage,
    loadingModel,
    sendMessage,
    selectConversation,
    startNewConversation,
    deleteActiveConversation,
    initializeForKb,
  } = useChat();
  const [input, setInput] = useState("");
  const [previewNote, setPreviewNote] = useState<NotePreview | null>(null);
  const [filePreview, setFilePreview] = useState<FilePreview | null>(null);
  const [expandedThinking, setExpandedThinking] = useState<Set<string>>(new Set());
  const [entityPanelNodeId, setEntityPanelNodeId] = useState<string | null>(null);
  const [entityPanelName, setEntityPanelName] = useState<string | undefined>();
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Expose setPreviewNote globally so AssistantMessageBody can trigger it without prop drilling
  useEffect(() => {
    (window as unknown as { __chatSetPreview?: unknown }).__chatSetPreview = (n: NotePreview) =>
      setPreviewNote(n);
    return () => { delete (window as unknown as { __chatSetPreview?: unknown }).__chatSetPreview; };
  }, []);

  const handleEntityClick = useCallback((nodeId: string, name: string) => {
    setEntityPanelNodeId(nodeId);
    setEntityPanelName(name);
  }, []);

  // Only the last few assistant messages may trigger entity scans; older
  // ones render highlight-free (or from the module-level scan cache).
  const scannableMessageIds = useMemo(() => {
    const ids = new Set<string>();
    for (
      let i = messages.length - 1;
      i >= 0 && ids.size < ENTITY_SCAN_RECENT_LIMIT;
      i--
    ) {
      if (messages[i].role === "assistant") ids.add(messages[i].id);
    }
    return ids;
  }, [messages]);
  const [greeting] = useState(() => {
    const hour = new Date().getHours();
    if (hour < 12) return "Good morning!";
    if (hour < 18) return "Good afternoon!";
    return "Good evening!";
  });

  useEffect(() => {
    if (!isHydrated) return;
    void initializeForKb(currentKB);
  }, [currentKB, initializeForKb, isHydrated]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;
    sendMessage(input, currentKB);
    setInput("");
  };

  const _handleNoteReference = async (noteId: string) => {
    try {
      const fullNote = await api.getNote(noteId, currentKB);
      setPreviewNote({
        id: fullNote.id,
        title: fullNote.title || "Untitled",
        content: fullNote.content,
      });
    } catch (error) {
      console.error("Error fetching note:", error);
    }
  };

  const handleFileClick = (url: string, filename: string) => {
    const resolvedUrl = encodeFileUrl(resolveFileUrl(url, currentKB));
    let type: FilePreview["type"] = "other";

    if (isImageUrl(resolvedUrl) || isImageUrl(filename)) {
      type = "image";
    } else if (/\.pdf(\?|$)/i.test(resolvedUrl) || /\.pdf$/i.test(filename)) {
      type = "pdf";
    } else if (isVideoUrl(resolvedUrl) || isVideoUrl(filename)) {
      type = "video";
    } else if (isAudioUrl(resolvedUrl) || isAudioUrl(filename)) {
      type = "audio";
    }

    setFilePreview({ url: resolvedUrl, filename, type });
  };

  const handleRevealPreviewFile = async () => {
    if (!filePreview) return;
    try {
      const { local_path } = await api.resolveVaultLocalPath(
        filePreview.url,
        currentKB,
      );
      const ok = await revealInFolder(local_path);
      if (!ok) {
        window.open(filePreview.url, "_blank", "noopener,noreferrer");
      }
    } catch (error) {
      console.error("Reveal failed:", error);
      alert("Could not reveal this file on disk.");
    }
  };

  const handleDeleteChat = () => {
    if (!activeConversationId && messages.length === 0) return;
    if (window.confirm("Delete this chat? This cannot be undone.")) {
      void deleteActiveConversation(currentKB);
    }
  };

  const handleNewChat = () => {
    startNewConversation();
  };

  const suggestions = [
    "What are my recent thoughts?",
    "Show me my tasks",
    "Summarize my notes",
    "What concepts am I exploring?",
  ];

  return (
    <div className="relative flex h-screen w-full flex-col overflow-hidden bg-black">
      {/* Animated background */}
      <ShaderBackground />

      {/* Header */}
      <div className="relative z-10 border-b border-white/10 bg-black/50 backdrop-blur-xl">
        <div className="mx-auto max-w-4xl px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 overflow-hidden rounded-xl ring-1 ring-white/15">
                <Image
                  src="/logo-icon.png"
                  alt="Orb"
                  width={40}
                  height={40}
                  loading="eager"
                  className="h-full w-full object-cover"
                />
              </div>
              <div>
                <h1 className="text-xl font-bold text-white">Orb</h1>
                <p className="text-xs text-white/60">Your Personal Brain</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleNewChat}
                className="flex items-center gap-1.5 rounded-lg border border-purple-500/30 bg-purple-500/10 px-3 py-1.5 text-xs text-purple-300 transition-all hover:bg-purple-500/20"
              >
                <MessageSquarePlus className="h-3 w-3" />
                New Chat
              </button>
              {(activeConversationId || messages.length > 0) && (
                <button
                  onClick={handleDeleteChat}
                  className="flex items-center gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs text-red-400 transition-all hover:bg-red-500/20"
                >
                  <Trash2 className="h-3 w-3" />
                  Delete Chat
                </button>
              )}
              {activeConversationId && (
                <button
                  onClick={async () => {
                    try {
                      const md = await api.exportChat(activeConversationId, "markdown");
                      const blob = new Blob([md], { type: "text/markdown" });
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement("a");
                      a.href = url;
                      a.download = `chat-${activeConversationId.slice(0, 8)}.md`;
                      a.click();
                      URL.revokeObjectURL(url);
                    } catch {
                      /* ignore */
                    }
                  }}
                  className="flex items-center gap-1.5 rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-white/70 transition-all hover:bg-white/10"
                >
                  <Download className="h-3 w-3" />
                  Export
                </button>
              )}
              {conversations.length > 0 && (
                <div className="relative">
                  <select
                    value={activeConversationId || ""}
                    onChange={(e) => {
                      const id = e.target.value;
                      if (id) void selectConversation(id, currentKB);
                      else startNewConversation();
                    }}
                    disabled={isLoadingConversations}
                    className="max-w-[220px] appearance-none rounded-lg border border-white/10 bg-white/5 py-1.5 pl-8 pr-8 text-xs text-white/80 backdrop-blur-xl transition-all hover:bg-white/10 focus:border-purple-500/50 focus:outline-none"
                  >
                    <option value="">New chat</option>
                    {conversations.map((conv) => (
                      <option key={conv.id} value={conv.id}>
                        {conv.title}
                      </option>
                    ))}
                  </select>
                  <MessagesSquare className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-white/50" />
                  <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-white/50" />
                </div>
              )}
              {/* Active KB badge */}
              <div className="flex items-center gap-1 rounded-full border border-purple-500/30 bg-purple-500/10 px-3 py-1.5">
                <Database className="h-3 w-3 text-purple-400" />
                <span className="text-xs text-purple-300 font-medium">{currentKBName}</span>
              </div>
              <div className="flex items-center gap-1 rounded-full border border-white/10 bg-white/5 px-3 py-1.5">
                <Database className="h-3 w-3 text-green-400" />
                <span className="text-xs text-white/70">SQLite</span>
              </div>
              <div className="flex items-center gap-1 rounded-full border border-white/10 bg-white/5 px-3 py-1.5">
                <Network className="h-3 w-3 text-blue-400" />
                <span className="text-xs text-white/70">Kuzu</span>
              </div>
              <div className="flex items-center gap-1 rounded-full border border-white/10 bg-white/5 px-3 py-1.5">
                <Layers className="h-3 w-3 text-cyan-400" />
                <span className="text-xs text-white/70">Qdrant</span>
              </div>
              <div className="flex items-center gap-1 rounded-full border border-white/10 bg-white/5 px-3 py-1.5">
                <Search className="h-3 w-3 text-yellow-400" />
                <span className="text-xs text-white/70">Meilisearch</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Chat Messages */}
      <div className="relative z-10 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-4xl px-6 py-8">
          {messages.length === 0 ? (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              className="flex flex-col items-center justify-center py-12"
            >
              <Sparkles className="mb-4 h-12 w-12 text-purple-400" />
              <h2 className="mb-2 text-2xl font-bold text-white">{greeting}</h2>
              <p className="mb-8 text-center text-white/60">
                Ask me anything about your notes, documents, and knowledge graph
              </p>
              <div className="flex flex-wrap justify-center gap-2">
                {suggestions.map((suggestion, index) => (
                  <button
                    key={index}
                    onClick={() => setInput(suggestion)}
                    className="rounded-full border border-white/10 bg-white/5 px-4 py-2 text-sm text-white/80 backdrop-blur-xl transition-all hover:border-purple-500/50 hover:bg-white/10"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </motion.div>
          ) : (
            <div className="space-y-6">
              <AnimatePresence>
                {messages.map((message) => (
                  <motion.div
                    key={message.id}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    className={cn(
                      "flex gap-4",
                      message.role === "user" ? "justify-end" : "justify-start",
                    )}
                  >
                    {message.role === "assistant" && (
                      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-linear-to-br from-purple-500 to-pink-500">
                        <Sparkles className="h-4 w-4 text-white" />
                      </div>
                    )}
                    <div
                      className={cn(
                        "max-w-[80%] rounded-2xl px-4 py-3",
                        message.role === "user"
                          ? "bg-linear-to-br from-purple-500 to-pink-500 text-white"
                          : "border border-white/10 bg-white/5 text-white backdrop-blur-xl",
                      )}
                    >
                      {message.role === "assistant" ? (
                        <AssistantMessageBody
                          message={message}
                          kb={currentKB}
                          scanEnabled={scannableMessageIds.has(message.id)}
                          onEntityClick={handleEntityClick}
                          onFileClick={handleFileClick}
                          expandedThinking={expandedThinking}
                          onToggleThinking={(id) =>
                            setExpandedThinking((prev) => {
                              const next = new Set(prev);
                              if (next.has(id)) next.delete(id); else next.add(id);
                              return next;
                            })
                          }
                        />
                      ) : (
                        <p className="whitespace-pre-wrap">{message.content}</p>
                      )}

                    </div>
                    {message.role === "user" && (
                      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/10">
                        <User className="h-4 w-4 text-white" />
                      </div>
                    )}
                  </motion.div>
                ))}
              </AnimatePresence>
              {isLoading && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className="flex gap-4"
                >
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-linear-to-br from-purple-500 to-pink-500">
                    <Loader2 className="h-4 w-4 animate-spin text-white" />
                  </div>
                  <div className="rounded-2xl border border-white/10 bg-white/5 px-4 py-3 backdrop-blur-xl">
                    <div className="text-sm text-white/70">
                      {loadingStage || "Thinking..."}
                    </div>
                    {loadingModel && (
                      <div className="mt-0.5 text-xs text-purple-300/80">
                        Using {loadingModel}
                      </div>
                    )}
                  </div>
                </motion.div>
              )}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>
      </div>

      {/* Input */}
      <div className="relative z-10 border-t border-white/10 bg-black/50 backdrop-blur-xl">
        <div className="mx-auto max-w-4xl px-6 py-4">
          <form onSubmit={handleSubmit} className="flex gap-2">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask me anything..."
              disabled={isLoading}
              className="flex-1 rounded-full border border-white/10 bg-white/5 px-6 py-3 text-white placeholder-white/40 backdrop-blur-xl transition-all focus:border-purple-500/50 focus:outline-none disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={!input.trim() || isLoading}
              className="flex h-12 w-12 items-center justify-center rounded-full bg-linear-to-br from-purple-500 to-pink-500 text-white transition-all hover:scale-105 disabled:opacity-50 disabled:hover:scale-100"
              title="Send message"
              aria-label="Send message"
            >
              <Send className="h-5 w-5" />
            </button>
          </form>
          <div className="mt-3 flex items-center justify-center gap-2 text-xs text-white/40">
            <span>Powered by</span>
            <span className="font-medium text-purple-400">Gemma3 4B</span>
            <span>•</span>
            <span className="font-medium text-pink-400">Qwen3 Embedding</span>
            <span>•</span>
            <span className="font-medium text-emerald-400">Qwen3 Reranker</span>
            <span>•</span>
            <span className="font-medium text-teal-400">Florence 2</span>
            <span>•</span>
            <span className="font-medium text-amber-400">Whisper V3</span>
          </div>
        </div>
      </div>

      {/* Note Preview Modal */}
      <AnimatePresence>
        {previewNote && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm"
            onClick={() => setPreviewNote(null)}
          >
            <motion.div
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.9, opacity: 0 }}
              onClick={(e) => e.stopPropagation()}
              className="relative mx-4 max-h-[80vh] w-full max-w-3xl overflow-hidden rounded-2xl border border-white/10 bg-black/95 shadow-2xl backdrop-blur-xl"
            >
              <div className="flex items-center justify-between border-b border-white/10 px-6 py-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-linear-to-br from-purple-500 to-pink-500">
                    <FileText className="h-5 w-5 text-white" />
                  </div>
                  <h2 className="text-xl font-bold text-white">
                    {previewNote.title}
                  </h2>
                </div>
                <button
                  onClick={() => setPreviewNote(null)}
                  className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/5 text-white/60 transition-all hover:bg-white/10 hover:text-white"
                  title="Close preview"
                  aria-label="Close preview"
                >
                  <X className="h-5 w-5" />
                </button>
              </div>
              <div className="max-h-[calc(80vh-80px)] overflow-y-auto p-6">
                <SegmentedNoteContent
                  content={previewNote.content || "*Empty note*"}
                  onFileClick={handleFileClick}
                  onEntityClick={handleEntityClick}
                  kb={currentKB}
                  proseClassName="prose prose-invert max-w-none prose-headings:font-bold prose-headings:text-white prose-h1:text-4xl prose-h1:mt-6 prose-h1:mb-4 prose-h2:text-3xl prose-h2:mt-5 prose-h2:mb-3 prose-h3:text-2xl prose-h3:mt-4 prose-h3:mb-3 prose-h4:text-xl prose-h4:mt-3 prose-h4:mb-2 prose-p:leading-relaxed prose-p:text-white/90 prose-p:my-3 prose-strong:text-white prose-strong:font-bold prose-em:text-white/90 prose-em:italic prose-a:text-purple-400 prose-a:underline hover:prose-a:text-purple-300 prose-code:text-pink-400 prose-code:bg-white/10 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:before:content-[''] prose-code:after:content-[''] prose-pre:bg-white/5 prose-pre:border prose-pre:border-white/10 prose-blockquote:border-l-4 prose-blockquote:border-purple-500/50 prose-blockquote:text-white/80 prose-blockquote:pl-4 prose-blockquote:italic prose-ul:text-white/90 prose-ul:my-3 prose-ol:text-white/90 prose-ol:my-3 prose-li:text-white/90 prose-li:my-1"
                />
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Entity Detail Panel */}
      <EntityDetailPanel
        nodeId={entityPanelNodeId}
        name={entityPanelName}
        kb={currentKB}
        onClose={() => setEntityPanelNodeId(null)}
      />

      {/* File Preview Modal */}
      <AnimatePresence>
        {filePreview && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm"
            onClick={() => setFilePreview(null)}
          >
            <motion.div
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.9, opacity: 0 }}
              onClick={(e) => e.stopPropagation()}
              className="relative mx-4 max-h-[90vh] w-full max-w-6xl overflow-hidden rounded-2xl border border-white/10 bg-black/95 shadow-2xl backdrop-blur-xl"
            >
              <div className="flex items-center justify-between border-b border-white/10 px-6 py-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-linear-to-br from-purple-500 to-pink-500">
                    <FileText className="h-5 w-5 text-white" />
                  </div>
                  <h2 className="text-lg font-semibold text-white">
                    {filePreview.filename}
                  </h2>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleRevealPreviewFile();
                    }}
                    className="flex h-8 items-center gap-1.5 rounded-lg bg-purple-500/20 px-3 text-sm text-purple-300 transition-all hover:bg-purple-500/30"
                    title={
                      isDesktopApp()
                        ? revealInFolderLabel()
                        : "Open file"
                    }
                  >
                    <FolderOpen className="h-4 w-4" />
                    {isDesktopApp() ? revealInFolderLabel() : "Open"}
                  </button>
                  <button
                    onClick={() => setFilePreview(null)}
                    className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/5 text-white/60 transition-all hover:bg-white/10 hover:text-white"
                    title="Close file preview"
                    aria-label="Close file preview"
                  >
                    <X className="h-5 w-5" />
                  </button>
                </div>
              </div>
              <div className="max-h-[calc(90vh-80px)] overflow-y-auto p-6">
                {filePreview.type === "image" && (
                  /* eslint-disable-next-line @next/next/no-img-element */
                  <img
                    src={filePreview.url}
                    alt={filePreview.filename}
                    className="max-h-full max-w-full rounded-lg"
                  />
                )}
                {filePreview.type === "pdf" && (
                  <iframe
                    src={filePreview.url}
                    className="h-[calc(90vh-160px)] w-full rounded-lg"
                    title={filePreview.filename}
                  />
                )}
                {filePreview.type === "video" && (
                  <div className="flex min-h-50 items-center justify-center">
                    <BlobMediaPlayer
                      url={filePreview.url}
                      kind="video"
                      className="max-h-[70vh] max-w-full rounded-lg bg-black"
                    />
                  </div>
                )}
                {filePreview.type === "audio" && (
                  <div className="flex min-h-50 items-center justify-center">
                    <BlobMediaPlayer
                      url={filePreview.url}
                      kind="audio"
                      className="w-full max-w-2xl"
                    />
                  </div>
                )}
                {filePreview.type === "other" && (
                  <div className="flex min-h-50 flex-col items-center justify-center gap-4 text-white/60">
                    <FileText className="h-16 w-16" />
                    <p>Preview not available for this file type</p>
                    <button
                      type="button"
                      onClick={() => void handleRevealPreviewFile()}
                      className="rounded-lg bg-purple-500/20 px-4 py-2 text-purple-300 transition-all hover:bg-purple-500/30"
                    >
                      {isDesktopApp()
                        ? revealInFolderLabel()
                        : "Open file"}
                    </button>
                  </div>
                )}
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

    </div>
  );
}
