# Copyright 2026 Cisco Systems, Inc. and its affiliates
#
# SPDX-License-Identifier: Apache-2.0

"""Semantic memory plugin — vector search for long-term knowledge retrieval.

**Facade** that composes orthogonal building blocks:
- ``Retriever`` (search logic) — ``memory/_retrievers.py``

Schema initialization, indexing, caching, and context injection remain in
this plugin.  The search path delegates to a ``Retriever`` instance.

Constants are re-exported from the building blocks for backward compatibility.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from mas.library.standard.plugins.memory._citations import (
    CitationFormatter,
    create_citation_formatter,
)
from mas.library.standard.plugins.memory._retrievers import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_TOKENS,
    DEFAULT_HYBRID_TEXT_WEIGHT,
    DEFAULT_HYBRID_VECTOR_WEIGHT,
    DEFAULT_MAX_RESULTS,
    DEFAULT_MIN_SCORE,
    DEFAULT_MMR_LAMBDA,
    DEFAULT_TEMPORAL_DECAY_HALF_LIFE_DAYS,
    FTSRetriever,
    HybridRetriever,
    Retriever,
)
from mas.runtime.contracts.context_contract import ContextContract, ContextPart

from .memory_provider_plugin import MemoryProviderPlugin

logger = logging.getLogger(__name__)

DEFAULT_CACHE_MAX_ENTRIES = 128

# Re-export constants for backward compatibility
__all__ = [
    "SemanticMemoryPlugin",
    "_chunk_text",
    "DEFAULT_CHUNK_TOKENS",
    "DEFAULT_CHUNK_OVERLAP",
    "DEFAULT_MAX_RESULTS",
    "DEFAULT_MIN_SCORE",
    "DEFAULT_HYBRID_VECTOR_WEIGHT",
    "DEFAULT_HYBRID_TEXT_WEIGHT",
    "DEFAULT_MMR_LAMBDA",
    "DEFAULT_TEMPORAL_DECAY_HALF_LIFE_DAYS",
    "DEFAULT_CACHE_MAX_ENTRIES",
]


def _chunk_text(
    text: str, chunk_tokens: int = DEFAULT_CHUNK_TOKENS, overlap_tokens: int = DEFAULT_CHUNK_OVERLAP
) -> List[Dict[str, Any]]:
    """Split text into overlapping chunks (unchanged utility function)."""
    chunk_chars = chunk_tokens * 4
    overlap_chars = overlap_tokens * 4
    step = max(1, chunk_chars - overlap_chars)

    chunks = []
    for i in range(0, max(1, len(text)), step):
        segment = text[i : i + chunk_chars]
        if not segment.strip():
            continue
        chunks.append(
            {
                "text": segment,
                "char_start": i,
                "char_end": i + len(segment),
                "token_estimate": len(segment) // 4,
            }
        )
        if i + chunk_chars >= len(text):
            break
    return chunks


class SemanticMemoryPlugin(MemoryProviderPlugin, ContextContract):
    """SQLite-backed semantic memory — facade composing a ``Retriever``.

    Parameters
    ----------
    db_path:
        Path to the SQLite database.  Supports ``{agent_id}`` placeholder.
    embed_fn:
        Callable for embedding.  If None, FTS-only retriever is used.
    retriever:
        Optional pre-built ``Retriever``.  If provided, ``embed_fn`` and
        search params are ignored (retriever is used as-is).
        chunk_tokens / chunk_overlap / max_results / min_score / ...:
        Default retriever tuning parameters, forwarded to auto-created retriever.
    """

    plugin_id = "semantic_memory@v1"
    implements = ["memory"]
    requires: List[str] = []
    governed_by: List[str] = []

    @staticmethod
    def default_store_path(agent_id: str = "default"):
        from mas.library.standard.plugins.memory.semantic_store import default_store_path

        return default_store_path(agent_id)

    def __init__(
        self,
        db_path: str = "",
        embed_fn: Optional[Callable[..., List[List[float]]]] = None,
        chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        max_results: int = DEFAULT_MAX_RESULTS,
        min_score: float = DEFAULT_MIN_SCORE,
        hybrid_enabled: bool = True,
        vector_weight: float = DEFAULT_HYBRID_VECTOR_WEIGHT,
        text_weight: float = DEFAULT_HYBRID_TEXT_WEIGHT,
        mmr_enabled: bool = False,
        mmr_lambda: float = DEFAULT_MMR_LAMBDA,
        temporal_decay_enabled: bool = False,
        temporal_decay_half_life_days: int = DEFAULT_TEMPORAL_DECAY_HALF_LIFE_DAYS,
        cache_enabled: bool = True,
        cache_max_entries: int = DEFAULT_CACHE_MAX_ENTRIES,
        section_header: str = "## Semantic Memory",
        pinned: bool = False,
        retriever: Optional[Retriever] = None,
        citations_mode: str = "auto",
        citation_formatter: Optional[CitationFormatter] = None,
        context_inject: bool = True,
    ) -> None:
        MemoryProviderPlugin.__init__(self)
        ContextContract.__init__(self)

        self._db_path_template = db_path or ":memory:"
        self._embed_fn = embed_fn
        self._chunk_tokens = chunk_tokens
        self._chunk_overlap = chunk_overlap
        self._max_results = max_results
        self._min_score = min_score
        self._hybrid_enabled = hybrid_enabled
        self._vector_weight = vector_weight
        self._text_weight = text_weight
        self._cache_enabled = cache_enabled
        self._cache_max = cache_max_entries
        self._section_header = section_header
        self._pinned = pinned
        self._context_inject = context_inject
        self._citation_formatter = citation_formatter or create_citation_formatter(citations_mode)

        # Build retriever from params or use provided one
        if retriever is not None:
            self._retriever = retriever
        elif embed_fn is not None and hybrid_enabled:
            self._retriever = HybridRetriever(
                embed_fn=embed_fn,
                vector_weight=vector_weight,
                text_weight=text_weight,
                mmr_enabled=mmr_enabled,
                mmr_lambda=mmr_lambda,
                temporal_decay_enabled=temporal_decay_enabled,
                temporal_decay_half_life_days=temporal_decay_half_life_days,
            )
        else:
            self._retriever = FTSRetriever()

        self._conn: Optional[sqlite3.Connection] = None
        self._cache: OrderedDict[str, List[Dict[str, Any]]] = OrderedDict()
        self._pending_query: Optional[str] = None
        self._initialized = False

    @property
    def retriever(self) -> Retriever:
        return self._retriever

    # ------------------------------------------------------------------
    # Lazy initialization
    # ------------------------------------------------------------------

    def _resolve_db_path(self) -> str:
        if self._db_path_template == ":memory:":
            return ":memory:"
        agent_id = getattr(self, "agent_id", "default")
        return self._db_path_template.replace("{agent_id}", agent_id)

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return

        db_path = self._resolve_db_path()
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        if db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL REFERENCES documents(id),
                text TEXT NOT NULL,
                embedding TEXT DEFAULT NULL,
                char_start INTEGER,
                char_end INTEGER,
                token_estimate INTEGER,
                created_at REAL NOT NULL,
                FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
            )
        """)

        self._conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
            USING fts5(text, content='chunks', content_rowid='rowid')
        """)

        self._conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, text)
                VALUES ('delete', old.rowid, old.text);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, text)
                VALUES ('delete', old.rowid, old.text);
                INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
            END;
        """)

        self._conn.commit()
        self._initialized = True
        logger.debug(
            "SemanticMemory initialized at %s",
            db_path if db_path != ":memory:" else ":memory: (ephemeral)",
        )

    # ------------------------------------------------------------------
    # Indexing (unchanged)
    # ------------------------------------------------------------------

    def index_document(
        self,
        source: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        doc_id: Optional[str] = None,
    ) -> str:
        """Index a document: chunk it, embed it, store it."""
        self._ensure_initialized()
        assert self._conn is not None

        if not doc_id:
            doc_id = hashlib.sha256(content.encode()).hexdigest()[:16]

        now = time.time()
        meta_json = json.dumps(metadata or {})

        self._conn.execute(
            "INSERT OR REPLACE INTO documents (id, source, content, metadata, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (doc_id, source, content, meta_json, now, now),
        )

        self._conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))

        chunks = _chunk_text(content, self._chunk_tokens, self._chunk_overlap)
        embeddings: Optional[List[List[float]]] = None
        if self._embed_fn is not None:
            try:
                embeddings = self._embed_fn([c["text"] for c in chunks])
            except Exception as exc:
                logger.warning("SemanticMemory: embed_fn failed (%s) — storing without embeddings", exc)

        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc_id}:{i}"
            emb_json = json.dumps(embeddings[i]) if embeddings and i < len(embeddings) else None
            self._conn.execute(
                "INSERT INTO chunks (id, doc_id, text, embedding, char_start, char_end, "
                "token_estimate, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chunk_id,
                    doc_id,
                    chunk["text"],
                    emb_json,
                    chunk["char_start"],
                    chunk["char_end"],
                    chunk["token_estimate"],
                    now,
                ),
            )

        self._conn.commit()
        self._invalidate_cache()
        logger.debug("Indexed document %s (%d chunks) from source=%s", doc_id, len(chunks), source)
        return doc_id

    def index_session_transcript(self, agent_id: str, session_id: str, messages: List[Dict[str, Any]]) -> str:
        """Index session transcript as a single document."""
        from mas.runtime.contracts.content_types import text_of

        text = "\n".join(
            f"[{m.get('role', '?')}]: {text_of(m.get('content', ''), media='placeholder')}" for m in messages
        )
        return self.index_document(
            source=f"session:{agent_id}/{session_id}",
            content=text,
            metadata={"agent_id": agent_id, "session_id": session_id, "type": "session"},
            doc_id=f"session:{agent_id}:{session_id}",
        )

    # ------------------------------------------------------------------
    # Search — delegates to Retriever building block
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        min_score: Optional[float] = None,
        sources: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Search via the composed Retriever."""
        self._ensure_initialized()
        assert self._conn is not None

        max_r = max_results or self._max_results
        min_s = min_score if min_score is not None else self._min_score

        cache_key = f"{query}:{max_r}:{min_s}:{sources}"
        if self._cache_enabled and cache_key in self._cache:
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]

        results = self._retriever.search(
            conn=self._conn,
            query=query,
            max_results=max_r,
            min_score=min_s,
            sources=sources,
        )

        if self._cache_enabled:
            self._cache[cache_key] = results
            while len(self._cache) > self._cache_max:
                self._cache.popitem(last=False)

        return results

    def _invalidate_cache(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------
    # MemoryContract implementation
    # ------------------------------------------------------------------

    def read_memory(
        self,
        memory_type: str,
        *,
        search: str = "",
        limit: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        search_text = search
        if not search_text:
            return {"status": "ok", "items": [], "count": 0, "has_more": False}

        results = self.search(
            query=search_text,
            max_results=limit if limit is not None else self._max_results,
            sources=kwargs.get("sources"),
        )
        return {
            "status": "ok",
            "items": [
                {
                    # Return the document key (doc_id), not the chunk key.
                    # Chunk IDs follow the pattern "{doc_id}:{chunk_index}" where
                    # chunk_index is always a non-negative integer suffix.
                    # Callers (MemorySearchTool, eval pipelines) need the doc_id
                    # to match against ground-truth provenance keys.
                    "key": (
                        r["id"].rsplit(":", 1)[0]
                        if ":" in r["id"] and r["id"].rsplit(":", 1)[-1].isdigit()
                        else r["id"]
                    ),
                    "content": r["text"],
                    "metadata": {
                        **r.get("metadata", {}),
                        "source": r["source"],
                        "score": r["score"],
                    },
                }
                for r in results
            ],
            "count": len(results),
            "has_more": False,
        }

    def write_memory(self, memory_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        content = payload.get("content", "")
        source = payload.get("source", "manual")
        metadata = payload.get("metadata", {})
        doc_id = payload.get("key")

        if isinstance(content, dict):
            content = json.dumps(content)
        else:
            from mas.runtime.contracts.content_types import text_of

            content = text_of(content)

        doc_id = self.index_document(source=source, content=str(content), metadata=metadata, doc_id=doc_id)
        return {"status": "ok", "key": doc_id, "version": 1}

    # ------------------------------------------------------------------
    # ContextContract: inject search results into prompt
    # ------------------------------------------------------------------

    def on_pre_llm_call(self, hook_data: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        if not self._context_inject:
            return hook_data
        messages = hook_data.get("messages", [])
        user_msg = None
        for msg in reversed(messages):
            if msg.get("role") == "user":
                from mas.runtime.contracts.content_types import text_of

                self._pending_query = text_of(msg.get("content", ""))
                user_msg = msg
                break

        # Proactive memory injection: if we find high-confidence memories,
        # prepend them to the user message so the LLM cannot miss them.
        # Small/tool-calling models often skip system-context; user-message
        # injection is more reliable.
        if self._pending_query and user_msg is not None:
            try:
                self._ensure_initialized()
                results = self.search(self._pending_query, max_results=3)
            except Exception:
                results = []

            if results:
                snippets = [r["text"] for r in results if r.get("score", 0) >= 0.5]
                if snippets:
                    prefix = (
                        f"[Relevant memories — apply these before answering]\n"
                        f"{chr(10).join(f'- {s}' for s in snippets)}\n\n"
                    )
                    current = user_msg.get("content")
                    if isinstance(current, list):
                        user_msg["content"] = [{"type": "text", "text": prefix}] + list(current)
                    else:
                        user_msg["content"] = prefix + str(self._pending_query)
                    logger.debug(
                        "SemanticMemory: injected %d memories into user message",
                        len(snippets),
                    )

        return hook_data

    def collect_context(self, request: Any = None) -> List[ContextPart]:
        if not self._context_inject:
            return []
        query = (
            request.query if request is not None and getattr(request, "query", None) else None
        ) or self._pending_query
        if not query:
            return []

        try:
            results = self.search(query)
        except Exception as exc:
            logger.warning("SemanticMemory: search failed (%s) — skipping injection", exc)
            return []

        if not results:
            return []

        body = self._citation_formatter.format_results(results)
        full = f"{self._section_header}\n{body}"

        # Propagate created_at from search results so the MCE can compute
        # OC metric C4 (Stale Memory Utilization Rate) from the event stream.
        created_ats = [r["chunk"]["created_at"] for r in results if r.get("chunk", {}).get("created_at") is not None]

        return [
            ContextPart.memory(
                content=full,
                source="memory:semantic",
                section_id="memory/semantic",
                token_estimate=max(1, len(full) // 4),
                pinned=self._pinned,
                metadata={
                    "memory_type": "semantic",
                    "items_count": len(results),
                    "top_score": results[0]["score"] if results else 0,
                },
                provenance={
                    "mechanism": "memory_read",
                    "trigger": "runtime_default",
                    "actor": "semantic_memory",
                    "source_type": "memory",
                    "annotations": {
                        "oldest_created_at": min(created_ats) if created_ats else None,
                        "newest_created_at": max(created_ats) if created_ats else None,
                        "items_created_at": created_ats,
                    },
                },
            )
        ]

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
            self._initialized = False
