#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared A2A (agent_expose) listeners for every live-agent entry point.

``mas-ctl serve``, ``chat``, ``tui``, and ``run-mas`` host ``RuntimeInstance``
objects. When infra marks those hosted agents ``usage: deploy`` or
``use-and-deploy``, this module binds the advertised URL through the
``agent_expose`` plugin.

Dedicated ``serve`` is blocking (the process *is* the server). Chat / TUI /
run-mas keep stdin or curses as the conversation and bind in the background
for interrogation. ``usage: use`` peers stay client routes; another process
owns those listen ports.
"""

from __future__ import annotations

import logging
import socket
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from mas.ctl.infra.resolve import application_endpoint_is_deployed
from mas.ctl.session.mailbox import SessionTurnMailbox
from mas.runtime.session import SessionStatus

logger = logging.getLogger(__name__)

_WILDCARD_HOSTS = {"0.0.0.0", "::", "[::]"}


class _SilentDisplay:
    def on_user(self, text: str, *, turn_id: str = "") -> None:
        return None

    def on_agent(self, text: str) -> None:
        return None

    def on_turn_error(self, message: str, *, detail: str = "") -> None:
        return None

    def on_hitl_request(self, request: Any) -> None:
        return None

    def on_system(self, message: str) -> None:
        return None

    def on_error(self, message: str) -> None:
        return None


def make_runtime_handler(
    instance: Any,
    *,
    manifest: dict[str, Any] | None = None,
    control_dir: Path | None = None,
) -> Any:
    """Queue A2A ``message/send`` onto ``instance.run_user_text`` through one mailbox."""
    from mas.ctl.adapters.checkpoint import InMemoryCheckpointStore
    from mas.ctl.session.control_host import ControlDirectoryHost
    from mas.ctl.session.controller import SessionController
    from mas.ctl.session.manager import SessionManager
    from mas.runtime.boundary.control.contract import ControlCapability, SessionPaused

    manager = SessionManager(checkpoint_store=InMemoryCheckpointStore())
    mailbox = SessionTurnMailbox(queue=manager.turn_queue)
    host: ControlDirectoryHost | None = None
    exchange_hub = None
    if control_dir is not None:
        from mas.runtime.boundary.obs.exchange_hub import ExchangeHub

        exchange_hub = ExchangeHub()
        host = ControlDirectoryHost(
            manager.control(capability=ControlCapability(actor="admin", surface="admin")),
            control_dir,
            hub=exchange_hub,
        )

    def ensure_session(session_id: str) -> Any:
        if session_id not in manager.sessions:
            controller = SessionController(
                instance=instance,
                display=_SilentDisplay(),
                exchange_hub=exchange_hub,
            )
            manager.create(instance, controller, manifest or {}, session_id=session_id)
            if host is not None:
                host.advertise(session_id)
        return manager.get(session_id)

    def handle(
        prompt: str,
        *,
        turn_id: str | None = None,
        session_id: str | None = None,
        parent_call_id: str | None = None,
        upstream_correlation_id: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        resolved_session = mailbox.resolve_session_id(session_id)
        session = ensure_session(resolved_session)

        def run(text: str) -> Any:
            if session.status == SessionStatus.PAUSED:
                raise SessionPaused(resolved_session, reason=session.pause_reason)
            return instance.run_user_text(
                text,
                turn_id=turn_id or "u1",
                session_id=resolved_session,
                parent_call_id=parent_call_id or "",
                upstream_correlation_id=upstream_correlation_id,
            )

        trace = mailbox.submit(resolved_session, prompt, source="a2a", run=run)
        text = "\n".join(
            response.content
            for response in trace.client_responses
            if getattr(response, "content", "")
        ).strip()
        stream_chunks = tuple(
            chunk
            for response in trace.client_responses
            for chunk in (getattr(response, "stream_chunks", ()) or ())
        )
        if stream_chunks:
            return stream_chunks
        result: dict[str, Any] = {"text": text, "context_id": resolved_session}
        artifacts = [
            artifact
            for response in trace.client_responses
            for artifact in (getattr(response, "artifacts", ()) or ())
        ]
        if artifacts:
            result["artifacts"] = artifacts
        task_states = [
            response.task_state
            for response in trace.client_responses
            if getattr(response, "task_state", None)
        ]
        if task_states:
            result["task_state"] = task_states[-1]
        if getattr(trace, "awaiting_hitl", False):
            result["task_state"] = "input_required"
        return result

    handle.host = host
    handle.manager = manager
    return handle


def exposure_targets(
    hosted_ids: Iterable[str],
    applications: dict[str, dict[str, Any]],
    *,
    conversation_ids: Iterable[str],
) -> list[str]:
    """Hosted agents this process should bind.

    Every hosted A2A endpoint with ``usage: deploy`` or ``use-and-deploy`` is
    bound here. ``usage: use`` stays a client route so a remote
    ``mas-ctl serve`` (or ``--bind``) keeps that listen port.
    """
    owned = [str(item) for item in conversation_ids if str(item).strip()]
    hosted = [str(agent_id) for agent_id in hosted_ids]
    ordered = [agent_id for agent_id in owned if agent_id in hosted]
    ordered.extend(agent_id for agent_id in hosted if agent_id not in ordered)
    return [agent_id for agent_id in ordered if _should_expose(applications.get(agent_id))]


def _should_expose(endpoint: dict[str, Any] | None) -> bool:
    if not isinstance(endpoint, dict):
        return False
    protocol = str(endpoint.get("protocol") or "").strip()
    if protocol != "a2a":
        return False
    return application_endpoint_is_deployed(endpoint)


def start_hosted_exposures(
    instances: dict[str, Any],
    applications: dict[str, dict[str, Any]],
    *,
    conversation_ids: Iterable[str],
    manifests: dict[str, dict[str, Any]] | None = None,
) -> list[Any]:
    """Bind background listeners for hosted deploy targets. Close the handles."""
    provided = dict(manifests or {})
    handles: list[Any] = []
    for agent_id in exposure_targets(
        instances,
        applications,
        conversation_ids=conversation_ids,
    ):
        instance = instances[agent_id]
        endpoint = applications[agent_id]
        manifest = provided.get(agent_id)
        if not isinstance(manifest, dict):
            manifest = {"metadata": {"name": agent_id}, "spec": {}}
        try:
            handles.append(
                expose_agent(instance, manifest, endpoint, blocking=False)
            )
        except Exception:
            close_exposures(handles)
            raise
    return handles


def start_materialized_exposures(
    materialized: Any,
    *,
    entry_id: str,
    manifests: dict[str, dict[str, Any]] | None = None,
) -> list[Any]:
    """run-mas adapter: unwrap compose + instances, then bind."""
    from mas.ctl.executor.mas_session import load_agent_manifest_from_bind

    compose = getattr(materialized, "compose", None)
    inner = getattr(materialized, "materialized", None)
    instances = dict(getattr(inner, "instances", None) or {})
    applications = dict(getattr(getattr(compose, "resolved_infra", None), "applications", None) or {})
    provided = dict(manifests or {})
    binds = getattr(getattr(compose, "bind", None), "agents", None) or ()
    for agent_id in exposure_targets(instances, applications, conversation_ids=[entry_id]):
        if agent_id in provided:
            continue
        if binds:
            loaded = load_agent_manifest_from_bind(compose.bind, agent_id)
            if isinstance(loaded, dict):
                provided[agent_id] = loaded
    return start_hosted_exposures(
        instances,
        applications,
        conversation_ids=[entry_id],
        manifests=provided,
    )


def expose_agent(
    instance: Any,
    manifest: dict[str, Any],
    endpoint: dict[str, Any],
    *,
    blocking: bool = False,
    control_dir: Path | None = None,
) -> Any:
    """Create the ``agent_expose`` plugin and serve this instance.

    ``blocking=True`` is ``mas-ctl serve`` (process is the server, never
    returns until the listener stops). ``blocking=False`` is interrogation
    next to chat / TUI / run-mas.
    """
    from mas.runtime.registry import get_registry

    protocol = str(endpoint.get("protocol") or "").strip()
    if not protocol:
        raise ValueError("Application endpoint requires protocol")
    exposure = get_registry().create(
        "agent_expose",
        binding={"type": protocol},
        endpoint=endpoint,
    )
    handler = make_runtime_handler(instance, manifest=manifest, control_dir=control_dir)
    if blocking:
        logger.info(
            "agent %r: A2A listening on %s",
            (manifest.get("metadata") or {}).get("name") or "",
            endpoint.get("url") or "",
        )
        try:
            exposure.serve_blocking(manifest, handler)
            return None
        finally:
            host = getattr(handler, "host", None)
            if host is not None:
                host.close()
    handle = exposure.serve_background(manifest, handler)
    try:
        _wait_listening(
            handle.host,
            handle.port,
            thread=getattr(handle.server, "thread", None),
        )
    except Exception:
        close_exposures([handle])
        raise
    url = str(endpoint.get("url") or "").strip() or f"http://{handle.host}:{handle.port}"
    logger.info("agent %r: A2A interrogation listening on %s", (manifest.get("metadata") or {}).get("name") or "", url)
    return handle


@contextmanager
def hosted_exposures(
    instances: dict[str, Any],
    applications: dict[str, dict[str, Any]],
    *,
    conversation_ids: Iterable[str],
    manifests: dict[str, dict[str, Any]] | None = None,
) -> Iterator[list[Any]]:
    handles = start_hosted_exposures(
        instances,
        applications,
        conversation_ids=conversation_ids,
        manifests=manifests,
    )
    try:
        yield handles
    finally:
        close_exposures(handles)


def close_exposures(handles: Iterable[Any]) -> None:
    for handle in handles:
        if handle is None:
            continue
        closer = getattr(handle, "close", None)
        if not callable(closer):
            continue
        try:
            closer()
        except Exception:
            logger.exception("failed to close A2A interrogation listener")


def _wait_listening(
    host: str,
    port: int,
    *,
    thread: Any = None,
    timeout: float = 5.0,
) -> None:
    probe = "127.0.0.1" if host in _WILDCARD_HOSTS else host
    deadline = time.monotonic() + timeout
    last_exc: OSError | None = None
    while time.monotonic() < deadline:
        if thread is not None and not thread.is_alive():
            raise RuntimeError(f"A2A server thread exited before binding {host}:{port}")
        try:
            with socket.create_connection((probe, int(port)), timeout=0.2):
                return
        except OSError as exc:
            last_exc = exc
            time.sleep(0.05)
    raise RuntimeError(f"A2A server did not listen on {host}:{port}") from last_exc
