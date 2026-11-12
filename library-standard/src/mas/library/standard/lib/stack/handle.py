#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One stack for a session: a stack instance per local agent, shared services once.

``StackHandle`` takes its layers as ``{type, params}``, bottom to top, and turns
them into one started ``StackAgentComm`` per local agent. The handle knows no
protocol and no naming scheme: it resolves each layer through the plugin
registry and asks the layer class for what it needs.

* The bottom layer is a transport: a ``Layer`` with ``needs_lower = False``,
  or an ``agent_comm`` link (``AgentCommContract``).
* The hooks below are ``ChainMember`` class methods, each with a default; a
  ``Layer`` is a ``ChainMember``, and so are a node transport and
  ``local-group``. A class that is not one gets the defaults.
* ``shared_service(params)``: the handle calls it once per layer and passes a
  service that is not ``None`` to every agent's instance of that layer as
  ``service=``. The service has ``start()`` and ``close()``; the handle starts
  it before the first agent and closes it after the last. Default: none.
* ``agent_name(params, agent_id)``: the agent's address on that layer, passed
  as ``name=``. Default: the agent id.
* ``check_agent_id(params, agent_id)`` raises ``AgentCommError`` when the
  layer's grammar refuses the agent (``agent_id_refusal`` asks it, with
  ``agent_name``, before anything connects). Default: any id.
* Each agent's layer is built from the layer's ``params`` plus whichever of
  ``service``, ``name``, ``run_turn`` and ``agent_id`` its constructor names.
  When the layer has a shared service, the service checks the params and
  the agent's layer takes only the params its constructor names.
* When a layer above the transport overrides ``serve`` (a protocol layer such
  as ``a2a-framing``), the handle gives each agent's stack the session turn as the top
  of its inbound chain (``Stack.serve``). With no such layer the transport calls
  the turn itself.
* A transport that cannot serve an agent from this process sets the
  ``ChainMember.LISTEN_REFUSAL`` class attribute to the reason; ``listen_refusal`` reads it.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from mas.library.standard.lib.comm.compose import accepted_kwargs, resolve_layer_class
from mas.library.standard.lib.stack.agent_comm import turn_handler
from mas.library.standard.lib.stack.errors import StackError
from mas.library.standard.lib.stack.layer import ChainMember, Layer
from mas.runtime.boundary.agentcomm.protocol import AgentCommContract, AgentCommError

if TYPE_CHECKING:
    from mas.library.standard.plugins.agentcomm.stack import StackAgentComm

RunTurnFn = Callable[..., str]
"""The session turn: ``run_turn(agent_id, task, correlation_id, caller_call_id, context_id) -> reply``."""


class SessionTurn:
    """A session turn that can also be awaited: ``turn(agent_id, task, ...)`` and ``await turn.asend(...)``.

    A plain ``RunTurnFn`` is run as it is; a ``SessionTurn`` also gives each link an async path.
    """

    def __init__(self, call: RunTurnFn, acall: Callable[..., Awaitable[str]]) -> None:
        self._call = call
        self._acall = acall

    def __call__(self, *args: Any, **kwargs: Any) -> str:
        return self._call(*args, **kwargs)

    async def asend(self, *args: Any, **kwargs: Any) -> str:
        return await self._acall(*args, **kwargs)


def normalize_layers(entries: Any) -> list[dict[str, Any]]:
    """Every entry as ``{type, params}``. A stack has at least one layer."""
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)) or not entries:
        raise StackError("a stack needs at least one layer")
    out: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise StackError(f"a layer is a {{type, params}} map, got {type(entry).__name__}")
        unknown = set(entry) - {"type", "params", "priority", "per", "layer"}
        if unknown:
            raise StackError(f"layer {entry.get('type') or entry.get('layer')!r}: unknown keys {sorted(unknown)}")
        name = str(entry.get("type") or entry.get("layer") or "").strip()
        if not name:
            raise StackError("a layer type cannot be empty")
        params = entry.get("params")
        if params is None:
            params = {}
        if not isinstance(params, Mapping):
            raise StackError(f"layer {name!r}: params must be a map")
        item = {"type": name, "params": dict(params)}
        if "priority" in entry:
            item["priority"] = entry["priority"]
        if "per" in entry:
            item["per"] = entry["per"]
        out.append(item)
    return out


def _order_by_priority(layers: list[dict[str, Any]], classes: list[type]) -> tuple[list[dict[str, Any]], list[type]]:
    """Keep the transport at the bottom; higher priority wraps outside (top)."""
    if len(layers) < 2:
        return layers, classes
    from mas.runtime.layers.compose import layer_priority

    rest = list(zip(layers[1:], classes[1:], strict=True))
    # Same order as compose.order_steps, then reversed so the transport stays
    # bottom and higher priority is last (outer). Ties break by name.
    rest.sort(key=lambda item: (-layer_priority(item[0]), str(item[0].get("type") or "")))
    rest.reverse()
    return [layers[0], *[pair[0] for pair in rest]], [classes[0], *[pair[1] for pair in rest]]


def is_transport(cls: type) -> bool:
    """True when ``cls`` can sit at the bottom: it sends without a layer below."""
    from mas.library.standard.plugins.agentcomm.layer import BoundLayer

    if issubclass(cls, Layer):
        return not cls.needs_lower
    if issubclass(cls, BoundLayer):
        return False
    return issubclass(cls, AgentCommContract)


def layer_classes(layers: Sequence[Mapping[str, Any]], registry: Any = None) -> list[type]:
    """The class of each normalized layer (``resolve_layer_class``).

    The first must be a transport; every layer above it must need a layer
    below it, so a transport cannot sit above another one.
    """
    classes: list[type] = []
    for layer in layers:
        name = str(layer.get("type") or "")
        try:
            classes.append(
                resolve_layer_class("agent_comm", {"type": name, "params": dict(layer.get("params") or {})}, registry)
            )
        except KeyError:
            raise StackError(f"unknown layer {name!r}") from None
    if classes and not is_transport(classes[0]):
        raise StackError(f"the first layer must be a transport; {layers[0].get('type')!r} needs a layer below it")
    for position, cls in enumerate(classes[1:], start=1):
        if is_transport(cls):
            raise StackError(
                f"layer {position} ({layers[position].get('type')!r}) is a transport; only the first layer can be one"
            )
    return classes


def is_direct_link(cls: type) -> bool:
    """True for a transport that is an ``agent_comm`` link and no ``ChainMember``.

    Such a link reaches one agent at its own address (the A2A SDK client at a
    url). A chain of that one row is the released direct path, the same as an
    agent-keyed Application row, not a stack.
    """
    return is_transport(cls) and not issubclass(cls, ChainMember)


def listen_refusal(classes: Sequence[type]) -> str:
    """Why an agent cannot be served from this process on these layers, or ``""``.

    The bottom layer answers: a transport that only connects names
    ``LISTEN_REFUSAL``, the reason and where to serve instead.
    """
    if not classes:
        return ""
    return _member(classes[0]).LISTEN_REFUSAL


def _member(cls: type) -> type[ChainMember]:
    """What ``cls`` says about itself in a chain: its own hooks if it is a ``ChainMember``, else the defaults."""
    return cls if issubclass(cls, ChainMember) else ChainMember


def agent_name(cls: type, params: Mapping[str, Any], agent_id: str) -> str:
    """The address of ``agent_id`` on a layer of class ``cls``: its own ``agent_name``, else the agent id."""
    key = str(agent_id or "").strip()
    if not key:
        raise StackError("an agent id cannot be empty")
    try:
        return str(_member(cls).agent_name(dict(params), key))
    except (AgentCommError, ValueError) as exc:
        raise StackError(str(exc)) from None


def agent_id_refusal(cls: type, params: Mapping[str, Any], agent_id: str) -> str:
    """Why a layer of class ``cls`` cannot carry ``agent_id``, or ``""``.

    The layer's ``agent_name`` must accept the id, and so must its
    ``check_agent_id`` when it has one. A layer with neither takes any id.
    """
    try:
        agent_name(cls, params, agent_id)
        _member(cls).check_agent_id(dict(params), str(agent_id).strip())
    except (AgentCommError, StackError, ValueError) as exc:
        return str(exc)
    return ""


def _agent_turn(run_turn: RunTurnFn | None, agent_id: str) -> RunTurnFn | None:
    """The session turn called with the MAS agent id, whatever name the link reports."""
    if run_turn is None:
        return None

    def turn(_name: str, task: str, *args: Any) -> str:
        return run_turn(agent_id, task, *args)

    if not isinstance(run_turn, SessionTurn):
        return turn
    asend = run_turn.asend

    async def aturn(_name: str, task: str, *args: Any) -> str:
        return await asend(agent_id, task, *args)

    return SessionTurn(turn, aturn)


def _no_turn(agent_id: str) -> Any:
    def refuse(_envelope: Any) -> Any:
        raise StackError(f"agent {agent_id!r} has no turn to serve this request")

    return refuse


class StackHandle:
    """The session's instance of one stack.

    ``link(agent_id)`` returns that agent's started ``StackAgentComm``: its
    own stack, its own name, its own receive. Agents on one handle share
    ``fabric_id``, so a send between them stays on the caller's link.
    """

    def __init__(
        self,
        name: str,
        layers: Sequence[Any],
        run_turn: RunTurnFn | None = None,
        registry: Any = None,
    ) -> None:
        self.name = str(name)
        self.layers = normalize_layers(layers)
        self._registry = registry
        self._classes = layer_classes(self.layers, registry)
        self.layers, self._classes = _order_by_priority(self.layers, self._classes)
        self._run_turn = run_turn
        self.plugin_id = str(self.layers[0]["type"])
        self.fabric_id = uuid.uuid4().int  # not id(self): an id can be reused once the object is freed
        self._services: list[Any] = [None] * len(self.layers)
        self._links: dict[str, StackAgentComm] = {}
        self._started = False
        self._closed = False

    def __repr__(self) -> str:
        return f"StackHandle(name={self.name!r})"

    def agent_name(self, agent_id: str) -> str:
        """The address of ``agent_id`` on the bottom layer."""
        return agent_name(self._classes[0], self.layers[0]["params"], agent_id)

    def start(self) -> None:
        """Create and start each layer's shared service, bottom first. Runs once."""
        if self._closed:
            raise StackError(f"stack {self.name!r} is closed")
        if self._started:
            return
        try:
            for index, (layer, cls) in enumerate(zip(self.layers, self._classes, strict=True)):
                service = _member(cls).shared_service(dict(layer["params"]))
                if service is None:
                    continue
                self._services[index] = service
                service.start()
        except BaseException:
            self._close_services()
            raise
        self._started = True

    def link(self, agent_id: str, run_turn: RunTurnFn | None = None) -> StackAgentComm:
        """The started stack of ``agent_id``. Built once; later calls return the same object."""
        from mas.library.standard.plugins.agentcomm.stack import StackAgentComm

        key = str(agent_id)
        existing = self._links.get(key)
        if existing is not None:
            return existing
        self.start()
        turn = _agent_turn(run_turn if run_turn is not None else self._run_turn, key)
        built: list[Any] = []
        for layer, cls, service in zip(self.layers, self._classes, self._services, strict=True):
            extra: dict[str, Any] = {
                "name": agent_name(cls, layer["params"], key),
                "run_turn": turn,
                "agent_id": key,
            }
            kwargs = dict(layer["params"])
            if service is not None:
                # the service already took (and checked) the params; the link takes the ones it names
                extra["service"] = service
                kwargs = accepted_kwargs(cls, kwargs)
            kwargs.update(accepted_kwargs(cls, extra))
            try:
                built.append(cls(**kwargs))
            except TypeError as exc:
                raise StackError(f"stack {self.name!r}: layer {layer['type']!r}: {exc}") from exc
        comm = StackAgentComm(built, registry=self._registry)
        comm.fabric_id = self.fabric_id
        if comm.wraps_inbound:
            # A layer above the transport decodes what arrives (a protocol layer), so the
            # transport hands each request to the chain and the session turn sits on top.
            try:
                comm.serve(turn_handler(turn, key) if turn is not None else _no_turn(key))
            except StackError as exc:
                raise StackError(f"stack {self.name!r}: {exc}") from exc
        comm.start()
        self._links[key] = comm
        return comm

    def close(self) -> None:
        """Stop every agent's stack, then the shared services, top layer first. Safe to call twice."""
        links = list(self._links.values())
        self._links.clear()
        first: BaseException | None = None
        for comm in links:
            try:
                comm.stop()
            except Exception as exc:  # every link still stops, and the services still close
                first = first or exc
        try:
            self._close_services()
        except Exception as exc:
            first = first or exc
        finally:
            self._started = False
            self._closed = True
        if first is not None:
            raise first

    def _close_services(self) -> None:
        services = [service for service in self._services if service is not None]
        self._services = [None] * len(self.layers)
        first: BaseException | None = None
        for service in reversed(services):
            try:
                service.close()
            except Exception as exc:
                first = first or exc
        if first is not None:
            raise first
