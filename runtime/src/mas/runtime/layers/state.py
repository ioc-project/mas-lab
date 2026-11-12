#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The running spec as a call layer sees it.

The running spec is copied from the agent spec when the session starts and then evolves. It belongs to the
session: a layer never writes it. It holds a :class:`RunningSpecPort`, built from the control handle the
session gave it with a least-privilege capability, so every change is a ``spec_delta`` the session applies
(refused while it checkpoints or is over), traced, and restored with a snapshot.

Writes are compare-and-set: a layer computes a transition from the state it read, and the session applies it
only if that is still the state. Two writers never lose an update and nothing is locked; the loser re-reads.
Outside a managed session there is no running spec; :class:`MemorySpecPort` then keeps the layer state in the
layer itself and edits no tool.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from mas.runtime.boundary.control.contract import ControlDenied
from mas.runtime.session.spec_machine import SpecConflict, SpecVerdict, spec_on_delta
from mas.runtime.session.state import SessionStatus

_log = logging.getLogger(__name__)


class RunningSpecPort(Protocol):
    """What a layer may do with the running spec."""

    def read(self, layer: str, target: str) -> dict[str, Any]:
        """The state *layer* recorded for *target*; ``{}`` when none."""
        ...

    def targets(self, layer: str) -> dict[str, dict[str, Any]]:
        """Every target *layer* has recorded state for."""
        ...

    def swap(
        self, layer: str, target: str, expected: Mapping[str, Any], state: Mapping[str, Any] | None, *, reason: str
    ) -> None:
        """Set *target*'s state to *state* (``None`` clears it) if it is still *expected*, else ``SpecConflict``."""
        ...

    def disable_tool(self, name: str, *, by: str, reason: str) -> bool:
        """Take tool *name* out of the spec the model sees. ``False`` when the spec does not list it."""
        ...

    def enable_tool(self, name: str, *, by: str, reason: str) -> bool:
        """Put back a tool *by* disabled. ``False`` when it is not listed or someone else disabled it."""
        ...

    def tool_probe(self, name: str) -> dict[str, Any] | None:
        """The arguments of the probe tool *name* declares in the spec, or ``None`` when it declares none."""
        ...


class MemorySpecPort:
    """No running spec: layer state kept in the process, no tool edited."""

    def __init__(self) -> None:
        self._states: dict[str, dict[str, dict[str, Any]]] = {}

    def read(self, layer: str, target: str) -> dict[str, Any]:
        return copy.deepcopy(self._states.get(layer, {}).get(target, {}))

    def targets(self, layer: str) -> dict[str, dict[str, Any]]:
        return copy.deepcopy(self._states.get(layer, {}))

    def swap(
        self, layer: str, target: str, expected: Mapping[str, Any], state: Mapping[str, Any] | None, *, reason: str
    ) -> None:
        del reason
        bucket = self._states.setdefault(layer, {})
        # the same machine decides here as in a session; only the lifecycle is always live
        if spec_on_delta(SessionStatus.ACTIVE, bucket.get(target), expected, guarded=True) is SpecVerdict.CONFLICT:
            raise SpecConflict(f"{layer} state for {target!r} changed since it was read")
        if state:
            bucket[target] = copy.deepcopy(dict(state))
        else:
            bucket.pop(target, None)

    def disable_tool(self, name: str, *, by: str, reason: str) -> bool:
        return False

    def enable_tool(self, name: str, *, by: str, reason: str) -> bool:
        return False

    def tool_probe(self, name: str) -> dict[str, Any] | None:
        return None


def transition(
    port: RunningSpecPort,
    layer: str,
    target: str,
    step: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    why: str | Callable[[dict[str, Any], dict[str, Any]], str],
    tries: int = 4,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Move *target*'s state by *step*; the spec machine decides whether it applies. Returns ``(before, after)``.

    This is the one retry-on-conflict loop for every layer: when the machine answers ``CONFLICT`` (someone
    changed the state first) the state is read again and *step* applied to that. When it answers ``BUSY``
    (the session is checkpointing or over) the change does not stick and the call goes on. No layer carries
    its own guard.
    """
    for _ in range(tries):
        before = port.read(layer, target)
        after = step(before)
        if after == before:
            return before, after
        what = why(before, after) if callable(why) else why
        try:
            port.swap(layer, target, before, after, reason=f"{layer} {what} {target}")
        except SpecConflict:
            continue
        except ControlDenied as exc:
            _log.warning("%s could not record %s for %s: %s", layer, what, target, exc)
            return before, before
        return before, after
    _log.warning("%s gave up recording a change for %s: the spec kept changing", layer, target)
    return port.read(layer, target), port.read(layer, target)


class SessionSpecPort:
    """The running spec of one session, through its control handle and its current spec."""

    def __init__(self, control: Any, session_id: str, ctx: Any) -> None:
        self._control = control
        self._session_id = session_id
        self._ctx = ctx

    def read(self, layer: str, target: str) -> dict[str, Any]:
        found = self._control.layer_state(self._session_id, layer=layer).get(target)
        return dict(found) if isinstance(found, Mapping) else {}

    def targets(self, layer: str) -> dict[str, dict[str, Any]]:
        return {
            str(target): dict(state)
            for target, state in self._control.layer_state(self._session_id, layer=layer).items()
            if isinstance(state, Mapping)
        }

    def swap(
        self, layer: str, target: str, expected: Mapping[str, Any], state: Mapping[str, Any] | None, *, reason: str
    ) -> None:
        self._control.swap_layer_state(
            self._session_id,
            layer=layer,
            target=target,
            expected=dict(expected),
            state=dict(state) if state else None,
            reason=reason,
        )

    def disable_tool(self, name: str, *, by: str, reason: str) -> bool:
        try:
            self._control.disable_tool(self._session_id, name=name, reason=reason, by=by)
        except ValueError:  # the spec does not list this tool
            return False
        return True

    def enable_tool(self, name: str, *, by: str, reason: str) -> bool:
        try:
            self._control.enable_tool(self._session_id, name=name, reason=reason, by=by)
        except ValueError:  # not listed, or disabled by someone else (SpecConflict is a ValueError)
            return False
        return True

    def tool_probe(self, name: str) -> dict[str, Any] | None:
        for tool in running_spec_of(self._ctx).get("tools") or []:
            if not isinstance(tool, dict) or str(tool.get("name") or tool.get("id") or "") != name:
                continue
            declared = tool.get("spec") if isinstance(tool.get("spec"), dict) else tool
            probe = declared.get("probe")
            if isinstance(probe, dict):
                arguments = probe.get("arguments")
                return dict(arguments) if isinstance(arguments, dict) else {}
        return None


def spec_port_for(ctx: Any) -> RunningSpecPort | None:
    """The running-spec port of the session *ctx* belongs to, or ``None`` outside a managed session."""
    from mas.runtime.driver.mocks import AutoCtxAssembler

    if not isinstance(ctx, AutoCtxAssembler) or ctx.layer_control is None or not ctx.session_id:
        return None
    return SessionSpecPort(ctx.layer_control, ctx.session_id, ctx)


def running_spec_of(ctx: Any) -> dict[str, Any]:
    """The spec a call runs under: the session's running spec, else the bound manifest's spec, else empty."""
    from mas.runtime.driver.mocks import AutoCtxAssembler

    if not isinstance(ctx, AutoCtxAssembler):
        return {}
    if ctx.current_spec is not None:
        return ctx.current_spec
    spec = (ctx.manifest or {}).get("spec")
    return spec if isinstance(spec, dict) else {}
