#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Agent loop that drives a remote A2A agent: send the task, return the reply."""

from __future__ import annotations

import threading

from mas.runtime.boundary.agentcomm.protocol import AgentCommContract, AgentCommError, SendContext
from mas.runtime.boundary.loop import LoopError, LoopReply, LoopTask
from mas.runtime.driver.sync_bridge import SyncBridge

from library_ioa.plugins.a2a.agent_comm import A2AAgentComm


class A2AHarnessLoop:
    """Send each task to a remote A2A agent and return its reply.

    The send goes through an ``AgentCommContract``: by default the ``a2a`` link at ``url`` (the A2A SDK
    client, with ``headers`` and ``timeout``); or any other link or a built ``agent_comm/stack`` passed as
    ``comm``, so the layers above the transport (identity, redaction, retry) apply to the task as they
    apply to a peer send. The task's session id is the A2A ``contextId``; ``parent_call_id`` and the
    upstream correlation id ride the message metadata as ``mas.caller_call_id`` / ``mas.correlation_id``.

    Only the reply text comes back: the A2A link returns text, so the remote agent's own tool events are
    not visible here. A ``comm`` passed in belongs to the caller: ``close`` leaves it open.
    """

    loop_id = "a2a-harness"

    def __init__(
        self,
        url: str | None = None,
        *,
        target: str = "harness",
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        comm: AgentCommContract | None = None,
    ) -> None:
        if (url is None) == (comm is None):
            raise ValueError("a2a-harness takes a url, or a comm, not both and not neither")
        self._owned = A2AAgentComm(str(url), headers=headers, timeout=timeout) if comm is None else None
        self._comm: AgentCommContract = comm if comm is not None else self._owned
        self._target = target
        self._bridge = SyncBridge()
        self._lock = threading.Lock()

    def run(self, task: LoopTask) -> LoopReply:
        send = self._comm.asend(
            self._target,
            task.text,
            correlation_id=task.upstream_correlation_id or 0,
            caller_call_id=task.parent_call_id,
            context_id=task.session_id,
            headers=dict(task.headers) or None,
            context=SendContext(headers=dict(task.headers)),
        )
        try:
            with self._lock:
                text = self._bridge.run(send)
        except AgentCommError as exc:
            raise LoopError(str(exc)) from exc
        return LoopReply(text=str(text), context_id=task.session_id)

    def close(self) -> None:
        with self._lock:
            self._bridge.close()
        if self._owned is not None:
            self._owned.stop()
