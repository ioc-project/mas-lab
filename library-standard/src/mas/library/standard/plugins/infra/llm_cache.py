#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Infra middleware plugins — wrap EngineContract; runtime dispatches by registry name."""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mas.runtime.engine.llm_cache import (
    load_cache,
    middleware_cache_deserialize,
    middleware_cache_serialize,
    persist_cache,
)
from mas.runtime.schema.egress import InvokeEngineIo
from mas.runtime.schema.ingress import EngineIoReturn

_SHARED_LLM_CACHES: dict[str, dict[str, Any]] = {}

# First LlmCacheMiddleware in this process that asks for write_mode=replace
# truncates cache_path. Later instances for the same path must load/merge so
# a MAS (one middleware per agent) does not wipe siblings' writes.
_REPLACE_STARTED: set[str] = set()


def reset_llm_cache_replace_guard() -> None:
    """Test helper: allow another replace-on-startup in this process."""
    _REPLACE_STARTED.clear()


def _cache_for_path(path: Path | None) -> dict[str, Any]:
    """One in-memory dict per resolved cache file.

    MAS peer engines each wrap their own LiveLlmEngine with llm_cache. Without
    sharing, each persist() dumped a stale snapshot and clobbered keys written
    by the other agent in the same process. Only used for the default
    write_mode="append" -- write_mode="replace" manages its own disk-backed
    dict directly (see LlmCacheMiddleware.__post_init__/_persist) so a later
    instance for the same path picks up on-disk changes made outside this
    process-shared cache.
    """
    if path is None:
        return {}
    key = str(path.expanduser().resolve())
    cached = _SHARED_LLM_CACHES.get(key)
    if cached is None:
        cached = load_cache(path)
        _SHARED_LLM_CACHES[key] = cached
    return cached


@dataclass
class LlmCacheMiddleware:
    """Cache LLM_CALL results on disk — sits in front of live infra.

    allow_read / allow_write are independent (both default True, like a
    normal read-through cache); a demo can compose write-only (build the
    cache), read-only (replay it), or both (default) purely via infra
    manifest params — no code branching per mode.
    """

    per = "all"
    stores_llm_responses = True

    inner: Any
    middleware_id: str = "llm_cache"
    cache_path: Path | None = None
    allow_read: bool = True
    allow_write: bool = True
    # Replay-only mode: never falls through to `inner` on a cache miss, so a
    # stacked infra manifest can guarantee zero calls reach the real provider
    # (e.g. a fast demo replay with no live LLM/network configured at all).
    raise_on_miss: bool = False
    include_preview: bool = False
    # A cache hit returns in ~0ms, which removes the pacing real LLM latency
    # gave calling code for free -- e.g. a multi-agent demo where a moderator
    # posts a "Round N" card on delegation start while a specialist's HITL
    # request is posted synchronously the instant it registers can visibly
    # race (HITL card before the round card) once every delegate answers
    # instantly. Sleep for a random uniform delay in
    # [replay_delay_min_s, replay_delay_max_s] on a hit to restore demo-worthy
    # pacing; both default to 0 (no delay, existing behavior unchanged). Set
    # both to the same value for a fixed delay.
    replay_delay_min_s: float = 0.0
    replay_delay_max_s: float = 0.0
    # Optional: append {key, preview} as a JSONL line to this file on every
    # raise_on_miss -- turns an opaque "key mismatch" into an inspectable
    # diff (e.g. parallel-delegation timing making a peer-context prompt
    # non-deterministic between record and replay runs).
    miss_log_path: Path | None = None
    # append (default): load existing keys (shared across sibling MAS
    # engines via _cache_for_path), persist merges new ones.
    # replace: first instance in this process truncates cache_path (startup
    # recording); later instances for the same path load/merge straight off
    # disk instead of the process-shared cache, since a fresh recording run
    # is expected to have rewritten the file out-of-process.
    write_mode: str = "append"
    _cache: dict[str, Any] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        mode = (self.write_mode or "append").strip().lower()
        if mode not in {"append", "replace"}:
            raise ValueError(f"write_mode must be 'append' or 'replace', got {self.write_mode!r}")
        self.write_mode = mode
        if not self.cache_path:
            return
        self.cache_path = Path(self.cache_path).expanduser().resolve()
        if mode == "replace" and self.allow_write:
            token = str(self.cache_path)
            if token not in _REPLACE_STARTED:
                _REPLACE_STARTED.add(token)
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_text("{}", encoding="utf-8")
                self._cache = {}
                return
        self._cache = load_cache(self.cache_path) if mode == "replace" else _cache_for_path(self.cache_path)

    def exchange_preview(self, op: str, *, correlation_id: int = 0) -> str:
        head = str(self.inner.exchange_preview(op, correlation_id=correlation_id) or "")
        label = "[llm_cache middleware]"
        return f"{label}\n{head}".strip() if head else label

    def invoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        if not (self.allow_read or self.allow_write) or io.op != "LLM_CALL":
            return self.inner.invoke(io)
        # Exactly one exchange_preview() call per invoke(): LiveLlmEngine's
        # preview resets ctx._assembly_correlation_id as a side effect, so
        # calling it more than once here (as the old separate tool-results
        # check + cache-key computation did) corrupted textual tool-call
        # parsing correlation state on every cached turn.
        # No tool-results skip: the cache key is the full preview text
        # (conversation so far, including any prior tool result), so a
        # different tool outcome naturally produces a different key and a
        # fresh miss -- there is nothing to protect against by excluding
        # these turns, and skipping them defeats caching a whole agentic
        # turn, where the useful answer is almost always the post-tool-call
        # completion.
        preview = self._preview(io)
        key, cached = self._lookup(preview)
        if self.allow_read and cached is not None:
            self._simulate_replay_delay()
            if self.allow_write and key not in self._cache:
                self._cache[key] = cached
                self._persist()
            ret = middleware_cache_deserialize(cached, io.correlation_id)
            return ret.model_copy(
                update={
                    "cache_status": "hit",
                    "cache_layer": "infra_llm_cache",
                    "cache_events": [{"layer": "infra_llm_cache", "status": "hit"}],
                }
            )
        if self.allow_read and self.raise_on_miss:
            self._log_miss(key, preview)
            self._record_strict_miss(io)
            shown = preview if len(preview) <= 4000 else preview[:4000] + "\n…"
            raise RuntimeError(f"llm_cache miss (raise_on_miss=true) for key {key}\n{shown}")
        ret = self.inner.invoke(io)
        if self.allow_read:
            events = [*ret.cache_events, {"layer": "infra_llm_cache", "status": "miss"}]
            ret = ret.model_copy(
                update={
                    "cache_status": "miss",
                    "cache_layer": "infra_llm_cache",
                    "cache_events": events,
                }
            )
        if (
            self.allow_write
            and ret.response_kind == "MODEL_TEXT"
            and ret.next_step in {"STOP", "TOOL_CALL", "PARALLEL_TOOL_CALLS"}
        ):
            self._cache[key] = middleware_cache_serialize(
                ret,
                include_preview=self.include_preview,
                preview=preview,
            )
            self._persist()
        return ret

    async def ainvoke(self, io: InvokeEngineIo) -> EngineIoReturn:
        if not (self.allow_read or self.allow_write) or io.op != "LLM_CALL":
            return await self.inner.ainvoke(io)
        preview = self._preview(io)
        key, cached = self._lookup(preview)
        if self.allow_read and cached is not None:
            await self._asimulate_replay_delay()
            if self.allow_write and key not in self._cache:
                self._cache[key] = cached
                self._persist()
            ret = middleware_cache_deserialize(cached, io.correlation_id)
            return ret.model_copy(
                update={
                    "cache_status": "hit",
                    "cache_layer": "infra_llm_cache",
                    "cache_events": [{"layer": "infra_llm_cache", "status": "hit"}],
                }
            )
        if self.allow_read and self.raise_on_miss:
            self._log_miss(key, preview)
            self._record_strict_miss(io)
            shown = preview if len(preview) <= 4000 else preview[:4000] + "\n…"
            raise RuntimeError(f"llm_cache miss (raise_on_miss=true) for key {key}\n{shown}")
        ret = await self.inner.ainvoke(io)
        if self.allow_read:
            events = [*ret.cache_events, {"layer": "infra_llm_cache", "status": "miss"}]
            ret = ret.model_copy(
                update={
                    "cache_status": "miss",
                    "cache_layer": "infra_llm_cache",
                    "cache_events": events,
                }
            )
        if (
            self.allow_write
            and ret.response_kind == "MODEL_TEXT"
            and ret.next_step in {"STOP", "TOOL_CALL", "PARALLEL_TOOL_CALLS"}
        ):
            self._cache[key] = middleware_cache_serialize(
                ret,
                include_preview=self.include_preview,
                preview=preview,
            )
            self._persist()
        return ret

    def _simulate_replay_delay(self) -> None:
        lo = max(0.0, self.replay_delay_min_s)
        hi = max(lo, self.replay_delay_max_s)
        if hi <= 0:
            return
        time.sleep(random.uniform(lo, hi))

    async def _asimulate_replay_delay(self) -> None:
        lo = max(0.0, self.replay_delay_min_s)
        hi = max(lo, self.replay_delay_max_s)
        if hi <= 0:
            return
        import asyncio

        await asyncio.sleep(random.uniform(lo, hi))

    def _log_miss(self, key: str, preview: str) -> None:
        if not self.miss_log_path:
            return
        try:
            self.miss_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.miss_log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"key": key, "preview": preview}) + "\n")
        except Exception:
            pass

    def _record_strict_miss(self, io: InvokeEngineIo) -> None:
        from mas.runtime.engine.infra_pipeline import call_ctx

        ctx = call_ctx(self.inner)
        observability = ctx.observability if ctx is not None else None
        record = getattr(observability, "record_cache_lookup", None) if observability is not None else None
        if callable(record):
            record(
                correlation_id=io.correlation_id,
                cache_layer="infra_llm_cache",
                cache_status="miss",
            )

    def _preview(self, io: InvokeEngineIo) -> str:
        return str(self.inner.exchange_preview("LLM_CALL", correlation_id=io.correlation_id) or "")

    def _lookup(self, preview: str) -> tuple[str, Any | None]:
        from mas.runtime.engine.llm_cache import cache_route_from_engine, lookup_preview_cache

        leaf, model, api_base, leaf_count = cache_route_from_engine(self.inner)
        return lookup_preview_cache(
            self._cache,
            preview,
            leaf=leaf,
            model=model,
            api_base=api_base,
            leaf_count=leaf_count,
        )

    def _cache_key(self, preview: str) -> str:
        key, _entry = self._lookup(preview)
        return key

    def _persist(self) -> None:
        if not self.cache_path:
            return
        if self.write_mode == "replace":
            # write_mode="replace" isn't backed by the process-shared cache
            # (see __post_init__/_cache_for_path) -- merge with whatever's on
            # disk now so this doesn't clobber entries another instance for
            # the same path already wrote since this one's own load.
            on_disk = load_cache(self.cache_path)
            on_disk.update(self._cache)
            self._cache = on_disk
        persist_cache(self.cache_path, self._cache)

    @classmethod
    def wrap(cls, inner: Any, params: dict[str, Any] | None = None) -> "LlmCacheMiddleware":
        """Build from an infra pipeline ``params`` mapping (alias keys allowed)."""
        params = dict(params or {})
        path_raw = params.get("cache_path") or params.get("path")
        path = Path(str(path_raw)) if path_raw else None
        enabled = params.get("enabled")
        default_on = True if enabled is None else enabled is not False
        allow_read = params.get("allow_read", default_on) is not False
        allow_write = params.get("allow_write", default_on) is not False
        miss_log_raw = params.get("miss_log_path")
        return cls(
            inner=inner,
            cache_path=path,
            allow_read=bool(allow_read),
            allow_write=bool(allow_write),
            raise_on_miss=params.get("raise_on_miss", False) is True,
            include_preview=params.get("include_preview", False) is True,
            replay_delay_min_s=float(params.get("replay_delay_min_s") or 0.0),
            replay_delay_max_s=float(params.get("replay_delay_max_s") or 0.0),
            miss_log_path=Path(str(miss_log_raw)) if miss_log_raw else None,
            write_mode=str(params.get("write_mode") or "append"),
        )
