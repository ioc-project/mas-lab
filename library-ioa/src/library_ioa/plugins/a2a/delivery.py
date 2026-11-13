# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Optional policy at the A2A delivery boundary, independent of agent execution."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol, TypeVar

from a2a.server.context import ServerCallContext
from a2a.utils.errors import InvalidAgentResponseError
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message
from mas.runtime.boundary.connection import ConnectionInfo

logger = logging.getLogger(__name__)
DeliveryStatus = Literal["allowed", "denied", "error", "timeout", "cancelled"]
DeliveryKind = Literal["send", "get_task", "list_tasks", "cancel_task", "push"]
_M = TypeVar("_M", bound=Message)


class DeliveryDecision(Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class DeliveryCandidate:
    """Immutable snapshot of the exact A2A payload proposed for release.

    ``payload`` and ``request_metadata`` are UTF-8 JSON bytes, not mutable SDK
    objects. Metadata is correlation information, not authenticated identity.
    ``recipient_id`` comes from the SDK's authenticated user, or is empty.
    Push uses ``destination`` (the configured callback URL) instead.
    """

    agent_id: str
    recipient_id: str
    destination: str
    task_id: str
    context_id: str
    kind: DeliveryKind
    payload: bytes
    request_metadata: bytes
    connection: ConnectionInfo | None = None


class DeliveryPolicy(Protocol):
    async def authorize(self, candidate: DeliveryCandidate) -> DeliveryDecision:
        """Decide on this candidate without changing it or executing the agent."""
        ...

    async def aclose(self) -> None:
        """Release policy-owned resources when the exposure shuts down."""
        ...


@dataclass(frozen=True)
class DeliveryOutcome:
    """Release decision, with candidate content only after approval."""

    agent_id: str
    recipient_id: str
    task_id: str
    context_id: str
    kind: DeliveryKind
    status: DeliveryStatus
    payload: bytes | None


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class DeliveryController:
    """One exposure's policy, bounded decisions and cleanup.

    Decisions are not cached here. A policy can bind decisions to its own
    candidate/recipient keys across retrieval and push without task resumption.
    """

    def __init__(self, agent_id: str, policy: DeliveryPolicy, *, timeout: float = 30) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("A2A delivery timeout must be finite and positive")
        if not callable(getattr(policy, "authorize", None)) or not callable(getattr(policy, "aclose", None)):
            raise TypeError("A2A delivery policy requires authorize and aclose methods")
        self.agent_id = agent_id
        self.policy = policy
        self.timeout = timeout
        self._pending: set[asyncio.Task[DeliveryDecision]] = set()
        self._closed = False
        self._closing = asyncio.Event()

    async def release(
        self,
        value: _M,
        *,
        kind: DeliveryKind,
        context: ServerCallContext | None = None,
        request: Message | None = None,
        destination: str = "",
        task_id: str = "",
        connection: ConnectionInfo | None = None,
    ) -> _M:
        if self._closed:
            raise InvalidAgentResponseError(message="A2A delivery policy is closed")
        # Isolate the wire response from the task store and from policy code.
        snapshot = type(value)()
        snapshot.CopyFrom(value)
        data = MessageToDict(snapshot)
        nested = next((data[k] for k in ("task", "message", "statusUpdate", "artifactUpdate") if k in data), data)
        metadata = {}
        if request is not None:
            req = MessageToDict(request)
            metadata = {"request": req.get("metadata", {}), "message": req.get("message", {}).get("metadata", {})}
        candidate = DeliveryCandidate(
            agent_id=self.agent_id,
            recipient_id=context.user.user_name if context and context.user.is_authenticated else "",
            destination=destination,
            task_id=task_id or nested.get("id", nested.get("taskId", "")),
            context_id=nested.get("contextId", ""),
            kind=kind,
            payload=_json(data),
            request_metadata=_json(metadata),
            connection=connection or (context.state.get("mas.delivery.connection") if context else None),
        )
        status: DeliveryStatus = "error"
        pending = None
        closing = asyncio.create_task(self._closing.wait())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        try:
            pending = asyncio.create_task(self.policy.authorize(candidate))
            self._pending.add(pending)
            pending.add_done_callback(self._decision_done)
            done, _ = await asyncio.wait((pending, closing), timeout=self.timeout, return_when=asyncio.FIRST_COMPLETED)
            # Cancellation acknowledgement is not authorization. In particular a
            # policy may catch CancelledError and return ALLOW after its deadline.
            if self._closed:
                status = "cancelled"
                raise InvalidAgentResponseError(message="A2A delivery policy is closed")
            if pending not in done or loop.time() >= deadline:
                raise TimeoutError
            decision = pending.result()
            if decision is DeliveryDecision.ALLOW:
                status = "allowed"
                return snapshot
            if decision is DeliveryDecision.DENY:
                status = "denied"
            raise InvalidAgentResponseError(message="A2A response denied by delivery policy")
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except TimeoutError:
            status = "timeout"
            raise InvalidAgentResponseError(message="A2A delivery policy timed out") from None
        except InvalidAgentResponseError:
            raise InvalidAgentResponseError(message="A2A response denied by delivery policy") from None
        except Exception:
            # Policy exceptions can contain candidate content or credentials.
            raise InvalidAgentResponseError(message="A2A delivery policy failed") from None
        finally:
            closing.cancel()
            if pending is not None and not pending.done():
                pending.cancel()
            observer = getattr(self.policy, "on_release", None)
            if callable(observer):
                outcome = DeliveryOutcome(
                    candidate.agent_id,
                    candidate.recipient_id,
                    candidate.task_id,
                    candidate.context_id,
                    kind,
                    status,
                    candidate.payload if status == "allowed" else None,
                )
                try:
                    observer(outcome)
                except Exception:
                    logger.warning("A2A release outcome observer failed")

    def _decision_done(self, task: asyncio.Task) -> None:
        self._pending.discard(task)
        if not task.cancelled():
            # Retrieve late exceptions without exposing policy data in logs.
            task.exception()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._closing.set()
        pending = tuple(self._pending)
        for task in pending:
            task.cancel()
        cleanup = asyncio.create_task(self.policy.aclose())
        cleanup.add_done_callback(self._decision_done)
        tasks = (*pending, cleanup)
        try:
            _, unfinished = await asyncio.wait(tasks, timeout=self.timeout)
            if cleanup.done() and not cleanup.cancelled():
                cleanup.result()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
        if unfinished:
            logger.warning("A2A delivery policy cleanup exceeded its deadline")
