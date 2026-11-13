# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Capture the SDK's selected interface without duplicating negotiation."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from a2a.client import ClientFactory
from a2a.client.client_factory import TransportProducer
from a2a.types import AgentCard
from a2a.utils.constants import TransportProtocol
from mas.runtime.boundary.connection import BindingUnavailable


@dataclass(frozen=True, repr=False)
class SelectedInterface:
    card: bytes
    protocol_binding: str
    url: str
    headers: tuple[tuple[str, str], ...]
    timeout: float

    @property
    def transport(self) -> str:
        if self.protocol_binding in {TransportProtocol.JSONRPC, TransportProtocol.HTTP_JSON}:
            return "http"
        if self.protocol_binding == TransportProtocol.GRPC:
            return "grpc"
        raise BindingUnavailable("The selected A2A transport cannot be described")

    def agent_card(self) -> AgentCard:
        card = AgentCard()
        card.ParseFromString(self.card)
        return card


class RecordingClientFactory(ClientFactory):
    """Observe the public producer callback after native SDK selection."""

    selected: tuple[str, str] | None = None

    def register(self, label: str, generator: TransportProducer) -> None:
        def record(card, url, config):
            self.selected = (label, url)
            return generator(card, url, config)

        super().register(label, record)

    def selection(self, card: AgentCard, *, headers: dict[str, str], timeout: float) -> SelectedInterface:
        if self.selected is None:
            raise BindingUnavailable("A2A SDK did not resolve an interface")
        protocol, url = self.selected
        pinned = AgentCard()
        pinned.CopyFrom(card)
        # Preserve version and tenant selection, but remove every alternative
        # destination. Execution uses this snapshot, never another card lookup.
        interfaces = [i for i in pinned.supported_interfaces if i.protocol_binding == protocol and i.url == url]
        del pinned.supported_interfaces[:]
        pinned.supported_interfaces.extend(interfaces)
        return SelectedInterface(pinned.SerializeToString(), protocol, url, tuple(sorted(headers.items())), timeout)


CURRENT_INTERFACE: ContextVar[tuple[object, SelectedInterface] | None] = ContextVar(
    "a2a_selected_interface", default=None
)
