# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from mas.library.enforcement_proxy import require_native_hooks
from mas.library.enforcement_proxy.bridge import AuthorizationUnavailable, Bridge, canonical

try:
    from library_ioa.plugins.a2a.delivery import DeliveryCandidate, DeliveryDecision, DeliveryOutcome
    from library_ioa.plugins.mcp.client.wrapper import run_sync
    from mas.runtime.boundary.gov.plugin import EgressDecision
    from mas.runtime.boundary.gov.policy import EgressIntentView
    from mas.runtime.boundary.gov.transition import GovTransition
    from mas.runtime.kernel.config import KernelConfig
    from mas.runtime.kernel.coupling import GovDecision
except ImportError as error:
    raise RuntimeError("MasLab with native A2A delivery policies is required") from error


class RequestPolicy:
    plugin_id = "proxy_requests"

    def __init__(self, config_file: str, agent: str, **_kwargs: Any) -> None:
        require_native_hooks()
        self.bridge = Bridge(config_file, agent)
        self._turn = threading.local()

    def on_transition(self, transition: GovTransition) -> None:
        if transition.agent_id != self.bridge.agent:
            raise AuthorizationUnavailable("Governance plugin agent binding differs from runtime")
        if transition.kind == "USER_INPUT_RECEIVED":
            self._turn.context = {
                "session_id": transition.session_id,
                "task_id": transition.task_id,
                "user_task": transition.attributes.get("text", ""),
            }

    def evaluate_egress(self, intent: EgressIntentView, *, config: KernelConfig) -> EgressDecision:
        if intent.op != "TOOL_CALL":
            return GovDecision.ALLOW, self.plugin_id, "outside request policy scope"
        try:
            context = getattr(self._turn, "context", None)
            if context is None:
                raise AuthorizationUnavailable("No active native conversation context")
            route = self.bridge.request_route(intent.tool_name)
            result = run_sync(
                self.bridge.authorize(
                    destination=route["destination"],
                    kind=route["kind"],
                    transport=route["transport"],
                    tool=route.get("tool", ""),
                    context={**context, "correlation_id": intent.correlation_id},
                    payload=canonical({"name": intent.tool_name, "arguments": intent.tool_arguments or {}}),
                )
            )
            return (
                GovDecision.ALLOW if result.allowed else GovDecision.BLOCK,
                self.plugin_id,
                "authorized" if result.allowed else "authorization denied",
            )
        except Exception:
            return GovDecision.BLOCK, self.plugin_id, "authorization unavailable; operation blocked"

    def close(self) -> None:
        self.bridge.close()


class ReleasePolicy:
    def __init__(self, config_file: str, agent: str) -> None:
        require_native_hooks()
        self.bridge = Bridge(config_file, agent)
        self._observers: list[Callable[[DeliveryOutcome], None]] = []

    def subscribe(self, observer: Callable[[DeliveryOutcome], None]) -> None:
        if observer not in self._observers:
            self._observers.append(observer)

    def unsubscribe(self, observer: Callable[[DeliveryOutcome], None]) -> None:
        if observer in self._observers:
            self._observers.remove(observer)

    def on_release(self, outcome: DeliveryOutcome) -> None:
        for observer in tuple(self._observers):
            try:
                observer(outcome)
            except Exception:
                logging.getLogger(__name__).warning("Release observer failed")

    async def authorize(self, candidate: DeliveryCandidate) -> DeliveryDecision:
        if candidate.agent_id != self.bridge.agent:
            return DeliveryDecision.DENY
        # Authentication middleware establishes caller identity. Push destinations
        # are resolved from a protected configuration, never from candidate text.
        destination = (
            self.bridge.identity.get("push_destinations", {}).get(candidate.destination)
            if candidate.kind == "push"
            else candidate.recipient_id
        )
        if not destination:
            return DeliveryDecision.DENY
        try:
            result = await self.bridge.authorize(
                destination=destination,
                kind="release",
                transport="http",
                payload=candidate.payload,
                context={
                    "task_id": candidate.task_id,
                    "context_id": candidate.context_id,
                    "delivery_kind": candidate.kind,
                    "recipient": destination,
                    "push_destination": candidate.destination,
                    "correlation": json.loads(candidate.request_metadata),
                },
            )
            return DeliveryDecision.ALLOW if result.allowed else DeliveryDecision.DENY
        except AuthorizationUnavailable:
            return DeliveryDecision.DENY

    async def aclose(self) -> None:
        self._observers.clear()
        self.bridge.close()
