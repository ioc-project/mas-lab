#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""In-process ControlContract over SessionManager.

Every verb takes a session id, checks a capability token, and appends a
ControlEvent. Plugin, LLM tool, and admin callers share this class and
differ only in ``ControlCapability.surface``.
"""

from __future__ import annotations

from typing import Any, Literal

from mas.runtime.boundary.control.contract import (
    ContextUsageView,
    ControlCapability,
    ControlDenied,
    ControlEvent,
    QueueAt,
    QueuedInputView,
    QueueView,
    SessionNotStopped,
    SessionPaused,
    SessionSnapshotView,
)
from mas.runtime.engine.llm_output_limits import estimate_model_cost_usd
from mas.runtime.schema.observability import ObsEventKind
from mas.runtime.session import SessionStatus
from mas.runtime.session.snapshot import SnapshotRef, SnapshotTree


class SessionControl:
    """Default ControlContract implementation. Resolves ids through SessionManager."""

    def __init__(
        self,
        manager: Any,
        *,
        capability: ControlCapability | None = None,
        deny_navigate: Any | None = None,
    ) -> None:
        self._manager = manager
        self.capability = capability or ControlCapability(actor="in-process", surface="plugin")
        self.deny_navigate = deny_navigate
        self.events: list[ControlEvent] = manager.control_events

    def _require(self, method: str, session_id: str) -> Any:
        if not self.capability.allows(method, session_id):
            self._trace(method, session_id, kind="control_denied", denied=True, reason="capability")
            raise ControlDenied(method, session_id, reason="capability token does not allow this call")
        return self._manager.get(session_id)

    def scoped(self, capability: ControlCapability) -> SessionControl:
        """Narrow this handle. The result cannot do more than this token already could."""
        return SessionControl(
            self._manager,
            capability=self.capability.intersect(capability),
            deny_navigate=self.deny_navigate,
        )

    def _peer_queue_at(self, requested: QueueAt) -> QueueAt:
        if self.capability.surface == "peer":
            return "tail"
        return requested

    def _trace(
        self,
        method: str,
        session_id: str,
        *,
        kind: str,
        reason: str = "",
        payload: dict[str, Any] | None = None,
        denied: bool = False,
    ) -> ControlEvent:
        event = ControlEvent(
            kind=kind,
            session_id=session_id,
            actor=self.capability.actor,
            surface=self.capability.surface,
            method=method,
            reason=reason,
            payload=dict(payload or {}),
            denied=denied,
        )
        self.events.append(event)
        self._publish(event, session_id)
        return event

    def _publish(self, event: ControlEvent, session_id: str) -> None:
        try:
            session = self._manager.get(session_id)
        except KeyError:
            return
        driver = getattr(getattr(session, "instance", None), "driver", None)
        op = getattr(driver, "observability", None)
        if op is None:
            return
        payload = {
            "method": event.method,
            "category": f"control.{event.method}",
            "control_kind": event.kind,
            "actor": event.actor,
            "surface": event.surface,
            "denied": event.denied,
            "reason": event.reason,
            "session_id": event.session_id,
            **event.payload,
        }
        record_control = getattr(op, "record_control", None)
        if callable(record_control):
            record_control(**payload)
            return
        record_session = getattr(op, "record_session", None)
        if callable(record_session):
            record_session("control", **payload)

    def _ensure_stopped(self, session: Any, session_id: str, *, auto_stop: bool, method: str) -> None:
        if session.status is SessionStatus.PAUSED:
            return
        if auto_stop:
            session.pause(reason=f"{method} auto-stop")
            self._trace("pause", session_id, kind="session_paused", reason=f"{method} auto-stop")
            return
        self._trace(
            method,
            session_id,
            kind="control_denied",
            denied=True,
            reason="not stopped",
        )
        raise SessionNotStopped(session_id, method=method)

    def pause(self, session_id: str, *, reason: str) -> None:
        session = self._require("pause", session_id)
        session.pause(reason=reason)
        self._trace("pause", session_id, kind="session_paused", reason=reason)

    def resume(self, session_id: str) -> None:
        session = self._require("resume", session_id)
        session.resume()
        self._trace("resume", session_id, kind="session_resumed")

    def send_message(
        self,
        session_id: str,
        *,
        text: str,
        source: str = "user",
    ) -> str:
        """A2A Send Message (``message/send``).

        Additional input on a non-terminal task is queued at the tail as a
        turn. Never preempts or replaces an in-flight decode — that is
        :meth:`steer`, which A2A does not expose.
        """
        self._require("send_message", session_id)
        input_id = self._manager.turn_queue.enqueue(
            session_id,
            text,
            source=source,
            action="turn",
            actor=self.capability.actor,
            at="tail",
        )
        self._trace(
            "send_message",
            session_id,
            kind="input_enqueued",
            payload={"input_id": input_id, "source": source, "action": "turn", "at": "tail"},
        )
        return input_id

    def steer(
        self,
        session_id: str,
        *,
        text: str,
        mode: Literal[
            "preempt", "replace", "after", "amend", "enqueue", "inject_now"
        ] = "preempt",
        at: QueueAt = "head",
    ) -> None:
        """Control-protocol injection into a working generation. A2A has no steer RPC.

        ``preempt`` (default): keep streamed tokens, stop the rest of this
        decode, continue with ``text``. The decode is preempted; it is not a
        finished client response. ``amend`` / ``inject_now`` are aliases.

        ``replace``: discard streamed tokens, stop this decode, start a new
        exclusive turn with ``text``.

        ``after``: do not interrupt; queue ``text`` to the front and wait for
        the current generation to finish. ``enqueue`` is an alias. A peer
        always queues at the tail.
        """
        session = self._require("steer", session_id)
        when = {
            "preempt": "preempt",
            "amend": "preempt",
            "inject_now": "preempt",
            "replace": "replace",
            "after": "after",
            "enqueue": "after",
        }.get(mode)
        if when is None:
            raise ValueError("steer mode must be 'preempt', 'replace', or 'after'")
        if when != "after" and at not in {"head", "tail"}:
            raise ValueError("steer at= is only valid with mode='after'")
        supported = self._steer_modes(session)
        if when not in supported:
            plugin_id = self._pattern_id(session)
            raise ValueError(
                f"pattern {plugin_id} does not support steer mode {when!r} "
                f"(supported: {', '.join(sorted(supported)) or 'none'})"
            )
        who = self.capability.actor
        source = self.capability.surface
        queue_at = self._peer_queue_at(at)
        if when == "after":
            self._enqueue(
                session_id,
                text=text,
                source=source,
                action="turn",
                at=queue_at,
                actor=who,
            )
            self._trace(
                "steer",
                session_id,
                kind="steer_after",
                payload={"text": text, "at": queue_at, "source": source, "sender": who},
            )
            return
        if session.status is SessionStatus.PAUSED:
            self._trace(
                "steer",
                session_id,
                kind="control_denied",
                denied=True,
                reason="paused",
            )
            raise SessionPaused(session_id, reason=session.pause_reason or "paused")
        from mas.runtime.engine.inflight_llm import has_inflight, request_preempt, request_replace

        busy = has_inflight(session_id) or bool(getattr(session.controller, "inflight", False))
        if source == "peer" and not busy:
            action: Literal["turn", "steer"] = "steer" if when == "preempt" else "turn"
            self._enqueue(
                session_id,
                text=text,
                source=source,
                action=action,
                at="tail",
                actor=who,
            )
            self._trace(
                "steer",
                session_id,
                kind="steer_after",
                payload={
                    "text": text,
                    "at": "tail",
                    "why": "idle",
                    "source": source,
                    "sender": who,
                    "mode": when,
                },
            )
            return
        if when == "replace":
            if has_inflight(session_id):
                request_replace(session_id, text, source=source, sender=who)
                self._trace(
                    "steer",
                    session_id,
                    kind="steer_replaced",
                    payload={"text": text, "source": source, "sender": who},
                )
                return
            if bool(getattr(session.controller, "inflight", False)):
                placed = self._peer_queue_at("head")
                self._enqueue(
                    session_id,
                    text=text,
                    source=source,
                    action="turn",
                    at=placed,
                    actor=who,
                )
                self._trace(
                    "steer",
                    session_id,
                    kind="steer_after",
                    payload={"text": text, "at": placed, "why": "no_llm", "source": source},
                )
                return
            session.controller.run_turn(text, auto_hitl=False)
            self._trace(
                "steer",
                session_id,
                kind="steer_replaced",
                payload={"text": text, "source": source, "sender": who},
            )
            return
        if has_inflight(session_id):
            request_preempt(session_id, text, source=source, sender=who)
            self._trace(
                "steer",
                session_id,
                kind="steer_preempted",
                payload={"text": text, "source": source, "sender": who},
            )
            return
        if bool(getattr(session.controller, "inflight", False)):
            placed = self._peer_queue_at("head")
            self._enqueue(
                session_id,
                text=text,
                source=source,
                action="steer",
                at=placed,
                actor=who,
            )
            self._trace(
                "steer",
                session_id,
                kind="steer_after",
                payload={"text": text, "at": placed, "why": "no_llm", "source": source},
            )
            return
        session.controller.run_turn(f"/steer {text}", auto_hitl=False)
        self._trace(
            "steer",
            session_id,
            kind="steer_injected",
            payload={"text": text, "source": source, "sender": who},
        )

    def discard_last(self, session_id: str) -> str:
        session = self._require("discard_last", session_id)
        store = self._manager.checkpoint_store
        if store is None:
            raise RuntimeError("discard_last requires a checkpoint store")
        path = session.backtrack(store, steps=1)
        self._trace("discard_last", session_id, kind="checkpoint_restored", payload={"path": str(path)})
        return str(path)

    def inspect(self, session_id: str) -> SessionSnapshotView:
        session = self._require("inspect", session_id)
        tree: SnapshotTree = self._manager.snapshot_tree
        live = tree.live(session_id)
        cursor = tree.cursor(session_id)
        view = SessionSnapshotView(
            session_id=session_id,
            status=session.status.value,
            turn=int(getattr(session.controller, "_turn", 0)),
            live_snapshot_id=live.snapshot_id if live else None,
            cursor_snapshot_id=cursor.snapshot_id if cursor else None,
            spec_revision=getattr(session, "spec_revision", None),
        )
        self._trace(
            "inspect",
            session_id,
            kind="checkpoint_inspected",
            payload={"live": view.live_snapshot_id, "cursor": view.cursor_snapshot_id},
        )
        return view

    def inspect_context(self, session_id: str) -> ContextUsageView:
        """Return the latest numeric context snapshot and cache counters only."""
        session = self._require("inspect_context", session_id)
        driver = getattr(getattr(session, "instance", None), "driver", None)
        ctx = getattr(driver, "ctx", None)
        raw = getattr(ctx, "last_context_usage", None)
        usage = dict(raw) if isinstance(raw, dict) else {}

        observability = getattr(driver, "observability", None)
        events = list(getattr(observability, "events", ()) or ())
        llm_returns = [
            event
            for event in events
            if getattr(event, "kind", None) == ObsEventKind.ENGINE_IO_RETURN
            and (getattr(event, "payload", {}) or {}).get("op") == "LLM_CALL"
        ]
        latest_payload = dict(getattr(llm_returns[-1], "payload", {}) or {}) if llm_returns else {}
        hits_by_layer: dict[str, int] = {}
        misses_by_layer: dict[str, int] = {}
        call_statuses: dict[int, set[str]] = {}
        for event in llm_returns:
            payload = getattr(event, "payload", {}) or {}
            cache_events = payload.get("cache_events") or []
            if not cache_events and payload.get("cache_status") in {"hit", "miss"}:
                cache_events = [
                    {
                        "layer": payload.get("cache_layer") or "unknown",
                        "status": payload.get("cache_status"),
                    }
                ]
            for cache_event in cache_events:
                status = cache_event.get("status")
                layer = str(cache_event.get("layer") or "unknown")
                if status == "hit":
                    hits_by_layer[layer] = hits_by_layer.get(layer, 0) + 1
                elif status == "miss":
                    misses_by_layer[layer] = misses_by_layer.get(layer, 0) + 1
                if status in {"hit", "miss"}:
                    call_statuses.setdefault(int(getattr(event, "correlation_id", 0)), set()).add(status)
        strict_misses = 0
        for event in events:
            if getattr(event, "kind", None) != ObsEventKind.CACHE_LOOKUP:
                continue
            payload = getattr(event, "payload", {}) or {}
            if payload.get("op") != "LLM_CALL":
                continue
            layer = str(payload.get("cache_layer") or "unknown")
            if payload.get("cache_status") == "hit":
                hits_by_layer[layer] = hits_by_layer.get(layer, 0) + 1
            elif payload.get("cache_status") == "miss":
                misses_by_layer[layer] = misses_by_layer.get(layer, 0) + 1
                strict_misses += 1
            status = payload.get("cache_status")
            if status in {"hit", "miss"}:
                call_statuses.setdefault(int(getattr(event, "correlation_id", 0)), set()).add(status)

        hits = sum("hit" in statuses for statuses in call_statuses.values())
        misses = sum("hit" not in statuses and "miss" in statuses for statuses in call_statuses.values())
        attempts = hits + misses
        latest_usage = dict(latest_payload.get("usage") or {})
        latest_cache_events = latest_payload.get("cache_events") or []
        latest_is_cached = any(row.get("status") == "hit" for row in latest_cache_events)
        if not latest_is_cached and latest_payload.get("cache_status") == "hit":
            latest_is_cached = True
        usage_source = "cached_response" if latest_is_cached else "provider" if latest_usage else "unavailable"

        session_cost = 0.0
        session_cost_known = bool(llm_returns) or strict_misses > 0
        for event in llm_returns:
            payload = getattr(event, "payload", {}) or {}
            cache_events = payload.get("cache_events") or []
            if not cache_events and payload.get("cache_status") in {"hit", "miss"}:
                cache_events = [
                    {
                        "layer": payload.get("cache_layer") or "unknown",
                        "status": payload.get("cache_status"),
                    }
                ]
            response_cached = any(row.get("status") == "hit" for row in cache_events)
            if response_cached:
                continue
            cost = estimate_model_cost_usd(
                str(payload.get("model") or ""),
                payload.get("usage") or {},
                pricing_override=payload.get("pricing"),
            )
            if cost is None:
                session_cost_known = False
                break
            session_cost += cost
        estimated_cost = session_cost if session_cost_known else None
        cost_status = "catalog_estimate" if session_cost_known else "pricing_or_usage_unavailable"
        view = ContextUsageView(
            session_id=session_id,
            available=bool(usage),
            captured_at=str(usage.get("captured_at") or ""),
            model=str(usage.get("model") or latest_payload.get("model") or ""),
            context_window=usage.get("context_window"),
            estimated_prompt_tokens=int(usage.get("estimated_prompt_tokens") or 0),
            completion_reserve=int(usage.get("completion_reserve") or 0),
            estimated_remaining_tokens=usage.get("estimated_remaining_tokens"),
            fill_ratio=usage.get("fill_ratio"),
            token_breakdown=dict(usage.get("token_breakdown") or {}),
            context_parts=tuple(dict(part) for part in (usage.get("context_parts") or [])),
            latest_provider_usage=latest_usage,
            provider_usage_source=usage_source,
            cache_hits=hits,
            cache_misses=misses,
            cache_hit_rate=hits / attempts if attempts else None,
            cache_hits_by_layer=hits_by_layer,
            cache_misses_by_layer=misses_by_layer,
            estimated_cost_usd=estimated_cost,
            cost_status=cost_status,
            archive_view_id=str(usage.get("archive_view_id") or ""),
            archive_covering_ids=tuple(str(x) for x in (usage.get("archive_covering_ids") or [])),
            system_prefix_hash=str(usage.get("system_prefix_hash") or ""),
            system_prefix_reused=bool(usage.get("system_prefix_reused")),
        )
        self._trace(
            "inspect_context",
            session_id,
            kind="context_usage_inspected",
            payload={
                "available": view.available,
                "estimated_prompt_tokens": view.estimated_prompt_tokens,
                "cache_hits": hits,
                "cache_misses": misses,
            },
        )
        return view

    def inspect_archive(self, session_id: str) -> dict:
        """Dump the conversation archive tree (summaries + leaves) for debug."""
        session = self._require("inspect_archive", session_id)
        driver = getattr(getattr(session, "instance", None), "driver", None)
        ctx = getattr(driver, "ctx", None)
        store = getattr(ctx, "conversation_chunks", None)
        dump = store.inspect() if store is not None and hasattr(store, "inspect") else {}
        self._trace(
            "inspect_archive",
            session_id,
            kind="archive_inspected",
            payload={
                "view_id": dump.get("view_id"),
                "covering": len(dump.get("covering_ids") or []),
                "leaves": len(dump.get("leaves") or []),
            },
        )
        return dump

    def snapshot(self, session_id: str, *, label: str = "", auto_stop: bool = False) -> SnapshotRef:
        session = self._require("snapshot", session_id)
        self._ensure_stopped(session, session_id, auto_stop=auto_stop, method="snapshot")
        snap = session.take_snapshot(label=label or "control", kind="explicit")
        ref = snap.ref
        self._trace(
            "snapshot",
            session_id,
            kind="checkpoint_taken",
            payload={"snapshot_id": ref.snapshot_id, "label": ref.label},
        )
        return ref

    def persist(
        self,
        session_id: str,
        *,
        snapshot_id: str | None = None,
        label: str = "",
        auto_stop: bool = False,
    ) -> dict[str, str]:
        session = self._require("persist", session_id)
        self._ensure_stopped(session, session_id, auto_stop=auto_stop, method="persist")
        store = self._manager.checkpoint_store
        if store is None:
            raise RuntimeError(
                "persist requires a checkpoint store; start chat with --checkpoint-dir"
            )
        if snapshot_id:
            body = self._manager.snapshot_tree.body(snapshot_id)
            if body is None:
                raise KeyError(f"unknown snapshot {snapshot_id!r}")
            snap = body
        else:
            snap = session.take_snapshot(label=label or "persist", kind="explicit")
        path = session.persist_snapshot(snap, store)
        ref = snap.ref
        payload = {
            "path": str(path),
            "snapshot_id": ref.snapshot_id,
            "label": ref.label,
        }
        self._trace("persist", session_id, kind="checkpoint_persisted", payload=payload)
        return payload

    def run_script(
        self,
        session_id: str,
        *,
        text: str = "",
        script_file: str = "",
        auto_stop: bool = False,
    ) -> list[Any]:
        from mas.library.standard.plugins.control.script import (
            parse_control_script,
            resolve_curl_data,
            run_control_script,
        )

        self._require("run_script", session_id)
        body = str(text or "")
        if script_file:
            body = resolve_curl_data(
                script_file if str(script_file).startswith("@") else f"@{script_file}"
            )
        statements = parse_control_script(body)
        results = run_control_script(
            self,
            session_id,
            statements,
            default_auto_stop=auto_stop,
        )
        self._trace(
            "run_script",
            session_id,
            kind="control_script",
            payload={"commands": [item.raw for item in statements]},
        )
        return results

    def list_checkpoints(self, session_id: str) -> list[SnapshotRef]:
        self._require("list_checkpoints", session_id)
        nodes = self._manager.snapshot_tree.list_nodes(session_id)
        self._trace(
            "list_checkpoints",
            session_id,
            kind="checkpoint_listed",
            payload={"count": len(nodes), "ids": [n.snapshot_id for n in nodes]},
        )
        return nodes

    def navigate(self, session_id: str, *, to: str, reason: str) -> SnapshotRef:
        self._require("navigate", session_id)
        tree: SnapshotTree = self._manager.snapshot_tree
        current = tree.cursor(session_id)
        if callable(self.deny_navigate) and self.deny_navigate(to):
            self._trace(
                "navigate",
                session_id,
                kind="checkpoint_navigated",
                reason=reason,
                payload={"from": current.snapshot_id if current else None, "to": to},
                denied=True,
            )
            raise ControlDenied("navigate", session_id, reason=reason or "navigate denied")
        ref = tree.set_cursor(session_id, to)
        self._trace(
            "navigate",
            session_id,
            kind="checkpoint_navigated",
            reason=reason,
            payload={"from": current.snapshot_id if current else None, "to": ref.snapshot_id},
        )
        return ref

    def steer_modes(self, session_id: str) -> frozenset[str]:
        session = self._require("steer", session_id)
        return self._steer_modes(session)

    def _steer_modes(self, session: Any) -> frozenset[str]:
        plugin = getattr(
            getattr(getattr(session, "instance", None), "kernel", None),
            "plugin",
            None,
        )
        modes = getattr(plugin, "STEER_MODES", None)
        if plugin is None or modes is None:
            return frozenset({"after"})
        return frozenset(str(item) for item in modes)

    def _pattern_id(self, session: Any) -> str:
        plugin = getattr(
            getattr(getattr(session, "instance", None), "kernel", None),
            "plugin",
            None,
        )
        return str(getattr(plugin, "plugin_id", "") or "unknown")

    def _enqueue(
        self,
        session_id: str,
        *,
        text: str,
        source: str,
        priority: int = 0,
        action: Literal["turn", "steer"] = "turn",
        actor: str = "",
        at: QueueAt = "tail",
    ) -> str:
        return self._manager.turn_queue.enqueue(
            session_id,
            text,
            source=source,
            priority=priority,
            action=action,
            actor=actor or self.capability.actor,
            at=at,
        )

    def enqueue_input(
        self,
        session_id: str,
        *,
        text: str,
        source: str,
        priority: int = 0,
        action: Literal["turn", "steer"] = "turn",
        actor: str = "",
        at: QueueAt = "tail",
    ) -> str:
        self._require("enqueue_input", session_id)
        input_id = self._enqueue(
            session_id,
            text=text,
            source=source,
            priority=priority,
            action=action,
            actor=actor,
            at=at,
        )
        self._trace(
            "enqueue_input",
            session_id,
            kind="input_enqueued",
            payload={"input_id": input_id, "source": source, "action": action, "at": at},
        )
        return input_id

    def peek_queue(self, session_id: str) -> list[QueuedInputView]:
        self._require("peek_queue", session_id)
        items = self._manager.turn_queue.peek(session_id)
        self._trace(
            "peek_queue",
            session_id,
            kind="queue_inspected",
            payload={"count": len(items)},
        )
        return items

    def inspect_queue(self, session_id: str) -> QueueView:
        self._require("inspect_queue", session_id)
        view = self._manager.turn_queue.inspect(session_id)
        self._trace(
            "inspect_queue",
            session_id,
            kind="queue_inspected",
            payload={"revision": view.revision, "count": len(view.items)},
        )
        return view

    def reorder_queue(
        self,
        session_id: str,
        order: list[str],
        *,
        revision: int | None = None,
    ) -> None:
        self._require("reorder_queue", session_id)
        new_revision = self._manager.turn_queue.reorder(session_id, order, revision=revision)
        self._trace(
            "reorder_queue",
            session_id,
            kind="queue_reordered",
            payload={"order": list(order), "revision": new_revision},
        )

    def cancel_queued(
        self,
        session_id: str,
        input_id: str,
        *,
        revision: int | None = None,
    ) -> None:
        self._require("cancel_queued", session_id)
        new_revision = self._manager.turn_queue.cancel(session_id, input_id, revision=revision)
        self._trace(
            "cancel_queued",
            session_id,
            kind="input_cancelled",
            payload={"input_id": input_id, "revision": new_revision},
        )

    def set_queued_action(
        self,
        session_id: str,
        input_id: str,
        *,
        action: Literal["turn", "steer"],
        revision: int | None = None,
    ) -> None:
        self._require("set_queued_action", session_id)
        new_revision = self._manager.turn_queue.set_action(
            session_id, input_id, action=action, revision=revision
        )
        self._trace(
            "set_queued_action",
            session_id,
            kind="queue_action_changed",
            payload={"input_id": input_id, "action": action, "revision": new_revision},
        )

    def cancel_inflight(self, session_id: str) -> bool:
        """Cancel the in-flight LLM ``ainvoke``. Tools stay soft-interrupt only."""
        self._require("cancel_inflight", session_id)
        from mas.runtime.engine.inflight_llm import cancel as cancel_llm

        cancelled = cancel_llm(session_id)
        self._trace(
            "cancel_inflight",
            session_id,
            kind="inflight_cancelled",
            payload={"cancelled": cancelled},
        )
        return cancelled

    def fork_investigation(self, session_id: str) -> str:
        session = self._require("fork_investigation", session_id)
        origin = session.take_snapshot(label="investigate-origin")
        child = session.take_snapshot(label="investigate", live=False)
        self._trace(
            "fork_investigation",
            session_id,
            kind="branch_opened",
            payload={
                "from": origin.ref.snapshot_id,
                "child": child.ref.snapshot_id,
                "parent_status": session.status.value,
            },
        )
        return child.ref.snapshot_id

    def disable_tool(self, session_id: str, *, name: str, reason: str) -> int:
        session = self._require("disable_tool", session_id)
        from mas.runtime.session.spec_revision import SpecDelta
        from mas.runtime.session.state import ManifestRef

        rev = self._manager.spec_log.apply(
            session_id,
            SpecDelta("disable_tool", {"name": name}),
            actor=self.capability.actor,
            reason=reason,
        )
        session.spec_revision = rev.revision
        session.manifest_ref = ManifestRef.from_content(self._manager.spec_log.current_manifest(session_id))
        ctx = getattr(getattr(session.instance, "driver", None), "ctx", None)
        if ctx is not None:
            from mas.runtime.engine.tools import is_spawn_subagent_enabled, spawn_subagent_params

            spec = self._manager.spec_log.current_spec(session_id)
            ctx.current_spec = spec
            ctx.allow_subagent_spawning = is_spawn_subagent_enabled(spec)
            ctx.subagent_templates = list((spawn_subagent_params(spec) or {}).get("templates") or [])
        self._trace(
            "disable_tool",
            session_id,
            kind="spec_revised",
            reason=reason,
            payload={"revision": rev.revision, "hash": rev.content_hash, "tool": name},
        )
        return rev.revision
