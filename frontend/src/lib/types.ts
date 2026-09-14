export interface Note {
    id: string;
    title: string;
    content: string;
    created_at: string;
    updated_at?: string;
    processed?: boolean;
    failed?: boolean;
    processing_stage?: string | null;
    processing_model?: string | null;
    /** Vault-relative path e.g. Life/Daily Log/2024-07-07.md */
    rel_path?: string | null;
}

export interface FilePreview {
    url: string;
    filename: string;
    type: "image" | "pdf" | "audio" | "video" | "other";
}

/** Subset used by the chat page when previewing a linked note. */
export interface NotePreview {
    id: string;
    title: string;
    content: string;
}

/** One per-attachment extraction job (GET /api/v1/notes/:id/attachments/jobs). */
export type AttachmentJob = { status: "running" | "done" | "failed"; error?: string | null };

/** Status shape returned by GET /api/v1/notes/:id/status */
export type NoteStatus = {
    id: string;
    processed: boolean;
    failed: boolean;
    status: string;
    processing_stage?: string | null;
    processing_model?: string | null;
};

export type ChatStatus = {
    request_id: string;
    conversation_id?: string;
    stage: string;
    model?: string | null;
    done?: boolean;
    result?: {
        answer?: string;
        thinking?: string | null;
        conversation_id?: string;
        assistant_message_id?: string;
    };
    error?: string;
};

export interface ChatConversation {
    id: string;
    kb_id: string;
    title: string;
    created_at?: string | null;
    updated_at?: string | null;
}

export interface ChatMessageRecord {
    id: string;
    conversation_id: string;
    role: "user" | "assistant";
    content: string;
    thinking?: string | null;
    created_at?: string | null;
}

/** Resolved chat/ingestion LLM for a KB (override layered over Settings). */
export interface EffectiveLLM {
    provider: string;
    model: string | null;
    ingestion_model: string | null;
    /** Endpoint URL when provider is "openai_compat". */
    base_url?: string | null;
    /** True when nothing is pinned on the KB — it follows Settings. */
    inherited: boolean;
}

/** Knowledge base metadata returned by GET /api/v1/kb */
export interface KnowledgeBase {
    id: string;
    name: string;
    slug?: string;
    vault_path?: string;
    kuzu_path?: string;
    qdrant_col_cores?: string;
    typesense_collection?: string;
    created_at: string | null;
    /** Per-KB LLM override (null = inherit Settings). Chat + ingestion only. */
    llm_provider?: string | null;
    llm_model?: string | null;
    llm_ingestion_model?: string | null;
    effective_llm?: EffectiveLLM;
    /** Whether the Finance section is available for this KB (default true). */
    finance_enabled?: boolean;
}

/** A local chat GGUF Orb found on this machine. */
export interface LocalChatModel {
    /** Catalog id, or a MODELS_DIR-relative / absolute .gguf path. */
    id: string;
    label: string;
    size_gb: number;
    /** "catalog" = curated + downloaded; "discovered" = found on disk. */
    source: "catalog" | "discovered";
    architecture?: string;
    context_length?: number | null;
    /** Non-blocking advisories, e.g. the file looks like a reranker. */
    warnings?: string[];
}

/** GET/PATCH /api/v1/kb/:id/llm */
export interface KBLLMConfig {
    kb_id: string;
    override: {
        provider: string | null;
        model: string | null;
        ingestion_model: string | null;
        base_url?: string | null;
    };
    effective: EffectiveLLM;
    providers: string[];
    /** OpenAI-compatible endpoints that already have a saved key. */
    endpoints?: string[];
    /** Chat GGUFs already on disk — catalog downloads plus user-added files. */
    local_models: LocalChatModel[];
}

export interface FinanceAccount {
    id: string;
    name: string;
    account_type: string;
    opening_balance: number;
    balance: number;
    currency?: string;
    archived?: boolean;
}

export interface FinanceTransaction {
    id: string;
    group_id?: string;
    journal_id?: string;
    date: string | null;
    description: string;
    amount: number;
    account_id: string;
    account_name?: string;
    counterparty_name?: string;
    type?: string;
    category?: string | null;
    currency_code?: string | null;
}

export interface FinanceBudget {
    id: string;
    name: string;
    active: boolean;
    spent: number;
    currency?: string | null;
    auto_budget_amount?: number;
    auto_budget_period?: string | null;
    notes?: string | null;
}

export interface FinanceCategory {
  id: string;
  name: string;
  notes?: string | null;
}

export interface FinanceRecurrence {
  id: string;
  title: string;
  type?: string | null;
  description?: string | null;
  amount: number;
  currency?: string | null;
  first_date?: string | null;
  repeat_until?: string | null;
  active: boolean;
  repetition_type?: string | null;
  repetition_moment?: string | null;
  source_name?: string | null;
  destination_name?: string | null;
}

export interface FinanceRuleGroup {
  id: string;
  title: string;
  description?: string | null;
  order?: number | null;
  active: boolean;
}

export interface FinanceRule {
  id: string;
  title: string;
  description?: string | null;
  rule_group_id: string;
  trigger?: string | null;
  active: boolean;
  strict?: boolean;
  triggers: Array<{ type?: string | null; value?: string | null }>;
  actions: Array<{ type?: string | null; value?: string | null }>;
}

export interface FinanceSearchResult {
  kind: "transactions" | "accounts" | string;
  query: string;
  results: Array<FinanceTransaction | FinanceAccount>;
}

export interface FinanceWorkspace {
    exists: boolean;
    ready?: boolean;
    status?: string;
    detail?: string;
    scope?: string;
    kb_id?: string;
    kb_name?: string;
    firefly_group_id?: number;
    currency?: string;
    administration_title?: string;
    firefly_url?: string;
}

export interface FinanceSummary {
    days: number;
    start: string;
    end: string;
    asset_balance: number;
    income_total: number;
    expense_total: number;
    transfer_total: number;
    net_flow: number;
    chart?: unknown;
    accounts: FinanceAccount[];
    recent_transactions: FinanceTransaction[];
}

export interface FinanceReport {
    start: string;
    end: string;
    basic: Record<string, unknown>;
    category_chart?: unknown;
    budget_chart?: unknown;
    balance_chart?: unknown;
    accounts: FinanceAccount[];
    kb_id?: string;
    kb_name?: string;
}

export interface NotesGraphPayload {
    nodes: Array<{ id: string; title: string; type: string; rel_path?: string | null }>;
    edges: Array<{ source: string; target: string; type: string }>;
    center_id?: string;
}

export interface SetupStatus {
    data_dir: string;
    models_dir: string;
    paths_json?: string;
    default_vault_path?: string;
    active_vault_path?: string;
    ai_setup_mode: string;
    ai_configured: boolean;
    local_models_ready?: boolean;
    multimodal_ready?: boolean;
    needs_model_download?: boolean;
    database_backend: string;
    llm_provider?: string;
}
