import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useRef,
  type ReactNode,
} from "react";
import { api } from "@/lib/api";
import type { ChatConversation } from "@/lib/types";

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  thinking?: string;
}

interface ChatContextValue {
  messages: Message[];
  conversations: ChatConversation[];
  activeConversationId: string | null;
  isLoading: boolean;
  isLoadingConversations: boolean;
  loadingStage: string | null;
  loadingModel: string | null;
  sendMessage: (text: string, kb: string) => void;
  loadConversations: (kb: string) => Promise<void>;
  selectConversation: (conversationId: string, kb: string) => Promise<void>;
  startNewConversation: () => void;
  deleteActiveConversation: (kb: string) => Promise<void>;
  initializeForKb: (kb: string) => Promise<void>;
}

const ChatContext = createContext<ChatContextValue>({
  messages: [],
  conversations: [],
  activeConversationId: null,
  isLoading: false,
  isLoadingConversations: false,
  loadingStage: null,
  loadingModel: null,
  sendMessage: () => {},
  loadConversations: async () => {},
  selectConversation: async () => {},
  startNewConversation: () => {},
  deleteActiveConversation: async () => {},
  initializeForKb: async () => {},
});

const POLL_INTERVAL_MS = 1000;
const POLL_MAX_ATTEMPTS = 600; // ~10 minutes
const POLL_MAX_CONSECUTIVE_ERRORS = 8;

function toMessage(record: {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking?: string | null;
  created_at?: string | null;
}): Message {
  return {
    id: record.id,
    role: record.role,
    content: record.content,
    timestamp: record.created_at ? new Date(record.created_at) : new Date(),
    thinking: record.thinking || undefined,
  };
}

export function ChatProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<
    string | null
  >(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingConversations, setIsLoadingConversations] = useState(false);
  const [loadingStage, setLoadingStage] = useState<string | null>(null);
  const [loadingModel, setLoadingModel] = useState<string | null>(null);

  const activeConversationIdRef = useRef<string | null>(null);
  const pollCleanupRef = useRef<(() => void) | null>(null);
  const sendGenerationRef = useRef(0);

  useEffect(() => {
    activeConversationIdRef.current = activeConversationId;
  }, [activeConversationId]);

  useEffect(() => {
    return () => {
      pollCleanupRef.current?.();
      pollCleanupRef.current = null;
    };
  }, []);

  const loadConversations = useCallback(async (kb: string) => {
    setIsLoadingConversations(true);
    try {
      const rows = await api.listChatConversations(kb);
      setConversations(rows);
    } catch {
      setConversations([]);
    } finally {
      setIsLoadingConversations(false);
    }
  }, []);

  const selectConversation = useCallback(
    async (conversationId: string, _kb: string) => {
      try {
        const rows = await api.getChatMessages(conversationId);
        setActiveConversationId(conversationId);
        setMessages(rows.map(toMessage));
      } catch {
        setActiveConversationId(conversationId);
        setMessages([]);
      }
    },
    [],
  );

  const startNewConversation = useCallback(() => {
    setActiveConversationId(null);
    setMessages([]);
  }, []);

  const deleteActiveConversation = useCallback(
    async (kb: string) => {
      if (!activeConversationId) {
        startNewConversation();
        return;
      }
      try {
        await api.deleteChatConversation(activeConversationId);
      } catch {
        return;
      }
      const remaining = conversations.filter(
        (c) => c.id !== activeConversationId,
      );
      setConversations(remaining);
      if (remaining.length > 0) {
        await selectConversation(remaining[0].id, kb);
      } else {
        startNewConversation();
      }
      await loadConversations(kb);
    },
    [
      activeConversationId,
      conversations,
      loadConversations,
      selectConversation,
      startNewConversation,
    ],
  );

  const initializeForKb = useCallback(async (kb: string) => {
    setIsLoadingConversations(true);
    try {
      const rows = await api.listChatConversations(kb);
      setConversations(rows);
      if (rows.length > 0) {
        const rowsMessages = await api.getChatMessages(rows[0].id);
        setActiveConversationId(rows[0].id);
        setMessages(rowsMessages.map(toMessage));
      } else {
        setActiveConversationId(null);
        setMessages([]);
      }
    } catch {
      setConversations([]);
      setActiveConversationId(null);
      setMessages([]);
    } finally {
      setIsLoadingConversations(false);
    }
  }, []);

  const sendMessage = useCallback(
    (text: string, kb: string) => {
      if (!text.trim() || isLoading) return;

      pollCleanupRef.current?.();
      const generation = ++sendGenerationRef.current;

      const userMessage: Message = {
        id: `local-user-${Date.now()}`,
        role: "user",
        content: text,
        timestamp: new Date(),
      };

      setMessages((prev) => [...prev, userMessage]);
      setIsLoading(true);
      setLoadingStage("Starting chat request");
      setLoadingModel(null);

      const requestId =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
      let interval: number | null = null;
      let conversationId = activeConversationIdRef.current;
      let attempts = 0;
      let consecutiveErrors = 0;
      let finished = false;

      const stopPolling = () => {
        if (interval !== null) {
          window.clearInterval(interval);
          interval = null;
        }
        if (pollCleanupRef.current) {
          pollCleanupRef.current = null;
        }
      };

      const isStale = () => generation !== sendGenerationRef.current;

      const fail = () => {
        if (finished || isStale()) return;
        finished = true;
        stopPolling();
        const errorMessage: Message = {
          id: `local-error-${Date.now()}`,
          role: "assistant",
          content: "Sorry, I encountered an error. Please try again.",
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, errorMessage]);
        setIsLoading(false);
        setLoadingStage(null);
        setLoadingModel(null);
      };

      const complete = async (
        answer?: string,
        thinking?: string | null,
        resultConversationId?: string,
      ) => {
        if (finished || isStale()) return;
        finished = true;
        stopPolling();
        const assistantMessage: Message = {
          id: `local-assistant-${Date.now()}`,
          role: "assistant",
          content: answer || "I couldn't generate a response.",
          timestamp: new Date(),
          thinking: thinking || undefined,
        };
        setMessages((prev) => [...prev, assistantMessage]);
        if (resultConversationId) {
          setActiveConversationId(resultConversationId);
          conversationId = resultConversationId;
        }
        setIsLoading(false);
        setLoadingStage(null);
        setLoadingModel(null);
        await loadConversations(kb);
        if (isStale()) return;
        if (conversationId) {
          try {
            const rows = await api.getChatMessages(conversationId);
            if (!isStale()) setMessages(rows.map(toMessage));
          } catch {
            // Keep optimistic messages if refresh fails.
          }
        }
      };

      const poll = () => {
        if (finished || isStale()) {
          stopPolling();
          return;
        }
        attempts += 1;
        if (attempts > POLL_MAX_ATTEMPTS) {
          fail();
          return;
        }
        api
          .getChatStatus(requestId)
          .then((status) => {
            if (finished || isStale()) return;
            consecutiveErrors = 0;
            setLoadingStage(status.stage);
            setLoadingModel(status.model ?? null);
            if (status.conversation_id) {
              conversationId = status.conversation_id;
              setActiveConversationId(status.conversation_id);
            }
            if (!status.done) return;
            if (status.error) {
              fail();
              return;
            }
            void complete(
              status.result?.answer,
              status.result?.thinking,
              status.result?.conversation_id || status.conversation_id,
            );
          })
          .catch(() => {
            if (finished || isStale()) return;
            consecutiveErrors += 1;
            if (consecutiveErrors >= POLL_MAX_CONSECUTIVE_ERRORS) {
              fail();
            }
          });
      };

      pollCleanupRef.current = stopPolling;

      api
        .startChat(text, kb, requestId, activeConversationIdRef.current)
        .then((started) => {
          if (finished || isStale()) return;
          if (started.conversation_id) {
            conversationId = started.conversation_id;
            setActiveConversationId(started.conversation_id);
          }
          interval = window.setInterval(poll, POLL_INTERVAL_MS);
          poll();
        })
        .catch(() => {
          fail();
        });
    },
    [isLoading, loadConversations],
  );

  return (
    <ChatContext.Provider
      value={{
        messages,
        conversations,
        activeConversationId,
        isLoading,
        isLoadingConversations,
        loadingStage,
        loadingModel,
        sendMessage,
        loadConversations,
        selectConversation,
        startNewConversation,
        deleteActiveConversation,
        initializeForKb,
      }}
    >
      {children}
    </ChatContext.Provider>
  );
}

export function useChat() {
  return useContext(ChatContext);
}
