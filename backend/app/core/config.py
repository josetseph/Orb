"""Application configuration loaded from environment variables via pydantic-settings."""

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


def _default_data_dir() -> str:
    try:
        from app.core.paths import resolve_data_dir

        return str(resolve_data_dir())
    except Exception:  # pylint: disable=broad-exception-caught
        return str(REPO_ROOT / "data")


def _default_models_dir() -> str:
    try:
        from app.core.paths import resolve_models_dir

        return str(resolve_models_dir())
    except Exception:  # pylint: disable=broad-exception-caught
        return str(BACKEND_DIR / "models")


DEFAULT_KUZU_DB_PATH = str(Path(_default_data_dir()) / "kuzu" / "kuzu_graph")


class Settings(BaseSettings):
    """Pydantic settings that load all configuration from environment variables and .env files."""

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    PROJECT_NAME: str = "Orb"
    API_V1_STR: str = "/api/v1"
    # Include 127.0.0.1 — Electron desktop loads that origin (≠ localhost for CORS)
    CORS_ORIGINS: str = (
        "http://localhost:3700,http://localhost:3701,"
        "http://127.0.0.1:3700,http://127.0.0.1:3701,"
        "http://localhost:17400,http://127.0.0.1:17400"
    )
    CORS_ALLOW_ORIGIN_REGEX: str | None = None

    # ── Desktop / path layout ─────────────────────────────────────────────────
    DATA_DIR: str = _default_data_dir()
    MODELS_DIR: str = _default_models_dir()
    # "sqlite" (desktop default) | "postgres" (contributor docker)
    DATABASE_BACKEND: str = "sqlite"
    # AI setup: "local" | "cloud" | "hybrid" | "none"
    AI_SETUP_MODE: str = "none"

    # ── Kuzu (embedded graph database) ──────────────────────────────────────
    KUZU_DB_PATH: str = DEFAULT_KUZU_DB_PATH

    # ── LLM Provider (local = in-process GGUF via llama-cpp-python)
    LLM_PROVIDER: str = "local"
    LLM_FALLBACK_PROVIDER: str | None = None
    # OpenAI-compat HTTP fields — unused for in-process local; used by cloud HTTP
    LLM_BASE_URL: str = "http://127.0.0.1:8080"
    LLM_API_KEY: str = "local"
    LLM_MODEL: str = "local-chat"
    LLM_KEEP_ALIVE: str = "10m"
    LLM_RESPONSE_FORMAT: str = "text"
    CHAT_MODEL: str | None = None
    INGESTION_MODEL: str | None = None
    INGESTION_PROVIDER: str | None = None
    INGESTION_LLM_MODEL: str | None = "local-chat"
    INGESTION_GEMINI_MODEL: str | None = None

    EMBEDDING_PROVIDER: str = "local"
    EMBEDDING_BASE_URL: str = "http://127.0.0.1:8081"
    EMBEDDING_API_KEY: str = "local"
    EMBEDDING_MODEL: str = "local-embed"
    EMBEDDING_DIMENSIONS: int = 1024

    VECTOR_SIMILARITY_THRESHOLD: float = 0.50
    VECTOR_PRE_RERANK_THRESHOLD: float = 0.45
    COMMUNITY_RECOMPUTE_BATCH_SIZE: int = 100
    COMMUNITY_DETECTION_ENABLED: bool = False
    TEMPORAL_DIGESTS_ENABLED: bool = False
    TEMPORAL_DIGEST_PERIOD: str = "month"
    RERANKER_ENABLED: bool = True
    RERANKER_TOP_K: int = 10
    RERANKER_SCORE_THRESHOLD: float = 0.05
    GRAPH_EXPAND_TOP_NEIGHBORS: int = 10
    GRAPH_EXPAND_SCORE_THRESHOLD: float = 0
    MAX_POTENTIAL_QUESTIONS: int = 10
    # Desktop UX: 3 is enough for most personal-KB questions; HotPotQA-style
    # multi-hop rarely benefits past ~3 before KB-miss exhaustion (see Results/).
    MAX_LOOP_ITERATIONS: int = 3
    CHAT_HISTORY_MAX_MESSAGES: int = 24
    BENCHMARK_MODE: bool = False

    QDRANT_HOST: str = "127.0.0.1"
    QDRANT_PORT: int = 6333
    QDRANT_API_KEY: str | None = None
    QDRANT_COLLECTION_NODE_CORES: str = "node_cores"
    QDRANT_COLLECTION_NODE_RELATIONSHIPS: str = "node_relationships"
    QDRANT_COLLECTION_NODE_ISOLATED_CONTEXTS: str = "node_isolated_contexts"

    # Meilisearch (keyword / BM25). TYPESENSE_* kept as env aliases for older installs.
    MEILI_HOST: str = "127.0.0.1"
    MEILI_PORT: int = 7700
    MEILI_MASTER_KEY: str = "orb-dev-key"
    MEILI_INDEX_NAME: str = "orb_nodes"
    TYPESENSE_HOST: str = "127.0.0.1"
    TYPESENSE_PORT: int = 7700
    TYPESENSE_API_KEY: str = "orb-dev-key"
    TYPESENSE_COLLECTION_NAME: str = "orb_nodes"

    # Transcription is Qwen3-ASR 1.7B: 5.6x realtime on Apple Silicon with no
    # repetition loops or dropped speech on a 76-min lecture where Whisper
    # lost 103 s. Empty means "let asr_engine pick the layout per platform".
    MODEL_ASR_HF: str = ""
    MODEL_ASR_LOCAL: str = ""
    # "auto" | "mlx" | "transformers". An explicit engine is never substituted.
    ASR_ENGINE: str = "auto"
    # None lets the model detect the language; pinning "en" on non-English
    # audio is a known source of invented transcripts.
    ASR_LANGUAGE: str | None = "en"
    MODEL_MARLIN_HF: str = "lunahr/Marlin-2B-ungated"
    MODEL_MARLIN_LOCAL: str = "marlin-2b"
    # Multimodal — loaded in-process (optional until first multimedia ingest).
    # Images are downscaled to this before any model (local or cloud) sees them.
    IMAGE_DESCRIBE_MAX_PIXELS: int = 1500000
    MODEL_RERANKER_LOCAL: str = "qwen3-reranker-0.6b"

    FIREFLY_BASE_URL: str | None = None
    FIREFLY_RUNTIME_FILE: str | None = None
    FIREFLY_API_TOKEN: str | None = None

    MODELS_PATH: str = "models"
    PDF_VISUAL_EXTRACTION_ENABLED: bool = True
    PDF_VISUAL_EXTRACTION_MAX_PAGES: int = 0
    PDF_VISUAL_RENDER_DPI: int = 144
    PDF_VISUAL_TEXT_THRESHOLD: int = 80

    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str | None = None
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL: str | None = None
    HUGGINGFACE_API_KEY: str | None = None
    HUGGINGFACE_MODEL: str | None = None

    # Contributor Postgres only — leave unset for Orb desktop (SQLite).
    DATABASE_TRANSACTION_POOLER_URL: str | None = None
    DATABASE_SESSION_POOLER_URL: str | None = None
    DATABASE_DIRECT_CONNECTION_URL: str | None = None

    LOG_LEVEL: str = "INFO"
    INGESTION_AGENT_CONCURRENCY: int = 2
    INGESTION_PIPELINE_CONCURRENCY: int = 1
    MULTIMEDIA_CONCURRENCY: int = 1
    USE_DYNAMIC_EMBEDDING_INSTRUCTION: bool = True

    @model_validator(mode="after")
    def _apply_typesense_aliases(self) -> "Settings":
        """Copy legacy TYPESENSE_* onto MEILI_* when Meili still has defaults."""
        if self.MEILI_HOST == "127.0.0.1" and self.TYPESENSE_HOST != "127.0.0.1":
            self.MEILI_HOST = self.TYPESENSE_HOST
        if self.MEILI_PORT == 7700 and self.TYPESENSE_PORT != 7700:
            self.MEILI_PORT = self.TYPESENSE_PORT
        if self.MEILI_MASTER_KEY == "orb-dev-key" and self.TYPESENSE_API_KEY != "orb-dev-key":
            self.MEILI_MASTER_KEY = self.TYPESENSE_API_KEY
        if (
            self.MEILI_INDEX_NAME == "orb_nodes"
            and self.TYPESENSE_COLLECTION_NAME != "orb_nodes"
        ):
            self.MEILI_INDEX_NAME = self.TYPESENSE_COLLECTION_NAME
        return self


settings = Settings()

_data = Path(settings.DATA_DIR)
settings.KUZU_DB_PATH = str(_data / "kuzu" / "kuzu_graph")
settings.MODELS_PATH = settings.MODELS_DIR
