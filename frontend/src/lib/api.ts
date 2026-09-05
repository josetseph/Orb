import axios from "axios";
import type {
  InspectedModel,
  ModelsPageState,
} from "@/lib/models-types";
import type {
  KBLLMConfig,
  ChatConversation,
  ChatMessageRecord,
  ChatStatus,
  FinanceAccount,
  FinanceBudget,
  FinanceCategory,
  FinanceRecurrence,
  FinanceReport,
  FinanceRule,
  FinanceRuleGroup,
  FinanceSearchResult,
  FinanceSummary,
  FinanceTransaction,
  FinanceWorkspace,
  KnowledgeBase,
  NotesGraphPayload,
  NoteStatus,
  SetupStatus,
} from "@/lib/types";
import { resolveApiBaseUrl } from "@/lib/desktop";

const API_BASE_URL = (process.env.NEXT_PUBLIC_API_URL ?? "/api/v1").replace(/\/$/, "");

/** Merge optional kb into axios query params (omit for default). */
function withKb(
  kb: string,
  params?: Record<string, unknown>,
): Record<string, unknown> | undefined {
  const out: Record<string, unknown> = { ...(params || {}) };
  if (kb && kb !== "default") out.kb = kb;
  return Object.keys(out).length ? out : undefined;
}

/** Path-only kb query for methods that already encode other params in the URL. */
function kbQuery(kb: string): string {
  return kb && kb !== "default" ? `?kb=${encodeURIComponent(kb)}` : "";
}

/** Optional per-request options (cancellation). */
export type RequestOpts = { signal?: AbortSignal };

/** Thin wrappers so each method is one line instead of three. */
const http = {
  get: (path: string, params?: Record<string, unknown>, opts?: RequestOpts) =>
    axios
      .get(`${API_BASE_URL}${path}`, { params, signal: opts?.signal })
      .then((r) => r.data),
  post: (path: string, data?: unknown, opts?: RequestOpts) =>
    axios
      .post(`${API_BASE_URL}${path}`, data, { signal: opts?.signal })
      .then((r) => r.data),
  put: (path: string, data?: unknown) =>
    axios.put(`${API_BASE_URL}${path}`, data).then((r) => r.data),
  patch: (path: string, data?: unknown) =>
    axios.patch(`${API_BASE_URL}${path}`, data).then((r) => r.data),
  del: (path: string) =>
    axios.delete(`${API_BASE_URL}${path}`).then((r) => r.data),
};

/** True when an error is an axios/DOM cancellation (aborted request). */
export function isRequestCancelled(error: unknown): boolean {
  return (
    axios.isCancel(error) ||
    (error instanceof DOMException && error.name === "AbortError")
  );
}

export const api = {
  // ── Chat ────────────────────────────────────────────────────────────────

  async startChat(
    query: string,
    kb = "default",
    requestId?: string,
    conversationId?: string | null,
  ) {
    return http.post(`/chat/async${kbQuery(kb)}`, {
      query,
      request_id: requestId,
      conversation_id: conversationId || undefined,
    });
  },

  async getChatStatus(requestId: string): Promise<ChatStatus> {
    return http.get(`/chat/status/${requestId}`);
  },

  async listChatConversations(kb = "default"): Promise<ChatConversation[]> {
    return http.get(`/chat/conversations`, withKb(kb));
  },

  async getChatMessages(conversationId: string): Promise<ChatMessageRecord[]> {
    return http.get(`/chat/conversations/${conversationId}/messages`);
  },

  async deleteChatConversation(conversationId: string) {
    return http.del(`/chat/conversations/${conversationId}`);
  },

  // ── File storage ─────────────────────────────────────────────────────────

  async upload(file: File, kb = "default") {
    const formData = new FormData();
    formData.append("file", file);
    // Desktop: hit FastAPI directly so large files aren't truncated by the
    // Next.js rewrite proxy (default 10MB → socket hang up / 500).
    const base = await resolveApiBaseUrl(API_BASE_URL);
    const response = await axios.post(
      `${base}/upload${kbQuery(kb)}`,
      formData,
      { timeout: 10 * 60 * 1000 },
    );
    return response.data;
  },

  // ── Notes (vault markdown + SQLite metadata) ─────────────────────────────

  async getNotes(
    search?: string,
    processed?: boolean,
    failed?: boolean,
    kb = "default",
    opts?: RequestOpts,
  ) {
    const params: Record<string, unknown> = {};
    if (search) params.search = search;
    if (processed !== undefined) params.processed = processed;
    if (failed !== undefined) params.failed = failed;
    return http.get(`/notes`, withKb(kb, params), opts);
  },

  async getNote(id: string, kb = "default") {
    return http.get(`/notes/${id}${kbQuery(kb)}`);
  },

  async getNoteStatus(id: string): Promise<NoteStatus> {
    return http.get(`/notes/${id}/status`);
  },

  async createNote(
    content: string,
    created_at?: string,
    kb = "default",
    title?: string,
    folder?: string,
  ) {
    return http.post(`/notes${kbQuery(kb)}`, {
      content,
      created_at,
      title,
      folder: folder || undefined,
    });
  },

  async moveNote(id: string, folder: string, kb = "default") {
    return http.post(`/notes/${id}/move${kbQuery(kb)}`, { folder });
  },

  async moveVaultFile(fromRel: string, toRel: string, kb = "default") {
    return http.post(`/vault/move${kbQuery(kb)}`, {
      from_rel: fromRel,
      to_rel: toRel,
    });
  },

  async deleteVaultFile(relPath: string, kb = "default") {
    return http.post(`/vault/delete${kbQuery(kb)}`, { rel_path: relPath });
  },

  async listVaultFolders(kb = "default"): Promise<{
    folders: string[];
    attachments?: Array<{ name: string; rel_path: string }>;
    media_files?: Array<{ name: string; rel_path: string }>;
    vault_name?: string;
    vault_path?: string;
  }> {
    return http.get(`/vault/folders`, withKb(kb));
  },

  async mkdirVaultFolder(path: string, kb = "default") {
    return http.post(`/vault/mkdir${kbQuery(kb)}`, { path });
  },

  async resolveVaultLocalPath(relOrUrl: string, kb = "default"): Promise<{
    rel_path: string;
    local_path: string;
    vault_path: string;
    exists: boolean;
  }> {
    return http.get(`/vault/local-path`, withKb(kb, { rel: relOrUrl }));
  },

  async updateNote(
    id: string,
    content: string,
    created_at?: string,
    kb = "default",
    title?: string,
  ) {
    return http.put(`/notes/${id}${kbQuery(kb)}`, { content, created_at, title });
  },

  /**
   * Best-effort save during window unload. `fetch(..., { keepalive: true })`
   * survives the window closing but caps the body at ~64KB, so large notes
   * fall back to a plain PUT (which may be killed with the window).
   */
  updateNoteOnUnload(id: string, content: string, kb = "default", title?: string) {
    const body = JSON.stringify({ content, title });
    if (body.length < 60_000 && typeof fetch === "function") {
      void fetch(`${API_BASE_URL}/notes/${id}${kbQuery(kb)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body,
        keepalive: true,
      }).catch(() => {});
      return;
    }
    void http.put(`/notes/${id}${kbQuery(kb)}`, { content, title }).catch(() => {});
  },

  /** Ingest an existing note into the given KB (default KB if omitted). */
  async ingestNote(id: string, kb = "default") {
    return http.post(`/notes/${id}/ingest${kbQuery(kb)}`);
  },

  /** Delete note from vault/SQLite and from the given KB's graph. */
  async deleteNote(id: string, kb = "default") {
    return http.del(`/notes/${id}${kbQuery(kb)}`);
  },

  /** Batch-delete notes (vault + SQLite + graph cleanup). Max 100. */
  async batchDeleteNotes(
    ids: string[],
    kb = "default",
  ): Promise<{
    deleted: string[];
    failed: Array<{ id: string; error: string }>;
    deleted_count: number;
    failed_count: number;
  }> {
    return http.post(`/notes/batch-delete${kbQuery(kb)}`, { ids });
  },

  // ── 3D Exploration graph ──────────────────────────────────────────────────

  async getGraph3DFull(kb = "default", opts?: RequestOpts): Promise<{
    nodes: Array<{
      node_id: string;
      name: string;
      node_type: string;
      description: string;
      facts: string[];
      community_id?: string;
      x: number;
      y: number;
      z: number;
    }>;
    edges: Array<{
      source: string;
      target: string;
      type: string;
    }>;
  }> {
    return http.get(`/graph/3d/full`, withKb(kb), opts);
  },

  async getNodeDetail(nodeId: string, kb = "default"): Promise<{
    node_id: string;
    name: string;
    node_type: string;
    description: string;
    isolated_contexts: string[];
    facts: string[];
    domain?: string;
    status?: string;
    community_id?: string;
    community_name?: string;
    connections?: {
      node_id: string;
      name: string;
      kind?: string;
      relationship?: string;
      direction?: string;
    }[];
    related_notes?: { note_id: string; name: string }[];
  }> {
    return http.get(
      `/graph/3d/node/${encodeURIComponent(nodeId)}${kbQuery(kb)}`,
    );
  },

  // ── Entity mention autocomplete ───────────────────────────────────────────

  async searchEntities(
    q: string,
    kb = "default",
    limit = 5,
  ): Promise<{ node_id: string; name: string; node_type: string }[]> {
    return http.get("/graph/entities/search", withKb(kb, { q, limit }));
  },

  async scanTextEntities(
    text: string,
    kb = "default",
    opts?: RequestOpts,
  ): Promise<{ node_id: string; name: string; node_type: string }[]> {
    return http.post(`/graph/entities/scan-text${kbQuery(kb)}`, { text }, opts);
  },

  // ── Knowledge-base management ─────────────────────────────────────────────

  async listKBs(): Promise<{ knowledge_bases: KnowledgeBase[] }> {
    return http.get("/kb");
  },

  async createKB(
    name: string,
    vaultPath?: string,
  ): Promise<{ id: string; name: string; vault_path?: string; message: string }> {
    return http.post("/kb", { name, vault_path: vaultPath || undefined });
  },

  async renameKB(id: string, name: string): Promise<{ id: string; name: string }> {
    return http.patch(`/kb/${id}`, { name });
  },

  async deleteKB(id: string): Promise<void> {
    return http.del(`/kb/${id}`);
  },

  async getKBLLM(id: string): Promise<KBLLMConfig> {
    return http.get(`/kb/${id}/llm`);
  },

  /** Empty strings / null clear a field back to "inherit Settings". */
  async updateKBLLM(
    id: string,
    data: {
      provider?: string | null;
      model?: string | null;
      ingestion_model?: string | null;
      /** Only meaningful for provider="openai_compat". */
      base_url?: string | null;
    },
  ): Promise<KBLLMConfig> {
    return http.patch(`/kb/${id}/llm`, data);
  },

  async emptyKB(kb = "default"): Promise<{
    status: string;
    kb_id: string;
    name: string;
    notes_removed: number;
    vault_path: string;
    message: string;
  }> {
    return http.post(`/kb/empty${kbQuery(kb)}`, {});
  },

  async deleteAllNonDefaultKBs(): Promise<{
    removed: Array<{ id: string; name?: string; vault_path?: string }>;
    errors: Array<{ id: string; error: string }>;
    removed_count: number;
    message: string;
  }> {
    return http.post(`/kb/delete-non-default`, {});
  },

  // ── Setup / paths ─────────────────────────────────────────────────────────

  async getSetupStatus(): Promise<SetupStatus> {
    return http.get("/setup/status");
  },

  async getModelCatalog(chatId?: string) {
    return http.get("/setup/model-catalog", chatId ? { chat_id: chatId } : undefined);
  },

  async saveSetupPaths(data: {
    data_dir: string;
    models_dir: string;
    default_vault_path?: string;
    ai_setup_mode?: string;
  }) {
    return http.post("/setup/paths", data);
  },

  async downloadModels(
    includeMultimodal = true,
    chatId?: string,
    opts?: { multimodalOnly?: boolean },
  ) {
    // Model packs are multi-GB; do not use the default axios timeout.
    return axios
      .post(
        `${API_BASE_URL}/setup/download-models`,
        {
          include_multimodal: includeMultimodal,
          chat_id: chatId || undefined,
          multimodal_only: Boolean(opts?.multimodalOnly),
        },
        { timeout: 0 },
      )
      .then((r) => r.data);
  },

  async selectChatModel(chatId: string) {
    return http.post("/setup/select-chat-model", { chat_id: chatId });
  },

  async getMultimodalStatus() {
    return http.get("/setup/multimodal-status");
  },

  // ── Notes graph / vault ───────────────────────────────────────────────────

  async getNotesGraph(kb = "default", opts?: RequestOpts): Promise<NotesGraphPayload> {
    return http.get(`/graph/notes`, withKb(kb), opts);
  },

  async getNoteNeighbors(
    noteId: string,
    kb = "default",
    opts?: RequestOpts,
  ): Promise<NotesGraphPayload> {
    return http.get(
      `/graph/notes/${encodeURIComponent(noteId)}/neighbors${kbQuery(kb)}`,
      undefined,
      opts,
    );
  },

  async getNoteEntitySubgraph(
    text: string,
    kb = "default",
    opts?: RequestOpts,
  ): Promise<NotesGraphPayload> {
    return http.post(`/graph/entities/note-subgraph${kbQuery(kb)}`, { text }, opts);
  },

  async rebuildNotesGraph(kb = "default") {
    return http.post(`/graph/notes/rebuild${kbQuery(kb)}`);
  },

  async reingestVault(kb = "default") {
    return http.post(`/notes/reingest-vault${kbQuery(kb)}`);
  },

  async exportChat(conversationId: string, format: "markdown" | "json" = "markdown") {
    if (format === "json") {
      return http.get(`/chat/conversations/${conversationId}/export`, { format });
    }
    const response = await axios.get(
      `${API_BASE_URL}/chat/conversations/${conversationId}/export`,
      { params: { format }, responseType: "text" },
    );
    return response.data as string;
  },

  // ── Finance ───────────────────────────────────────────────────────────────

  async getFinanceWorkspace(kb = "default") {
    return http.get(`/finance/workspace`, withKb(kb)) as Promise<FinanceWorkspace>;
  },

  async createFinanceWorkspace(currency: string, kb = "default") {
    return http.post(`/finance/workspace${kbQuery(kb)}`, { currency });
  },

  async resetFinanceAdministration(kb = "default"): Promise<{
    status: string;
    kb_id: string;
    message: string;
  }> {
    return http.post(`/finance/reset-administration${kbQuery(kb)}`, {});
  },

  async listFinanceAccounts(kb = "default"): Promise<FinanceAccount[]> {
    return http.get(`/finance/accounts`, withKb(kb));
  },

  async createFinanceAccount(
    data: {
      name: string;
      account_type: string;
      opening_balance?: number;
      currency?: string;
    },
    kb = "default",
  ) {
    return http.post(`/finance/accounts${kbQuery(kb)}`, data);
  },

  async listFinanceTransactions(
    kb = "default",
    accountId?: string,
  ): Promise<FinanceTransaction[]> {
    const params: Record<string, unknown> = {};
    if (accountId) params.account_id = accountId;
    return http.get(`/finance/transactions`, withKb(kb, params));
  },

  async getFinanceSummary(
    kb = "default",
    days = 30,
  ): Promise<FinanceSummary> {
    return http.get(`/finance/summary`, withKb(kb, { days }));
  },

  async getFinanceReport(
    kb = "default",
    start?: string,
    end?: string,
  ): Promise<FinanceReport> {
    const params: Record<string, unknown> = {};
    if (start) params.start = start;
    if (end) params.end = end;
    return http.get(`/finance/report`, withKb(kb, params));
  },

  async listFinanceBudgets(
    kb = "default",
    days = 30,
  ): Promise<FinanceBudget[]> {
    return http.get(`/finance/budgets`, withKb(kb, { days }));
  },

  async createFinanceBudget(
    data: { name: string; amount?: number; currency?: string },
    kb = "default",
  ) {
    return http.post(`/finance/budgets${kbQuery(kb)}`, data);
  },

  async listFinanceCategories(kb = "default"): Promise<FinanceCategory[]> {
    return http.get(`/finance/categories`, withKb(kb));
  },

  async createFinanceCategory(data: { name: string; notes?: string }, kb = "default") {
    return http.post(`/finance/categories${kbQuery(kb)}`, data);
  },

  async deleteFinanceCategory(id: string, kb = "default") {
    return http.del(`/finance/categories/${id}${kbQuery(kb)}`);
  },

  async listFinanceRecurrences(kb = "default"): Promise<FinanceRecurrence[]> {
    return http.get(`/finance/recurrences`, withKb(kb));
  },

  async createFinanceRecurrence(
    data: {
      title: string;
      amount: number;
      type?: string;
      source_id: string;
      destination_id: string;
      description?: string;
      first_date?: string;
      repeat_freq?: string;
    },
    kb = "default",
  ) {
    return http.post(`/finance/recurrences${kbQuery(kb)}`, data);
  },

  async deleteFinanceRecurrence(id: string, kb = "default") {
    return http.del(`/finance/recurrences/${id}${kbQuery(kb)}`);
  },

  async listFinanceRuleGroups(kb = "default"): Promise<FinanceRuleGroup[]> {
    return http.get(`/finance/rule-groups`, withKb(kb));
  },

  async createFinanceRuleGroup(data: { title: string; description?: string }, kb = "default") {
    return http.post(`/finance/rule-groups${kbQuery(kb)}`, data);
  },

  async deleteFinanceRuleGroup(id: string, kb = "default") {
    return http.del(`/finance/rule-groups/${id}${kbQuery(kb)}`);
  },

  async listFinanceRules(kb = "default"): Promise<FinanceRule[]> {
    return http.get(`/finance/rules`, withKb(kb));
  },

  async createFinanceRule(
    data: {
      title: string;
      rule_group_id: string;
      trigger_type?: string;
      trigger_value: string;
      action_type?: string;
      action_value: string;
      trigger?: string;
      description?: string;
    },
    kb = "default",
  ) {
    return http.post(`/finance/rules${kbQuery(kb)}`, data);
  },

  async deleteFinanceRule(id: string, kb = "default") {
    return http.del(`/finance/rules/${id}${kbQuery(kb)}`);
  },

  async searchFinance(
    query: string,
    kind: "transactions" | "accounts" = "transactions",
    kb = "default",
  ): Promise<FinanceSearchResult> {
    return http.get(`/finance/search`, withKb(kb, { query, kind }));
  },

  async createFinanceTransaction(
    data: {
      date?: string;
      description: string;
      amount: number;
      account_id: string;
      type?: string;
      transfer_account_id?: string;
      counterparty_name?: string;
      category?: string;
      budget_id?: string;
      currency?: string;
    },
    kb = "default",
  ) {
    return http.post(`/finance/transactions${kbQuery(kb)}`, data);
  },

  async deleteFinanceTransaction(id: string, kb = "default") {
    return http.del(`/finance/transactions/${id}${kbQuery(kb)}`);
  },

  // ── LLM runtime settings ──────────────────────────────────────────────────

  async getLLMSettings(): Promise<{ provider: string; model: string; ingestion_model: string; base_url: string }> {
    return http.get("/settings");
  },

  async updateLLMSettings(data: {
    provider?: string;
    model?: string;
    ingestion_model?: string;
    base_url?: string;
  }): Promise<{ provider: string; model: string; ingestion_model: string; base_url: string }> {
    return http.patch("/settings", data);
  },

  // ── Models page (one read for every model control) ────────────────────────

  async getModelsPage(kb = "default"): Promise<ModelsPageState> {
    return http.get("/models", withKb(kb));
  },

  /** Describe a browsed-to path before saving it, so errors are specific. */
  async inspectModelPath(path: string): Promise<InspectedModel> {
    return http.post("/models/inspect", { path });
  },

  // ── Cloud credentials (write-only; keys are never returned) ───────────────

  async getCredentials(): Promise<{
    providers: Record<string, { configured: boolean; source: string | null }>;
    known: string[];
    endpoints: string[];
  }> {
    return http.get("/credentials");
  },

  /** Ask an OpenAI-compatible server which models it serves. */
  async getEndpointModels(baseUrl: string): Promise<{ base_url: string; models: string[] }> {
    return http.get("/llm/endpoint-models", { base_url: baseUrl });
  },

  // ── Maintenance ───────────────────────────────────────────────────────────

  async getMaintenanceStatus(kb = "default"): Promise<{
    community_detection: {
      running: boolean;
      pending_nodes?: number;
      needed?: boolean;
      timer_armed?: boolean;
      idle_seconds?: number;
    };
    temporal_digests: { running: boolean };
    ingestion?: {
      active: number;
      last_completed_at?: string | null;
    };
    healthy?: boolean;
  }> {
    return http.get(`/admin/maintenance-status`, withKb(kb));
  },

  async rebuildCommunities(kb = "default"): Promise<{ status: string; message: string }> {
    return http.post(`/admin/rebuild-communities${kbQuery(kb)}`, {});
  },

  async buildTemporalDigests(period?: string, kb = "default"): Promise<{ status: string; message: string }> {
    return http.post(`/admin/build-temporal-digests${kbQuery(kb)}`, { period: period ?? null });
  },

  async resetIngestionData(kb = "default"): Promise<{ status: string; message: string }> {
    return http.post(`/admin/reset-ingestion-data${kbQuery(kb)}`, {});
  },

  async reingestAll(kb = "default"): Promise<{ status: string; notes_queued: number; message: string }> {
    return http.post(`/admin/reingest-all${kbQuery(kb)}`, {});
  },
};
