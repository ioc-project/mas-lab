#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""In-process group-capable agent_comm link with hierarchical names."""

from __future__ import annotations

import logging
import random
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from mas.library.standard.lib.comm.capabilities import capability_set
from mas.library.standard.lib.comm.headers import bound_headers, merge_headers
from mas.library.standard.lib.comm.names import namespace_of, parse_name, require_instance_name
from mas.library.standard.lib.comm.payload import (
    INFORM_ASK_INTENTS,
    MAX_MESSAGE_SIZE,
    PUBLISH_INTENT,
    STEER_INTENT,
    bound_inbound,
    check_message_size,
    dispatch_steer,
    inbound_payload,
    normalize_send_intent,
    require_honoured_intent,
    send_result,
    steer_peer_sessions,
)
from mas.library.standard.lib.comm.send_context import align_send
from mas.runtime.boundary.agentcomm.protocol import AgentCommContract, AgentCommError, SendContext
from mas.runtime.boundary.control.contract import ControlContract

OnRequestFn = Callable[..., str]
RunTurnFn = Callable[[str, str, int, str, str], str]

_log = logging.getLogger(__name__)


def _copy(envelope: dict[str, Any]) -> dict[str, Any]:
    """Per-receiver copy of an envelope; headers travel in it, not in shared state."""
    return {**envelope, "headers": dict(envelope.get("headers") or {})}


@dataclass
class _Channel:
    name: str
    key: str
    namespace: str
    members: set[str]
    moderator: str
    closed: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)


class LocalGroupFabric:
    """Shared in-process fabric. A session injects one; there is no process registry.

    This PR is the Python API. Session wiring that creates one fabric and
    passes it into every agent is a later step. A YAML binding with only
    ``params.name`` builds a private fabric, so two such links cannot talk.
    """

    def __init__(
        self,
        *,
        inbox_limit: int = 64,
        history_limit: int = 4096,
        overflow: str = "reject",
        max_message_size: int = MAX_MESSAGE_SIZE,
        seed: int | None = 0,
        deterministic: bool = True,
    ) -> None:
        if overflow not in {"reject", "drop_oldest"}:
            raise ValueError(f"unknown overflow policy {overflow!r}")
        self.inbox_limit = inbox_limit
        self.history_limit = history_limit
        self.overflow = overflow
        self.max_message_size = max_message_size
        self.deterministic = deterministic
        self.nodes: dict[str, LocalGroupAgentComm] = {}
        self.channels: dict[str, _Channel] = {}
        self._seq = 0
        self._rng = random.Random(seed if deterministic else None)

    def register(self, link: LocalGroupAgentComm) -> None:
        if link.name in self.nodes:
            raise AgentCommError(f"duplicate name {link.name!r}")
        self.nodes[link.name] = link

    def unregister(self, link: LocalGroupAgentComm) -> None:
        self.nodes.pop(link.name, None)
        for key, channel in list(self.channels.items()):
            if channel.moderator == link.name:
                del self.channels[key]
            else:
                channel.members.discard(link.name)

    def next_id(self) -> str:
        self._seq += 1
        return f"{self._seq:08d}"

    def pick(self, names: list[str]) -> str:
        ordered = sorted(names)
        return ordered[self._rng.randrange(len(ordered))]


class LocalGroupAgentComm(AgentCommContract):
    """L7 local-group link: unicast, seedable anycast, moderator channels.

    Pass a shared ``LocalGroupFabric`` from Python. ``stop`` unregisters this
    link, removes channels it moderated, drains its inbox, and rejects later
    sends. Unicast with no handler raises. Capabilities do not claim
    ``reliable``: inboxes are bounded and can drop or reject.
    """

    CAPABILITIES: ClassVar[dict[str, bool]] = capability_set(
        unicast=True, anycast=True, group=True, ordered=True, isolation=True
    )
    HONOURED_SEND_INTENTS = INFORM_ASK_INTENTS

    def __init__(
        self,
        run_turn: RunTurnFn | None = None,
        *,
        name: str,
        fabric: LocalGroupFabric | None = None,
        on_request: OnRequestFn | None = None,
        seed: int | None = 0,
        inbox_limit: int = 64,
        history_limit: int = 4096,
        overflow: str = "reject",
        max_message_size: int = MAX_MESSAGE_SIZE,
        deterministic: bool = True,
        control: ControlContract | None = None,
        session_id: str = "",
        steer_peers: Mapping[str, str] | Sequence[str] | None = None,
    ) -> None:
        parts = require_instance_name(name)
        self.name = "/".join(parts)
        self.namespace = namespace_of(parts)
        self.service = "/".join(parts[:3])
        self.instance = parts[3]
        self.agent_id = self.name
        self._run_turn = None if on_request is not None else run_turn
        self._on_request = on_request
        if fabric is None:
            self.fabric = LocalGroupFabric(
                seed=seed,
                inbox_limit=inbox_limit,
                history_limit=history_limit,
                overflow=overflow,
                max_message_size=max_message_size,
                deterministic=deterministic,
            )
        elif isinstance(fabric, LocalGroupFabric):
            self.fabric = fabric
        else:
            raise AgentCommError("local-group fabric must be a LocalGroupFabric; the session injects it")
        self._inbox: deque[dict[str, Any]] = deque()
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._sticky: dict[str, str] = {}
        self._started = False
        self._stopped = False
        self._control = control
        self.session_id = str(session_id or "").strip()
        self._steer_sessions = steer_peer_sessions(steer_peers)
        self.fabric.register(self)

    def capabilities(self) -> dict[str, bool]:
        return dict(self.CAPABILITIES)

    def honoured_send_intents(self) -> frozenset[str]:
        if self._control is None:
            return self.HONOURED_SEND_INTENTS
        return self.HONOURED_SEND_INTENTS | {STEER_INTENT}

    def _resolved_steer_peers(self) -> set[str]:
        names: set[str] = set()
        for raw in self._steer_sessions:
            try:
                names.add(self._resolve(raw).name)
            except AgentCommError:
                continue
        return names

    def _steer(self, peer: LocalGroupAgentComm, text: str, *, mode: str) -> str:
        return dispatch_steer(
            self._control,
            session_id=peer.session_id,
            text=text,
            mode=mode,
            target=peer.name,
            allowed=self._resolved_steer_peers(),
            sender=self.agent_id,
        )

    def bind(self, lower: object) -> None:
        del lower

    def start(self) -> None:
        if self._stopped:
            raise AgentCommError(f"{self.name} is stopped")
        if self._started:
            return
        self._started = True

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self.fabric.unregister(self)
        self._inbox.clear()
        self._subscribers.clear()

    def receive(self) -> dict[str, Any] | None:
        """Pop the next queued publish, or ``None`` if the inbox is empty."""
        self._require_live()
        if not self._inbox:
            return None
        return self._inbox.popleft()

    def drain(self) -> list[dict[str, Any]]:
        """Pop every queued publish."""
        self._require_live()
        items = list(self._inbox)
        self._inbox.clear()
        return items

    def _require_live(self) -> None:
        if self._stopped:
            raise AgentCommError(f"{self.name} is stopped")

    def send(
        self,
        target_agent_id: str,
        task: str,
        *,
        correlation_id: int = 0,
        caller_call_id: str = "",
        context_id: str = "",
        headers: dict[str, str] | None = None,
        context: SendContext | None = None,
    ) -> str:
        steered = self._steer_if_requested(target_agent_id, task, headers=headers, context=context)
        if steered is not None:
            return steered
        peer, envelope = self._prepare_send(
            target_agent_id,
            task,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
            headers=headers,
            context=context,
        )
        result = peer._receive_request(envelope)
        return send_result(str(envelope["intent"]), result)

    async def asend(
        self,
        target_agent_id: str,
        task: str,
        *,
        correlation_id: int = 0,
        caller_call_id: str = "",
        context_id: str = "",
        headers: dict[str, str] | None = None,
        context: SendContext | None = None,
    ) -> str:
        steered = self._steer_if_requested(target_agent_id, task, headers=headers, context=context)
        if steered is not None:
            return steered
        peer, envelope = self._prepare_send(
            target_agent_id,
            task,
            correlation_id=correlation_id,
            caller_call_id=caller_call_id,
            context_id=context_id,
            headers=headers,
            context=context,
        )
        result = await peer._areceive_request(envelope)
        return send_result(str(envelope["intent"]), result)

    def _steer_if_requested(
        self,
        target_agent_id: str,
        task: str,
        *,
        headers: dict[str, str] | None,
        context: SendContext | None,
    ) -> str | None:
        _headers, aligned = align_send(headers, context, sender=self.agent_id)
        del _headers
        if normalize_send_intent(aligned.intent) != STEER_INTENT:
            return None
        self._require_live()
        self._check_size(task)
        require_honoured_intent(self, STEER_INTENT)
        peer = self._resolve(target_agent_id)
        return self._steer(peer, task, mode=aligned.mode)

    def _prepare_send(
        self,
        target_agent_id: str,
        task: str,
        *,
        correlation_id: int,
        caller_call_id: str,
        context_id: str,
        headers: dict[str, str] | None,
        context: SendContext | None,
    ) -> tuple[LocalGroupAgentComm, dict[str, Any]]:
        self._require_live()
        self._check_size(task)
        headers, context = align_send(headers, context, sender=self.agent_id)
        kind = require_honoured_intent(self, context.intent)
        peer = self._resolve(target_agent_id)
        payload = inbound_payload(task, sender=context.sender, intent=kind)
        envelope = {
            "sender": self.agent_id,
            "task": payload,
            "intent": kind,
            "headers": merge_headers(headers),
            "correlation_id": correlation_id,
            "caller_call_id": caller_call_id,
            "context_id": context_id,
        }
        return peer, _copy(envelope)

    def _handler_kwargs(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return {
            "sender": envelope["sender"],
            "intent": str(envelope.get("intent") or ""),
            "agent_id": self.agent_id,
            "correlation_id": envelope.get("correlation_id", 0),
            "caller_call_id": envelope.get("caller_call_id", ""),
            "context_id": envelope.get("context_id", ""),
        }

    def _call_run_turn(self, envelope: dict[str, Any]) -> str:
        run_turn = self._run_turn
        if run_turn is None:
            raise AgentCommError(f"no handler for {self.name}")
        return run_turn(
            self.agent_id,
            str(envelope["task"]),
            int(envelope.get("correlation_id") or 0),
            str(envelope.get("caller_call_id") or ""),
            str(envelope.get("context_id") or ""),
        )

    def _inbound(self, envelope: dict[str, Any]):
        return bound_inbound(
            sender=str(envelope.get("sender") or ""),
            intent=str(envelope.get("intent") or ""),
        )

    def _receive_request(self, envelope: dict[str, Any]) -> str:
        self._require_live()
        with self._inbound(envelope), bound_headers(envelope.get("headers")):
            if self._on_request is not None:
                return self._on_request(str(envelope["task"]), **self._handler_kwargs(envelope))
            if self._run_turn is not None:
                return self._call_run_turn(envelope)
        raise AgentCommError(f"no handler for {self.name}")

    async def _areceive_request(self, envelope: dict[str, Any]) -> str:
        self._require_live()
        asend_fn = getattr(self._run_turn, "asend", None) if self._on_request is None else None
        if callable(asend_fn):
            with self._inbound(envelope), bound_headers(envelope.get("headers")):
                return await asend_fn(
                    self.agent_id,
                    str(envelope["task"]),
                    int(envelope.get("correlation_id") or 0),
                    str(envelope.get("caller_call_id") or ""),
                    str(envelope.get("context_id") or ""),
                )
        return self._receive_request(envelope)

    def create_channel(self, name: str, members: list[str], options: dict[str, Any] | None = None) -> str:
        self._require_live()
        if options:
            raise AgentCommError("create_channel options are not supported")
        key = self._channel_key(name, creating=True)
        existing = self.fabric.channels.get(key)
        if existing is not None and not existing.closed:
            if existing.moderator == self.name:
                return existing.name
            raise AgentCommError(f"channel {name!r} already exists")
        resolved = {self._member_key(member) for member in members}
        resolved.add(self.name)
        self.fabric.channels[key] = _Channel(
            name=name,
            key=key,
            namespace=self.namespace,
            members=resolved,
            moderator=self.name,
        )
        return name

    def invite(self, channel: str, member: str) -> None:
        self._require_live()
        state = self._require_moderator(channel)
        state.members.add(self._member_key(member))

    def remove(self, channel: str, member: str) -> None:
        self._require_live()
        state = self._require_moderator(channel)
        state.members.discard(self._member_key(member))

    def close_channel(self, channel: str) -> None:
        self._require_live()
        self._require_moderator(channel).closed = True

    def members(self, channel: str) -> list[str]:
        self._require_live()
        return sorted(self._channel(channel).members)

    def publish(
        self,
        channel: str,
        payload: str,
        *,
        to: str | None = None,
        headers: dict[str, str] | None = None,
        context: SendContext | None = None,
    ) -> dict[str, str]:
        self._require_live()
        self._check_size(payload)
        state = self._channel(channel)
        if state.closed:
            raise AgentCommError(f"channel {channel!r} is closed")
        if self.name not in state.members:
            raise AgentCommError(f"{self.name!r} is not a member of {channel!r}")
        headers, context = align_send(headers, context, sender=self.agent_id)
        labelled = inbound_payload(payload, sender=context.sender, intent=PUBLISH_INTENT)
        envelope = {
            "id": self.fabric.next_id(),
            "sender": self.agent_id,
            "channel": channel,
            "kind": "message",
            "payload": labelled,
            "intent": PUBLISH_INTENT,
            "headers": merge_headers(headers),
            "to": to,
        }
        targets = self._publish_targets(state, to)
        self._assert_history_room(state)
        queued = [peer for peer in targets if not peer._subscribers]
        for peer in queued:
            peer._assert_inbox_room()
        state.history.append(envelope)
        copies = [_copy(envelope) for _ in targets]
        for peer, item in zip(targets, copies, strict=True):
            if peer._subscribers:
                peer._notify(item)
            else:
                peer._enqueue(item)
        return {"channel": channel, "sender": self.agent_id, "id": str(envelope["id"])}

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._require_live()
        self._subscribers.append(callback)

    def _publish_targets(self, state: _Channel, to: str | None) -> list[LocalGroupAgentComm]:
        if to is not None:
            key = self._member_key(to)
            if key not in state.members:
                raise AgentCommError(f"{to!r} is not a member of {state.name!r}")
            peer = self.fabric.nodes.get(key)
            if peer is None or peer is self:
                raise AgentCommError(f"no peer {to!r}")
            return [peer]
        targets: list[LocalGroupAgentComm] = []
        for key in state.members:
            if key == self.name:
                continue
            peer = self.fabric.nodes.get(key)
            if peer is not None:
                targets.append(peer)
        return targets

    def _assert_history_room(self, state: _Channel) -> None:
        if len(state.history) < self.fabric.history_limit:
            return
        if self.fabric.overflow == "drop_oldest" and state.history:
            state.history.pop(0)
            return
        raise AgentCommError("history overflow")

    def _assert_inbox_room(self) -> None:
        if len(self._inbox) < self.fabric.inbox_limit:
            return
        if self.fabric.overflow == "drop_oldest" and self._inbox:
            return
        raise AgentCommError("inbox overflow")

    def _enqueue(self, envelope: dict[str, Any]) -> None:
        if len(self._inbox) >= self.fabric.inbox_limit:
            if self.fabric.overflow == "drop_oldest" and self._inbox:
                self._inbox.popleft()
            else:
                raise AgentCommError("inbox overflow")
        self._inbox.append(envelope)

    def _notify(self, envelope: dict[str, Any]) -> None:
        with self._inbound(envelope), bound_headers(envelope.get("headers")):
            for callback in list(self._subscribers):
                try:
                    callback(envelope)
                except Exception:
                    _log.exception("local-group subscriber failed for %s", self.name)

    def _check_size(self, payload: str) -> None:
        check_message_size(payload, max_size=self.fabric.max_message_size)

    def _channel_key(self, channel: str, *, creating: bool = False) -> str:
        parts = parse_name(channel)
        if len(parts) == 1:
            key = f"{self.namespace}/{parts[0]}"
        else:
            if namespace_of(parts) != self.namespace:
                raise AgentCommError(f"channel {channel!r} is isolated from {self.namespace}")
            key = "/".join(parts)
        if creating:
            self._reject_shadow(channel, key)
        return key

    def _reject_shadow(self, channel: str, key: str) -> None:
        if key in self.fabric.nodes:
            raise AgentCommError(f"channel {channel!r} shadows an agent")
        short = parse_name(channel)
        if len(short) == 1:
            for node in self.fabric.nodes.values():
                if node.namespace == self.namespace and node.instance == short[0]:
                    raise AgentCommError(f"channel {channel!r} shadows an agent")
        if len(short) == 3:
            for node in self.fabric.nodes.values():
                if node.service == key:
                    raise AgentCommError(f"channel {channel!r} shadows a service")
        if len(short) == 4:
            raise AgentCommError(f"channel {channel!r} shadows an agent name")

    def _channel(self, channel: str) -> _Channel:
        state = self.fabric.channels.get(self._channel_key(channel))
        if state is None:
            raise AgentCommError(f"unknown channel {channel!r}")
        if state.namespace != self.namespace:
            raise AgentCommError(f"channel {channel!r} is isolated from {self.namespace}")
        return state

    def _require_moderator(self, channel: str) -> _Channel:
        state = self._channel(channel)
        if state.moderator != self.name:
            raise AgentCommError("only the moderator may change membership")
        return state

    def _member_key(self, member: str) -> str:
        parts = parse_name(member)
        if len(parts) == 1:
            matches = [
                node.name
                for node in self.fabric.nodes.values()
                if node.namespace == self.namespace and node.instance == parts[0]
            ]
            if len(matches) > 1:
                raise AgentCommError(f"ambiguous short name {member!r}")
            if len(matches) == 1:
                return matches[0]
            return f"{self.service}/{parts[0]}"
        if len(parts) == 4:
            if namespace_of(parts) != self.namespace:
                raise AgentCommError(f"cross-namespace member {member!r} is isolated")
            return "/".join(parts)
        raise AgentCommError(f"unsupported name {member!r}")

    def _resolve(self, target: str) -> LocalGroupAgentComm:
        parts = parse_name(target)
        if len(parts) == 1:
            matches = [
                node
                for node in self.fabric.nodes.values()
                if node.namespace == self.namespace and node.instance == parts[0]
            ]
        elif len(parts) == 3:
            prefix = "/".join(parts)
            if namespace_of(parts) != self.namespace:
                raise AgentCommError(f"anycast target {target!r} is isolated")
            bound = self._sticky.get(prefix)
            if bound is not None:
                sticky = self.fabric.nodes.get(bound)
                if sticky is not None:
                    return sticky
            matches = [node for node in self.fabric.nodes.values() if node.name.startswith(prefix + "/")]
            if not matches:
                raise AgentCommError(f"no peer {target!r}")
            chosen = self.fabric.pick([node.name for node in matches])
            self._sticky[prefix] = chosen
            return self.fabric.nodes[chosen]
        elif len(parts) == 4:
            full = "/".join(parts)
            if namespace_of(parts) != self.namespace:
                raise AgentCommError(f"unicast target {target!r} is isolated")
            node = self.fabric.nodes.get(full)
            matches = [node] if node is not None else []
        else:
            raise AgentCommError(f"unsupported name {target!r}")
        if not matches:
            raise AgentCommError(f"no peer {target!r}")
        if len(matches) > 1:
            raise AgentCommError(f"ambiguous short name {target!r}")
        return matches[0]
