#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Build the three client paths with an identity layer in them, the way a row and a stack would."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from library_ioa.plugins.a2a.agent_comm import A2AAgentComm
from library_ioa.plugins.http_layer import HttpCallTransport
from library_ioa.plugins.mcp.provider import MCPToolProvider
from mas.library.standard.lib.stack import Envelope, Stack
from mas.library.standard.lib.stack.legacy import LinkLayer
from mas.library.standard.plugins.identity.layer import IdentityLayer
from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
from mas.runtime.secrets.ref import SecretRef

SECRETS: dict[str, str] = {"llm-key": "k-golden"}
"""The secret store of a test: ``bound_secrets(SECRETS)`` is bound for the whole test and entries added later show."""


def key_ref(value: str) -> SecretRef:
    """A ``SecretRef`` whose value in the test store is ``value``: the scheme gets the ref, never the text."""
    name = "k" + str(abs(hash(value)))
    SECRETS[name] = value
    return SecretRef(name=name)


def identity_step(scheme: str, params: Mapping[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "layer": "identity",
        "for": ["tool"],
        "params": {"identity": "id1", "scheme": scheme, "params": dict(params or {}), **extra},
    }


def mcp_provider(url: str, step: dict[str, Any] | None, **extra: Any) -> MCPToolProvider:
    spec: dict[str, Any] = {
        "name": "demo",
        "protocol": "mcp",
        "url": url + "/mcp",
        "tools": ["echo", "other"],
        "kind": "mcp",
        **extra,
    }
    if step is not None:
        spec["pipeline"] = [step]
    return MCPToolProvider.from_provider_spec(spec)


async def close(provider: MCPToolProvider) -> None:
    await asyncio.to_thread(provider.close)


def llm_provider(
    url: str, scheme: str, params: Mapping[str, Any] | None = None, **extra: Any
) -> tuple[Any, HttpCallTransport]:
    transport = HttpCallTransport(url=url + "/v1")
    IdentityLayer.wrap(
        transport,
        {"identity": "id1", "scheme": scheme, "params": dict(params or {}), **extra},
        call_kinds=("llm",),
    )
    provider = OpenAILLMProvider.wrap(
        transport, {"api_base": url + "/v1", "api_key": {"secret": "llm-key"}}, call_kinds=("llm",)
    )
    return provider, transport


async def chat(provider: Any) -> Any:
    return await provider.achat_completion(model="m", messages=[{"role": "user", "content": "q"}])


def a2a_stack(
    url: str, scheme: str, params: Mapping[str, Any] | None = None, *, agent_id: str = "caller", **extra: Any
):
    link = A2AAgentComm(url=url, timeout=10)
    layer = IdentityLayer(identity="id1", scheme=scheme, params=dict(params or {}), agent_id=agent_id, **extra)
    return Stack([LinkLayer(link), layer]), link, layer


def envelope(text: str = "hello") -> Envelope:
    return Envelope(payload=text, meta={"target": "peer", "intent": "task"})


def agent_card() -> AgentCard:
    return AgentCard(
        name="srv",
        description="d",
        version="1",
        capabilities=AgentCapabilities(streaming=True, push_notifications=True, extended_agent_card=True),
        supported_interfaces=[AgentInterface(url="http://srv", protocol_binding="JSONRPC", protocol_version="1.0")],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[AgentSkill(id="s", name="s", description="s", tags=["t"])],
    )
