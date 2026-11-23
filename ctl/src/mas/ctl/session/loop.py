#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Agent loops as ctl hosts them: the default kernel loop, and the loop an agent spec names.

``KernelLoop`` is the kernel main loop behind the loop interface: it queues the task onto the
conversation's session and returns what the turn answered. It is the loop whenever a spec names none.
``loop_from_spec`` builds the loop ``spec.loop`` names and wraps it with governance and
observability at the boundary.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mas.ctl.session.mailbox import SessionTurnMailbox
from mas.runtime.boundary.loop import AgentLoop, LoopReply, LoopTask
from mas.runtime.session import SessionStatus

logger = logging.getLogger(__name__)


class KernelLoop:
    """The kernel main loop as an agent loop: one session (kernel, context, engine) per conversation."""

    loop_id = "kernel"

    def __init__(self, *, mailbox: SessionTurnMailbox, ensure_session: Callable[[str], Any]) -> None:
        self._mailbox = mailbox
        self._ensure_session = ensure_session

    def run(self, task: LoopTask) -> LoopReply:
        from mas.runtime.boundary.control.contract import SessionPaused

        resolved_session = self._mailbox.resolve_session_id(task.session_id)
        session = self._ensure_session(resolved_session)

        def run(text: str) -> Any:
            if session.status == SessionStatus.PAUSED:
                raise SessionPaused(resolved_session, reason=session.pause_reason)
            # The controller's turn path keeps the conversation: it commits each turn to
            # the context, so the next message on this contextId sees the earlier ones.
            return session.controller.run_turn(
                text,
                turn_id=task.task_id or None,
                auto_hitl=False,
                parent_call_id=task.parent_call_id,
                upstream_correlation_id=task.upstream_correlation_id,
                dequeued=True,
            ).trace

        trace = self._mailbox.submit(resolved_session, task.text, source="a2a", run=run)
        return reply_from_trace(trace, resolved_session)

    def close(self) -> None:
        """Nothing to release: the conversations it opened close with the handler that owns them."""


def reply_from_trace(trace: Any, session_id: str) -> LoopReply:
    """What a finished kernel turn answered, as a :class:`LoopReply`."""
    responses = trace.client_responses
    text = "\n".join(response.content for response in responses if getattr(response, "content", "")).strip()
    stream = tuple(chunk for response in responses for chunk in (getattr(response, "stream_chunks", ()) or ()))
    artifacts = tuple(artifact for response in responses for artifact in (getattr(response, "artifacts", ()) or ()))
    task_states = [response.task_state for response in responses if getattr(response, "task_state", None)]
    task_state = task_states[-1] if task_states else ""
    if getattr(trace, "awaiting_hitl", False):
        task_state = "input_required"
    return LoopReply(text=text, context_id=session_id, artifacts=artifacts, task_state=task_state, stream=stream)


def handler_result(reply: LoopReply) -> Any:
    """The value an ``agent_expose`` handler returns: the chunks when the loop streamed, else a dict."""
    if reply.stream:
        return reply.stream
    result: dict[str, Any] = {"text": reply.text, "context_id": reply.context_id}
    if reply.artifacts:
        result["artifacts"] = list(reply.artifacts)
    if reply.task_state:
        result["task_state"] = reply.task_state
    return result


@dataclass
class LoopHost:
    """A loop the spec named, wrapped for the boundary, and what to close with it."""

    loop: AgentLoop
    shell: Any
    recorder: Any = None

    def close(self) -> None:
        """Close the loop, then whatever records its events (the recorder when set, else the shell's plugins)."""
        try:
            self.loop.close()
        finally:
            if self.recorder is not None:
                self.recorder.close()
            elif self.shell.obs_plugin_set is not None:
                self.shell.obs_plugin_set.close()


def loop_binding(manifest: dict[str, Any] | None) -> dict[str, Any] | None:
    """``spec.loop`` as ``{type, params}``, or None when the spec names no loop (the kernel loop runs)."""
    raw = ((manifest or {}).get("spec") or {}).get("loop")
    if raw is None:
        return None
    from mas.runtime.spec.plugin_binding import normalize_plugin_binding

    return normalize_plugin_binding(raw, field="spec.loop")


def loop_from_spec(
    manifest: dict[str, Any],
    *,
    manifest_dir: Path | None = None,
    flavour_spec: dict[str, Any] | None = None,
    obs_binding_override: Any | None = None,
    obs_from_cli: bool = False,
) -> LoopHost:
    """Build the loop ``spec.loop`` names, behind the agent's governance and observability.

    No kernel runs for the agent. The spec, with the Controls the Flavour attaches, is parsed as it is
    for a kernel agent, which gives the governance chain and the observability plugins; a ``RuntimeInstance``
    that never takes a turn holds them, and nothing else of it is used. A loop the registry does not
    know, or one its arguments refuse (a command that is not allow-listed), is a ``ValueError``.
    """
    from mas.ctl.session.flavour_control import apply_flavour_controls
    from mas.ctl.session.manifest_config import agent_id_from_manifest, spec_with_control_lists
    from mas.library.standard.plugins.loop.governed import GovernedLoop
    from mas.runtime.driver.instance import RuntimeInstance
    from mas.runtime.registry import get_registry

    binding = loop_binding(manifest)
    if binding is None:
        raise ValueError("the spec names no loop")
    agent_id = agent_id_from_manifest(manifest)
    spec = dict(manifest.get("spec") or {})
    if flavour_spec:
        apply_flavour_controls(manifest, flavour_spec, agent_id=agent_id, entry_id=agent_id, delegate_ids=[])
        spec = spec_with_control_lists(spec, flavour_spec, agent_id=agent_id, entry_id=agent_id, delegate_ids=[])
    spec.pop("loop", None)
    shell = RuntimeInstance.from_spec(
        spec,
        base_dir=manifest_dir,
        agent_id=agent_id,
        obs_binding_override=obs_binding_override,
        obs_from_cli=obs_from_cli,
    )
    try:
        inner = get_registry().create("agent_loop", binding=binding)
    except (KeyError, TypeError) as exc:
        raise ValueError(f"spec.loop: {exc}") from exc
    observer = shell.driver.observability
    if observer is not None:
        observer.set_context(agent_id=agent_id)
    return LoopHost(
        loop=GovernedLoop(inner, config=shell.kernel.config, observer=observer, agent_id=agent_id),
        shell=shell,
    )
