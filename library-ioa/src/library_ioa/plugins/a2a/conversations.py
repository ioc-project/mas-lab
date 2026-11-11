# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Recipient-issued contexts for opt-in peer conversations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from mas.runtime.boundary.agentcomm.protocol import AgentCommError, current_conversation


@dataclass
class Relationship:
    lock: Any = field(default_factory=Lock)
    context_id: str = ""
    unresolved: bool = False


def response_context(response: dict[str, Any]) -> str:
    """Inspect protocol envelopes only, never tool output or message text."""
    found: set[str] = set()
    pending = [response]
    while pending:
        value = pending.pop()
        if not isinstance(value, dict):
            continue
        for key in ("context_id", "contextId"):
            if key in value:
                context_id = value[key]
                if not isinstance(context_id, str) or not context_id.strip():
                    raise AgentCommError("Missing or invalid A2A response context")
                found.add(context_id)
        pending.extend(value.get(key) for key in ("result", "task", "message", "status"))
    if len(found) != 1:
        raise AgentCommError("Missing or conflicting A2A response contexts")
    return found.pop()


class PeerConversations:
    def __init__(self) -> None:
        self._lock = Lock()
        self._relationships: dict[tuple[str, str, str, str], Relationship] = {}

    def relationship(self, recipient: str, thread: str = "") -> Relationship:
        caller = current_conversation()
        if caller is None or not caller.session_id or not caller.agent_id:
            raise AgentCommError("Automatic A2A reuse requires an active caller conversation")
        key = (caller.session_id, caller.agent_id, recipient, thread)
        with self._lock:
            return self._relationships.setdefault(key, Relationship())

    def send(self, client: Any, recipient: str, task: str, *, thread: str, metadata: dict[str, str]) -> dict[str, Any]:
        relation = self.relationship(recipient, thread)
        with relation.lock:
            if relation.unresolved:
                raise AgentCommError("A2A conversation context is unresolved; do not retry an ambiguous operation")
            try:
                response = client.send_message(task, context_id=relation.context_id, metadata=metadata)
            except Exception:
                if not relation.context_id:
                    relation.unresolved = True
                raise
            return self._capture(relation, response)

    @staticmethod
    def _capture(relation: Relationship, response: dict[str, Any]) -> dict[str, Any]:
        try:
            received = response_context(response)
            if relation.context_id and received != relation.context_id:
                raise AgentCommError("A2A response changed an established context")
        except AgentCommError:
            relation.unresolved = True
            raise
        # Capture before native conversion raises for a failed remote task.
        relation.context_id = received
        return response

    async def asend(
        self, client: Any, recipient: str, task: str, *, thread: str, metadata: dict[str, str]
    ) -> dict[str, Any]:
        relation = self.relationship(recipient, thread)
        # One lock also protects synchronous callers. Never leave a worker
        # blocked on its acquisition after the async caller has been cancelled.
        while not relation.lock.acquire(blocking=False):
            await asyncio.sleep(0.01)
        try:
            if relation.unresolved:
                raise AgentCommError("A2A conversation context is unresolved; do not retry an ambiguous operation")
            try:
                response = await client.asend_message(task, context_id=relation.context_id, metadata=metadata)
            except (Exception, asyncio.CancelledError):
                if not relation.context_id:
                    relation.unresolved = True
                raise
            return self._capture(relation, response)
        finally:
            relation.lock.release()

    def clear(self) -> None:
        with self._lock:
            self._relationships.clear()
