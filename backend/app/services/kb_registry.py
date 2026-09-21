"""Knowledge Base registry: SQL-backed metadata + cached service contexts."""

from __future__ import annotations

import re
import shutil
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.core.log import get_logger
from app.core.paths import ensure_data_layout, resolve_data_dir, resolve_default_vault_path
from app.services.graph import GraphService, graph_service
from app.services.qdrant_service import QdrantService, qdrant_service
from app.services.retrieval import RetrievalService
from app.services.meilisearch_service import MeilisearchService, meilisearch_service
from app.services.vault import ensure_vault
from app.workflows.chat import ChatWorkflow
from app.workflows.ingestion import IngestionWorkflow

logger = get_logger("KBRegistry")

DEFAULT_KB_ID = "default"

# Providers a KB may pin. Embed / rerank / multimodal are deliberately not
# per-KB: embed dims are shared across every KB's Qdrant collections.
LLM_PROVIDERS = (
    "local",
    "openai_compat",
    "openai",
    "gemini",
    "anthropic",
    "huggingface",
)
_LLM_META_KEYS = (
    "llm_provider",
    "llm_model",
    "llm_ingestion_model",
    "llm_base_url",
)


def finance_enabled_for(meta: dict) -> bool:
    """Finance is on unless a KB was explicitly switched off.

    Rows written before this column existed read back as NULL, and those KBs
    already have Firefly groups — defaulting them to off would hide real data.
    """
    value = meta.get("finance_enabled")
    return True if value is None else bool(value)


def _clean_override(value) -> str | None:
    text = (str(value) if value is not None else "").strip()
    return text or None


def build_kb_llm_service(
    provider: str | None,
    model: str | None,
    ingestion_model: str | None,
    base_url: str | None = None,
):
    """Construct an LLMService pinned to a KB's override (raises on bad config)."""
    from app.services.llm import LLMService

    prov = (provider or settings.LLM_PROVIDER or "local").lower().strip()
    return LLMService(
        prov,
        chat_model=model,
        ingestion_model=ingestion_model,
        ingestion_provider=prov,
        base_url=base_url,
    )


def _system_model_for(provider: str) -> str | None:
    """What the global Settings would use for ``provider`` (no service construction)."""
    is_global = provider == (settings.LLM_PROVIDER or "").lower().strip()
    if is_global and settings.CHAT_MODEL:
        return settings.CHAT_MODEL
    return {
        "local": settings.LLM_MODEL,
        "openai": settings.OPENAI_MODEL,
        "gemini": settings.GEMINI_MODEL,
        "anthropic": settings.ANTHROPIC_MODEL,
        "huggingface": settings.HUGGINGFACE_MODEL,
    }.get(provider)


def effective_llm_config(meta: dict) -> dict:
    """Resolved provider/model for a KB row: overrides layered over system Settings."""
    provider = (_clean_override(meta.get("llm_provider")) or settings.LLM_PROVIDER or "local").lower()
    model = _clean_override(meta.get("llm_model")) or _system_model_for(provider)
    ingestion_model = (
        _clean_override(meta.get("llm_ingestion_model"))
        or model
    )
    base_url = _clean_override(meta.get("llm_base_url")) or (
        settings.LLM_BASE_URL if provider == "openai_compat" else None
    )
    return {
        "provider": provider,
        "model": model,
        "ingestion_model": ingestion_model,
        "base_url": base_url,
        "inherited": not any(_clean_override(meta.get(k)) for k in _LLM_META_KEYS),
    }


def _kuzu_db_file(data_dir: Path, slug: str) -> Path:
    """Return the Kuzu *database file* path for a KB slug.

    Kuzu rejects directories — the default KB uses ``…/kuzu/kuzu_graph`` (a file).
    """
    return data_dir / "kuzu" / slug / "kuzu_graph"


def normalize_kuzu_path(path_str: str) -> str:
    """Coerce a mis-configured directory path into a Kuzu database file path."""
    if not path_str:
        return path_str
    p = Path(path_str)
    # Already a file (or not created yet but named like the default file)
    if p.name == "kuzu_graph" or p.suffix:
        return str(p)
    # Legacy bug: create_kb mkdir'd ``…/kuzu/<slug>`` as a directory
    if p.exists() and p.is_dir():
        return str(p / "kuzu_graph")
    # Not created yet — treat bare ``…/kuzu/<slug>`` as the parent folder
    if p.parent.name == "kuzu":
        return str(p / "kuzu_graph")
    return str(p)


@dataclass
class KBContext:
    """Bundled service instances for one knowledge base."""

    kb_id: str
    name: str
    qdrant: QdrantService
    meili: MeilisearchService
    vault_path: str = ""
    # Graph is opened lazily — notes/finance must work even if Kuzu path was misconfigured.
    _graph: GraphService | None = field(default=None, repr=False)
    _kuzu_path: str = field(default="", repr=False)
    retrieval_service: object = field(default=None, repr=False)
    ingestion_workflow: object = field(default=None, repr=False)
    chat_workflow: object = field(default=None, repr=False)
    # Per-KB LLM override (None = inherit system Settings).
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_ingestion_model: str | None = None
    llm_base_url: str | None = None
    finance_enabled: bool = True
    _llm: object = field(default=None, repr=False)
    _llm_built_for: tuple | None = field(default=None, repr=False)

    @property
    def graph(self) -> GraphService:
        if self._graph is None:
            path = self._kuzu_path
            if not path:
                raise RuntimeError(f"No Kuzu path configured for KB '{self.name}'")
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._graph = GraphService(db_path=path, qdrant=self.qdrant)
        return self._graph

    @property
    def has_llm_override(self) -> bool:
        return bool(
            self.llm_provider
            or self.llm_model
            or self.llm_ingestion_model
            or self.llm_base_url
        )

    @property
    def llm(self):
        """LLM service for this KB: a pinned per-KB instance, or the global one."""
        if not self.has_llm_override:
            from app.services.llm import llm_service

            return llm_service
        # Rebuild if the override, the inherited provider, or a credential
        # changed underneath — clients capture the API key at construction.
        from app.services.credentials import credentials

        key = (
            (self.llm_provider or settings.LLM_PROVIDER or "local").lower(),
            self.llm_model,
            self.llm_ingestion_model,
            self.llm_base_url,
            credentials.version,
        )
        if self._llm is None or self._llm_built_for != key:
            self._llm = build_kb_llm_service(*key[:4])
            self._llm_built_for = key
        return self._llm

    def apply_llm_override(
        self,
        provider: str | None,
        model: str | None,
        ingestion_model: str | None,
        base_url: str | None = None,
    ) -> None:
        """Replace the override and drop cached services so they pick it up."""
        self.llm_provider = provider
        self.llm_model = model
        self.llm_ingestion_model = ingestion_model
        self.llm_base_url = base_url
        self._llm = None
        self._llm_built_for = None
        self.retrieval_service = None
        self.ingestion_workflow = None
        self.chat_workflow = None

    def _ensure_lazy(self) -> None:
        llm = self.llm
        if self.retrieval_service is None:
            self.retrieval_service = RetrievalService(
                graph=self.graph,
                qdrant=self.qdrant,
                meili=self.meili,
                llm=llm,
            )
        if self.ingestion_workflow is None:
            self.ingestion_workflow = IngestionWorkflow(
                graph=self.graph,
                qdrant=self.qdrant,
                meili=self.meili,
                llm=llm,
                kb_id=self.kb_id,
            )
        if self.chat_workflow is None:
            self.chat_workflow = ChatWorkflow(retrieval=self.retrieval_service, llm=llm)

    def get_ingestion_workflow(self):
        self._ensure_lazy()
        return self.ingestion_workflow

    def get_chat_workflow(self):
        self._ensure_lazy()
        return self.chat_workflow


def _db_path() -> Path:
    return resolve_data_dir() / "orb.db"


def _connect() -> sqlite3.Connection:
    ensure_data_layout()
    path = _db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_bases (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            vault_path TEXT NOT NULL,
            kuzu_path TEXT NOT NULL,
            qdrant_col_cores TEXT NOT NULL,
            qdrant_col_rels TEXT NOT NULL,
            qdrant_col_contexts TEXT NOT NULL,
            typesense_collection TEXT NOT NULL,
            created_at TEXT,
            firefly_group_id INTEGER,
            firefly_group_title TEXT,
            llm_provider TEXT,
            llm_model TEXT,
            llm_ingestion_model TEXT,
            llm_base_url TEXT,
            finance_enabled INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    _ensure_optional_columns(conn)
    conn.commit()
    return conn


def _ensure_optional_columns(conn: sqlite3.Connection) -> None:
    """Add finance-scope and LLM-override columns to existing knowledge_bases tables."""
    rows = conn.execute("PRAGMA table_info(knowledge_bases)").fetchall()
    colnames = {row["name"] for row in rows}
    for name, sqltype in (
        ("firefly_group_id", "INTEGER"),
        ("firefly_group_title", "TEXT"),
        ("llm_provider", "TEXT"),
        ("llm_model", "TEXT"),
        ("llm_ingestion_model", "TEXT"),
        ("llm_base_url", "TEXT"),
        ("finance_enabled", "INTEGER NOT NULL DEFAULT 1"),
    ):
        if name not in colnames:
            conn.execute(f"ALTER TABLE knowledge_bases ADD COLUMN {name} {sqltype}")


def _default_kb() -> KBContext:
    vault = resolve_default_vault_path()
    vault_str = str(vault) if vault else str(resolve_data_dir() / "vaults" / "default")
    ensure_vault(vault_str)
    return KBContext(
        kb_id=DEFAULT_KB_ID,
        name="default",
        qdrant=qdrant_service,
        meili=meilisearch_service,
        vault_path=vault_str,
        _graph=graph_service,
        _kuzu_path=str(settings.KUZU_DB_PATH),
    )


class KBRegistry:
    """Manages knowledge base metadata and caches live KBContext instances."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._metadata: dict[str, dict] = {}
        self._cache: dict[str, KBContext] = {}
        self._load()
        if DEFAULT_KB_ID not in self._cache:
            self._cache[DEFAULT_KB_ID] = _default_kb()
            # Persist default vault if missing
            self._ensure_default_row()

    def _ensure_default_row(self) -> None:
        ctx = self._cache[DEFAULT_KB_ID]
        with self._lock:
            conn = _connect()
            try:
                row = conn.execute(
                    "SELECT id FROM knowledge_bases WHERE id = ?", (DEFAULT_KB_ID,)
                ).fetchone()
                if row is None:
                    meta = {
                        "id": DEFAULT_KB_ID,
                        "name": "default",
                        "slug": "default",
                        "vault_path": ctx.vault_path,
                        "kuzu_path": str(settings.KUZU_DB_PATH),
                        "qdrant_col_cores": settings.QDRANT_COLLECTION_NODE_CORES,
                        "qdrant_col_rels": settings.QDRANT_COLLECTION_NODE_RELATIONSHIPS,
                        "qdrant_col_contexts": settings.QDRANT_COLLECTION_NODE_ISOLATED_CONTEXTS,
                        "typesense_collection": settings.MEILI_INDEX_NAME,
                        "created_at": datetime.utcnow().isoformat(),
                    }
                    conn.execute(
                        """
                        INSERT INTO knowledge_bases
                        (id, name, slug, vault_path, kuzu_path, qdrant_col_cores,
                         qdrant_col_rels, qdrant_col_contexts, typesense_collection,
                         created_at, firefly_group_id, firefly_group_title)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            meta["id"],
                            meta["name"],
                            meta["slug"],
                            meta["vault_path"],
                            meta["kuzu_path"],
                            meta["qdrant_col_cores"],
                            meta["qdrant_col_rels"],
                            meta["qdrant_col_contexts"],
                            meta["typesense_collection"],
                            meta["created_at"],
                            meta.get("firefly_group_id"),
                            meta.get("firefly_group_title"),
                        ),
                    )
                    conn.commit()
                    self._metadata[DEFAULT_KB_ID] = meta
            finally:
                conn.close()

    def _load(self) -> None:
        try:
            conn = _connect()
            conn.execute(
                "UPDATE knowledge_bases SET llm_provider = 'local' "
                "WHERE llm_provider IN ('ollama', 'lm_studio')"
            )
            conn.commit()
            rows = conn.execute("SELECT * FROM knowledge_bases").fetchall()
            conn.close()
            for row in rows:
                meta = dict(row)
                fixed = normalize_kuzu_path(meta.get("kuzu_path") or "")
                if fixed and fixed != meta.get("kuzu_path"):
                    meta["kuzu_path"] = fixed
                    try:
                        conn2 = _connect()
                        conn2.execute(
                            "UPDATE knowledge_bases SET kuzu_path = ? WHERE id = ?",
                            (fixed, meta["id"]),
                        )
                        conn2.commit()
                        conn2.close()
                    except Exception as exc:  # pylint: disable=broad-exception-caught
                        logger.warning(
                            "[KBRegistry] Failed to repair kuzu_path for %s: %s",
                            meta.get("name"),
                            exc,
                        )
                self._metadata[meta["id"]] = meta
                if meta["id"] != DEFAULT_KB_ID:
                    try:
                        self._cache[meta["id"]] = self._build_context(
                            meta["id"], meta
                        )
                    except Exception as exc:  # pylint: disable=broad-exception-caught
                        logger.warning(
                            "[KBRegistry] Failed to init KB '%s': %s",
                            meta.get("name"),
                            exc,
                        )
                else:
                    # Refresh default vault path from DB
                    self._cache[DEFAULT_KB_ID] = KBContext(
                        kb_id=DEFAULT_KB_ID,
                        name="default",
                        qdrant=qdrant_service,
                        meili=meilisearch_service,
                        vault_path=meta["vault_path"],
                        _graph=graph_service,
                        _kuzu_path=meta.get("kuzu_path") or str(settings.KUZU_DB_PATH),
                        llm_provider=_clean_override(meta.get("llm_provider")),
                        llm_model=_clean_override(meta.get("llm_model")),
                        llm_ingestion_model=_clean_override(meta.get("llm_ingestion_model")),
                        llm_base_url=_clean_override(meta.get("llm_base_url")),
                        finance_enabled=finance_enabled_for(meta),
                    )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"[KBRegistry] Failed to load from SQLite: {exc}")

    def _save_row(self, meta: dict) -> None:
        conn = _connect()
        conn.execute(
            """
            INSERT INTO knowledge_bases
            (id, name, slug, vault_path, kuzu_path, qdrant_col_cores,
             qdrant_col_rels, qdrant_col_contexts, typesense_collection, created_at,
             firefly_group_id, firefly_group_title,
             llm_provider, llm_model, llm_ingestion_model, llm_base_url,
             finance_enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name,
              vault_path=excluded.vault_path,
              kuzu_path=excluded.kuzu_path,
              firefly_group_id=excluded.firefly_group_id,
              firefly_group_title=excluded.firefly_group_title,
              llm_provider=excluded.llm_provider,
              llm_model=excluded.llm_model,
              llm_ingestion_model=excluded.llm_ingestion_model,
              llm_base_url=excluded.llm_base_url,
              finance_enabled=excluded.finance_enabled
            """,
            (
                meta["id"],
                meta["name"],
                meta["slug"],
                meta["vault_path"],
                meta["kuzu_path"],
                meta["qdrant_col_cores"],
                meta["qdrant_col_rels"],
                meta["qdrant_col_contexts"],
                meta["typesense_collection"],
                meta.get("created_at"),
                meta.get("firefly_group_id"),
                meta.get("firefly_group_title"),
                meta.get("llm_provider"),
                meta.get("llm_model"),
                meta.get("llm_ingestion_model"),
                meta.get("llm_base_url"),
                1 if finance_enabled_for(meta) else 0,
            ),
        )
        conn.commit()
        conn.close()

    def list_kbs(self) -> list[dict]:
        with self._lock:
            return list(self._metadata.values()) or [
                {
                    "id": DEFAULT_KB_ID,
                    "name": "default",
                    "slug": "default",
                    "vault_path": self._cache[DEFAULT_KB_ID].vault_path,
                    "kuzu_path": str(settings.KUZU_DB_PATH),
                    "created_at": None,
                    "finance_enabled": 1,
                }
            ]

    def create_kb(self, name: str, vault_path: str | None = None) -> KBContext:
        kb_id = str(uuid.uuid4())
        # The slug becomes filesystem paths and Qdrant/Meili collection names —
        # a raw name containing `/` or `..` would escape DATA_DIR.
        slug = re.sub(r"[^a-z0-9_-]", "-", name.lower().replace(" ", "_")).strip("-_")
        if not slug:
            slug = f"kb-{kb_id[:8]}"
        data = resolve_data_dir()
        if not vault_path:
            vault_path = str(data / "vaults" / slug)
        vault_path = str(ensure_vault(vault_path))
        # Kuzu requires a database *file* path (not a directory).
        kuzu_path = str(_kuzu_db_file(data, slug))
        Path(kuzu_path).parent.mkdir(parents=True, exist_ok=True)

        meta = {
            "id": kb_id,
            "name": name,
            "slug": slug,
            "vault_path": vault_path,
            "kuzu_path": kuzu_path,
            "qdrant_col_cores": f"{slug}_node_cores",
            "qdrant_col_rels": f"{slug}_node_relationships",
            "qdrant_col_contexts": f"{slug}_node_isolated_contexts",
            "typesense_collection": f"{slug}_nodes",
            "created_at": datetime.utcnow().isoformat(),
            "finance_enabled": 1,
        }

        with self._lock:
            self._metadata[kb_id] = meta
            self._save_row(meta)
            ctx = self._build_context(kb_id, meta)
            self._cache[kb_id] = ctx

        logger.info(f"[KBRegistry] Created KB '{name}' vault={vault_path}")
        return ctx

    def get_kb(self, kb_id: str) -> KBContext | None:
        with self._lock:
            if kb_id in self._cache:
                return self._cache[kb_id]
            if kb_id not in self._metadata:
                return None
            ctx = self._build_context(kb_id, self._metadata[kb_id])
            self._cache[kb_id] = ctx
            return ctx

    def get_kb_by_name(self, name: str) -> KBContext | None:
        normalized = name.lower().strip()
        if normalized in ("default", ""):
            return self._cache.get(DEFAULT_KB_ID) or _default_kb()
        with self._lock:
            for kb_id, meta in self._metadata.items():
                if (
                    meta.get("name", "").lower() == normalized
                    or meta.get("slug", "") == normalized
                ):
                    return self.get_kb(kb_id)
        return None

    def delete_kb(
        self,
        kb_id: str,
        *,
        delete_vault_files: bool = True,
        wipe_indexes: bool = True,
    ) -> bool:
        """Unregister a KB. Product path always wipes vault + indexes (defaults True)."""
        if kb_id == DEFAULT_KB_ID:
            raise ValueError("The default knowledge base cannot be deleted.")

        with self._lock:
            meta = self._metadata.pop(kb_id, None)
            ctx = self._cache.pop(kb_id, None)
            if meta is None:
                return False
            # Close Kuzu before deleting files on disk — only if it was ever
            # opened (``ctx.graph`` would open it just to close it).
            graph = getattr(ctx, "_graph", None)
            if graph is not None:
                try:
                    graph.close()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
            meta.pop("firefly_group_id", None)
            meta.pop("firefly_group_title", None)
            conn = _connect()
            conn.execute("DELETE FROM knowledge_bases WHERE id = ?", (kb_id,))
            conn.commit()
            conn.close()

        if wipe_indexes:
            self._cleanup_stores(meta)
        if delete_vault_files:
            try:
                vp = Path(meta["vault_path"]).resolve()
                # Only remove vaults Orb provisioned itself. A KB can point at a
                # pre-existing user folder (OneDrive, NAS) that holds far more
                # than Orb's notes — deleting the KB must never rmtree those.
                app_vaults = (resolve_data_dir() / "vaults").resolve()
                if vp.exists():
                    if vp == app_vaults or app_vaults in vp.parents:
                        shutil.rmtree(vp)
                    else:
                        logger.info(
                            "[KBRegistry] Keeping external vault folder %s "
                            "(outside %s)",
                            vp,
                            app_vaults,
                        )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(f"[KBRegistry] Vault delete failed: {exc}")

        logger.info(f"[KBRegistry] Deleted KB '{meta['name']}'")
        return True

    def rename_kb(self, kb_id: str, new_name: str) -> bool:
        if kb_id == DEFAULT_KB_ID:
            raise ValueError("The default knowledge base cannot be renamed.")
        with self._lock:
            if kb_id not in self._metadata:
                return False
            self._metadata[kb_id]["name"] = new_name
            self._save_row(self._metadata[kb_id])
            if kb_id in self._cache:
                self._cache[kb_id].name = new_name
        return True

    def get_metadata(self, kb_id: str) -> dict | None:
        with self._lock:
            meta = self._metadata.get(kb_id)
            return dict(meta) if meta else None

    def set_firefly_group(
        self, kb_id: str, group_id: int, title: str | None = None
    ) -> None:
        with self._lock:
            if kb_id not in self._metadata:
                return
            self._metadata[kb_id]["firefly_group_id"] = group_id
            if title:
                self._metadata[kb_id]["firefly_group_title"] = title
            self._save_row(self._metadata[kb_id])

    def detach_firefly_group(self, kb_id: str) -> None:
        """Remove KB linkage to a Firefly administration without deleting ledger data."""
        with self._lock:
            if kb_id not in self._metadata:
                return
            self._metadata[kb_id]["firefly_group_id"] = None
            self._metadata[kb_id]["firefly_group_title"] = None
            self._save_row(self._metadata[kb_id])

    def set_llm_config(
        self,
        kb_id: str,
        *,
        provider: str | None,
        model: str | None,
        ingestion_model: str | None,
        base_url: str | None = None,
    ) -> dict | None:
        """Persist a per-KB LLM override (all None = inherit) and refresh the live context."""
        provider = _clean_override(provider)
        if provider is not None:
            provider = provider.lower()
            if provider not in LLM_PROVIDERS:
                raise ValueError(
                    f"Unsupported provider '{provider}'. Choose one of: {', '.join(LLM_PROVIDERS)}"
                )
        model = _clean_override(model)
        ingestion_model = _clean_override(ingestion_model)
        base_url = _clean_override(base_url)
        if base_url:
            from app.services.credentials import normalize_base_url

            base_url = normalize_base_url(base_url)
        with self._lock:
            if kb_id == DEFAULT_KB_ID and DEFAULT_KB_ID not in self._metadata:
                self._ensure_default_row()
            meta = self._metadata.get(kb_id)
            if meta is None:
                return None
            meta["llm_provider"] = provider
            meta["llm_model"] = model
            meta["llm_ingestion_model"] = ingestion_model
            meta["llm_base_url"] = base_url
            self._save_row(meta)
            ctx = self._cache.get(kb_id)
            if ctx is not None:
                ctx.apply_llm_override(provider, model, ingestion_model, base_url)
            logger.info(
                "[KBRegistry] LLM override for '%s' → provider=%s model=%s ingestion=%s",
                meta.get("name"),
                provider or "(inherit)",
                model or "(inherit)",
                ingestion_model or "(inherit)",
            )
            return dict(meta)

    def set_finance_enabled(self, kb_id: str, enabled: bool) -> dict | None:
        """Turn finance on or off for one KB, leaving its Firefly group intact.

        Switching off hides the section rather than deleting anything, so a KB
        turned back on still has its accounts and transactions.
        """
        with self._lock:
            if kb_id == DEFAULT_KB_ID and DEFAULT_KB_ID not in self._metadata:
                self._ensure_default_row()
            meta = self._metadata.get(kb_id)
            if meta is None:
                return None
            meta["finance_enabled"] = 1 if enabled else 0
            self._save_row(meta)
            ctx = self._cache.get(kb_id)
            if ctx is not None:
                ctx.finance_enabled = bool(enabled)
            logger.info(
                "[KBRegistry] Finance %s for '%s'",
                "enabled" if enabled else "disabled",
                meta.get("name"),
            )
            return dict(meta)

    def set_vault_path(self, kb_id: str, vault_path: str) -> KBContext | None:
        """Point a KB at a different notes folder (creates folder if needed)."""
        vault_str = str(ensure_vault(vault_path))
        with self._lock:
            if kb_id == DEFAULT_KB_ID:
                if DEFAULT_KB_ID not in self._cache:
                    self._cache[DEFAULT_KB_ID] = _default_kb()
                self._ensure_default_row()
            if kb_id not in self._metadata:
                return None
            self._metadata[kb_id]["vault_path"] = vault_str
            self._save_row(self._metadata[kb_id])
            if kb_id in self._cache:
                self._cache[kb_id].vault_path = vault_str
            else:
                self._cache[kb_id] = self._build_context(kb_id, self._metadata[kb_id])
            logger.info(f"[KBRegistry] Vault for '{kb_id}' → {vault_str}")
            return self._cache[kb_id]

    def _build_context(self, kb_id: str, meta: dict) -> KBContext:
        kuzu_path = meta.get("kuzu_path") or ""
        qdrant = QdrantService(
            col_cores=meta["qdrant_col_cores"],
            col_relationships=meta["qdrant_col_rels"],
            col_contexts=meta["qdrant_col_contexts"],
        )
        ms = MeilisearchService(index_name=meta["typesense_collection"])
        # Do not open Kuzu here — notes/finance only need vault_path + kb_id.
        if kuzu_path:
            Path(kuzu_path).parent.mkdir(parents=True, exist_ok=True)
        return KBContext(
            kb_id=kb_id,
            name=meta["name"],
            qdrant=qdrant,
            meili=ms,
            vault_path=meta.get("vault_path", ""),
            _kuzu_path=kuzu_path,
            llm_provider=_clean_override(meta.get("llm_provider")),
            llm_model=_clean_override(meta.get("llm_model")),
            llm_ingestion_model=_clean_override(meta.get("llm_ingestion_model")),
            llm_base_url=_clean_override(meta.get("llm_base_url")),
            finance_enabled=finance_enabled_for(meta),
        )

    def _cleanup_stores(self, meta: dict) -> None:
        try:
            # Any client reaches the same server; a per-KB QdrantService would
            # recreate the collections in its constructor right before this.
            if qdrant_service.is_available():
                for col in [
                    meta["qdrant_col_cores"],
                    meta["qdrant_col_rels"],
                    meta["qdrant_col_contexts"],
                ]:
                    try:
                        qdrant_service.client.delete_collection(col)
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"[KBRegistry] Qdrant cleanup failed: {exc}")

        try:
            from app.services.meilisearch_service import MeilisearchService

            index_name = meta.get("typesense_collection")
            ms = MeilisearchService(index_name=index_name)
            if ms.is_available() and ms.client:
                try:
                    task = ms.client.delete_index(index_name)
                    ms.client.wait_for_task(task.task_uid, timeout_in_ms=10000)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning(
                        f"[KBRegistry] Meili index delete failed for '{index_name}': {exc}"
                    )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"[KBRegistry] Meilisearch cleanup failed: {exc}")

        try:
            kuzu_path = Path(meta["kuzu_path"]).resolve()
            kuzu_root = (resolve_data_dir() / "kuzu").resolve()
            if kuzu_root not in kuzu_path.parents:
                # A crafted KB name used to be able to point this at arbitrary
                # paths — never delete outside DATA_DIR/kuzu.
                logger.warning(
                    "[KBRegistry] Skipping Kuzu cleanup outside %s: %s",
                    kuzu_root,
                    kuzu_path,
                )
                return
            wal = Path(f"{kuzu_path}.wal")
            if kuzu_path.is_file():
                kuzu_path.unlink(missing_ok=True)
                wal.unlink(missing_ok=True)
                # Remove empty slug folder if we used …/kuzu/<slug>/kuzu_graph
                parent = kuzu_path.parent
                if parent.name != "kuzu" and parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
            elif kuzu_path.is_dir():
                shutil.rmtree(kuzu_path)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"[KBRegistry] Kuzu cleanup failed: {exc}")


kb_registry = KBRegistry()
