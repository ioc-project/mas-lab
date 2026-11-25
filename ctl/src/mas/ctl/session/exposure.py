#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Shared A2A (agent_expose) listeners for every live-agent entry point.

``mas-ctl serve``, ``chat``, ``tui``, and ``run-mas`` host ``RuntimeInstance``
objects. When infra marks those hosted agents ``usage: listen`` or
``[connect, listen]``, this module binds the advertised URL through the
``agent_expose`` plugin.

Dedicated ``serve`` is blocking (the process *is* the server). Chat / TUI /
run-mas keep stdin or curses as the conversation and bind in the background
for interrogation. ``usage: connect`` peers stay client routes; another process
owns those listen ports.
"""

from __future__ import annotations

import logging
import re
import socket
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Iterator

from mas.ctl.infra.resolve import application_endpoint_is_deployed
from mas.ctl.session.loop import KernelLoop, handler_result
from mas.ctl.session.mailbox import SessionTurnMailbox
from mas.runtime.boundary.loop import AgentLoop, LoopTask

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


MAX_CONVERSATIONS = 1024
_KEYED = re.compile(r"^[0-9a-f]{16}~")


def session_key(caller: str, context_id: str) -> str:
    """The key one conversation is stored under: the caller and the ``contextId`` together.

    A ``contextId`` is chosen by whoever sends it. Keyed by it alone, a second caller that learns or
    guesses an id joins the first caller's conversation and reads its history. With a verified
    ``caller`` the key holds both, so the same id from two callers is two conversations. A listener
    that verifies no one has no caller (``""``): the key is the id, as before.
    """
    if not caller:
        if _KEYED.match(context_id):
            raise ValueError("a contextId cannot look like a caller-keyed session id")
        return context_id
    return f"{sha256(caller.encode('utf-8')).hexdigest()[:16]}~{context_id}"


class TooManyConversations(RuntimeError):
    """A new ``contextId`` cannot be opened: the limit is reached, or the agent cannot be rebuilt."""


def make_runtime_handler(
    instance: Any,
    *,
    manifest: dict[str, Any] | None = None,
    control_dir: Path | None = None,
    max_conversations: int = MAX_CONVERSATIONS,
    conversation_factory: Callable[[], Any] | None = None,
) -> Any:
    """Queue A2A ``message/send`` onto one runtime per conversation.

    ``session_id`` is A2A ``contextId``: pass it to continue that conversation,
    omit it to start a new one. ``caller`` is the verified caller, when the listener verifies one:
    a conversation is then keyed by ``(caller, contextId)`` (:func:`session_key`).
    Every conversation runs on its own runtime
    (kernel, context, engine), built by ``conversation_factory`` or, by
    default, ``instance.conversation_factory``. The template ``instance`` is
    never used for a conversation, so conversations never wait on each other
    or see each other's state, whether they run on one thread or several.

    An agent that cannot be rebuilt (no factory) serves the one conversation
    it was given, ``instance`` itself, and refuses a second ``contextId``.
    At most ``max_conversations`` stay open; a further new ``contextId``
    raises :class:`TooManyConversations` instead of growing without bound.
    """
    from mas.ctl.adapters.checkpoint import InMemoryCheckpointStore
    from mas.ctl.session.control_host import ControlDirectoryHost
    from mas.ctl.session.controller import SessionController
    from mas.ctl.session.manager import SessionManager
    from mas.runtime.boundary.control.contract import ControlCapability

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

    factory = conversation_factory or getattr(instance, "conversation_factory", None)
    open_lock = threading.Lock()
    opened: list[Any] = []

    def _instance_for_session() -> Any:
        if factory is None:
            if opened:
                raise TooManyConversations(
                    "this agent was started without a way to rebuild it, so it serves one conversation"
                )
            return instance
        return factory()

    def ensure_session(session_id: str) -> Any:
        with open_lock:
            return _ensure_session_locked(session_id)

    def _ensure_session_locked(session_id: str) -> Any:
        if session_id not in manager.sessions:
            if len(manager.sessions) >= max_conversations:
                raise TooManyConversations(
                    f"{max_conversations} conversations are open; reuse a contextId or restart the listener"
                )
            live = _instance_for_session()
            opened.append(live)
            controller = SessionController(
                instance=live,
                display=_SilentDisplay(),
                exchange_hub=exchange_hub,
                session_id=session_id,
            )
            manager.create(live, controller, manifest or {}, session_id=session_id)
            if host is not None:
                host.advertise(session_id)
        return manager.get(session_id)

    loop = KernelLoop(mailbox=mailbox, ensure_session=ensure_session)

    def handle(
        prompt: str,
        *,
        turn_id: str | None = None,
        session_id: str | None = None,
        parent_call_id: str | None = None,
        upstream_correlation_id: int | None = None,
        caller: str = "",
        **_: Any,
    ) -> Any:
        context_id = mailbox.resolve_session_id(session_id)
        result = handler_result(
            loop.run(
                LoopTask(
                    text=prompt,
                    session_id=session_key(caller, context_id),
                    task_id=turn_id or "",
                    parent_call_id=parent_call_id or "",
                    upstream_correlation_id=upstream_correlation_id,
                )
            )
        )
        if isinstance(result, dict):
            result["context_id"] = context_id
        return result

    def close_conversations() -> None:
        """End the conversations this handler opened (not the template instance)."""
        for live in opened:
            if live is instance:
                continue
            plugin_set = getattr(live, "obs_plugin_set", None)
            for closer in (getattr(plugin_set, "close", None), getattr(live, "conversation_close", None)):
                if callable(closer):
                    try:
                        closer()
                    except Exception:
                        logger.exception("failed to close a conversation")

    handle.host = host
    handle.manager = manager
    handle.close_conversations = close_conversations
    return handle


def make_loop_handler(loop: AgentLoop) -> Any:
    """An ``agent_expose`` handler that hands each A2A ``message/send`` to ``loop``.

    A task the loop's governance refuses, or that the loop cannot complete, is an exception, which
    the exposure answers as a failed task. The loop is closed with ``close_conversations``.
    """

    def handle(
        prompt: str,
        *,
        turn_id: str | None = None,
        session_id: str | None = None,
        parent_call_id: str | None = None,
        upstream_correlation_id: int | None = None,
        caller: str = "",
        headers: Mapping[str, str] | None = None,
        **_: Any,
    ) -> Any:
        reply = loop.run(
            LoopTask(
                text=prompt,
                session_id=session_id or str(uuid.uuid4()),
                task_id=turn_id or "",
                caller=caller,
                headers=headers or {},
                parent_call_id=parent_call_id or "",
                upstream_correlation_id=upstream_correlation_id,
            )
        )
        return handler_result(reply)

    def close_conversations() -> None:
        loop.close()

    handle.host = None
    handle.close_conversations = close_conversations
    return handle


def exposure_targets(
    hosted_ids: Iterable[str],
    applications: dict[str, dict[str, Any]],
    *,
    conversation_ids: Iterable[str],
) -> list[str]:
    """Hosted agents this process should bind.

    Every hosted A2A endpoint with ``usage: listen`` or ``[connect, listen]``
    is bound here. ``usage: connect`` stays a client route so a remote
    ``mas-ctl serve`` (or ``--listen``) keeps that listen port.
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
    conversation_factories: dict[str, Callable[[], Any]] | None = None,
    flavour_spec: dict[str, Any] | None = None,
) -> list[Any]:
    """Bind background listeners for hosted deploy targets. Close the handles.

    ``conversation_factories`` maps an agent id to the function that builds a
    new conversation for it. Without one, the agent's own
    ``instance.conversation_factory`` is used.
    """
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
                expose_agent(
                    instance,
                    manifest,
                    endpoint,
                    blocking=False,
                    conversation_factory=(conversation_factories or {}).get(agent_id),
                    flavour_spec=flavour_spec,
                )
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
    conversation_factories: dict[str, Callable[[], Any]] | None = None,
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
        conversation_factories=conversation_factories,
    )


def expose_agent(
    instance: Any,
    manifest: dict[str, Any],
    endpoint: dict[str, Any],
    *,
    blocking: bool = False,
    control_dir: Path | None = None,
    conversation_factory: Callable[[], Any] | None = None,
    loop: AgentLoop | None = None,
    flavour_spec: dict[str, Any] | None = None,
) -> Any:
    """Create the ``agent_expose`` plugin and serve this instance.

    Under the Flavour's ``production`` hardening posture a listener that verifies no caller is
    refused here, before anything binds. Without ``flavour_spec`` (or without that posture) nothing
    is checked.

    With a ``loop`` the agent is served by that loop and ``instance`` is unused (pass None): the
    listener, the A2A surface and the conversation id are the same, the task goes to the loop. Without
    one the kernel loop runs it, as before.

    ``blocking=True`` is ``mas-ctl serve`` (process is the server, never
    returns until the listener stops). ``blocking=False`` is interrogation
    next to chat / TUI / run-mas.
    """
    from mas.runtime.registry import get_registry

    protocol = str(endpoint.get("protocol") or "").strip()
    if not protocol:
        raise ValueError("Application endpoint requires protocol")
    try:
        from library_ioa.plugins.a2a.grpc_extra import (
            a2a_plugin_type,
            a2a_row_grpc_reason,
            require_a2a_grpc,
        )
    except ImportError:
        a2a_plugin_type = None
        a2a_row_grpc_reason = None
    grpc_reason = a2a_row_grpc_reason(endpoint) if a2a_row_grpc_reason is not None else None
    if grpc_reason is not None:
        require_a2a_grpc(grpc_reason)
    plugin_type = a2a_plugin_type(protocol) if a2a_plugin_type is not None else protocol
    _refuse_unhardened_listener(flavour_spec, manifest, endpoint)
    _refuse_unenforced_pipeline(endpoint)
    exposure = get_registry().create(
        "agent_expose",
        binding={"type": plugin_type},
        endpoint=endpoint,
    )
    if loop is not None:
        handler = make_loop_handler(loop)
    else:
        handler = make_runtime_handler(
            instance,
            manifest=manifest,
            control_dir=control_dir,
            conversation_factory=conversation_factory,
        )
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
            handler.close_conversations()
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
    return _ExposureWithConversations(handle, handler)


def _refuse_unenforced_pipeline(endpoint: dict[str, Any]) -> None:
    """A listen row that names a protocol layer (an identity verifier) the host cannot put in front of its handler.

    The A2A and MCP hosts take their inbound chain from the program that builds them; this host builds the endpoint
    only, so a verifier named here would never run and the listener would serve unverified. Refused, not skipped.
    """
    steps = [step for step in endpoint.get("pipeline") or [] if isinstance(step, dict)]
    if not steps:
        return
    from mas.runtime.layers.compose import layer_id, layer_role

    carried = [
        layer_id(step) for step in steps if layer_role(step) == "protocol" and layer_id(step) not in {"a2a", "mcp"}
    ]
    if carried:
        raise ValueError(
            f"listen row {endpoint.get('url') or endpoint.get('protocol')!r} names layer {carried[0]!r}, which "
            "this host cannot put in front of its handler: it would serve callers unverified"
        )


def _refuse_unhardened_listener(
    flavour_spec: dict[str, Any] | None, manifest: dict[str, Any], endpoint: dict[str, Any]
) -> None:
    from mas.ctl.hardening import HardeningInput, apply_posture
    from mas.ctl.hardening.collect import a2a_exposure

    if endpoint.get("protocol") != "a2a":
        return
    name = str((manifest.get("metadata") or {}).get("name") or "agent")
    apply_posture(flavour_spec, lambda: HardeningInput(exposures=(a2a_exposure(name, endpoint),)))


class _ExposureWithConversations:
    """A listener handle that also ends the conversations its handler opened."""

    def __init__(self, handle: Any, handler: Any) -> None:
        self.listener = handle
        self.handler = handler

    def __getattr__(self, name: str) -> Any:
        return getattr(self.listener, name)

    def close(self) -> None:
        try:
            self.listener.close()
        finally:
            self.handler.close_conversations()


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
