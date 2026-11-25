#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""An identity step on a tool server's stack reaches a real MCP server through the same path ``run-mas`` takes."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mas.ctl.compose.runner import ComposeRequest, compose_run
from mas.ctl.executor.mas_session import materialize_mas_compose
from mas.ctl.executor.run_mas import _EntrySession, _QuietDisplay, open_entry_session
from mas.ctl.infra.bind import bind_session
from mas.ctl.infra.identities import agency_ids
from mas.ctl.session.controller import ConversationConfig, SessionController, close_observability
from mas.ctl.session.params_sidecar import params_from_mas_config
from mas.ctl.ui.stdout import StdoutConversationDisplay
from wiring_servers import FakeLlm, FakeMcp, Reply, Seen

AGENT = """apiVersion: mas/v1
kind: Agent
metadata: {name: desk}
spec:
  description: test
  models: [{model: gpt-4o-mini}]
  tools: [{ref: ./echo.tool.yaml}]
"""
TOOL = """apiVersion: mas/v1
kind: Tool
metadata: {name: echo, description: Echo, version: "1.0.0"}
spec:
  description: Echo the arguments.
  parameters: []
  impl: {kind: python, module_path: ./echo.py, class_name: EchoTool}
"""
TOOL_CODE = """class EchoTool:
    def on_collect_tools(self, **_):
        return [{"name": "echo", "description": "Echo.", "parameters": {"type": "object", "properties": {}}}]

    def call_tool(self, name, arguments, **_):
        return {"local": True}
"""
AGENT_BARE = """apiVersion: mas/v1
kind: Agent
metadata: {name: NAME}
spec:
  description: test
  models: [{model: gpt-4o-mini}]
"""
DELEGATING_MAS = """apiVersion: mas/v1
kind: MAS
metadata: {name: run-mas-delegation}
spec:
  agency: {agents: [{id: lead, ref: lead.yaml}, {id: payer, ref: payer.yaml}]}
  workflow:
    entry: lead
    nodes: [{id: lead, delegates_to: [payer]}, {id: payer}]
"""
MAS = """apiVersion: mas/v1
kind: MAS
metadata: {name: run-mas-identity}
spec:
  agency: {agents: [{id: desk, ref: agent.yaml}]}
  workflow: {entry: desk, nodes: [{id: desk}]}
"""


def _write(root: Path, url: str, protocol: str, *, delegating: bool = False) -> list[str]:
    if delegating:
        (root / "lead.yaml").write_text(AGENT_BARE.replace("NAME", "lead"), encoding="utf-8")
        (root / "payer.yaml").write_text(AGENT.replace("name: desk", "name: payer"), encoding="utf-8")
        (root / "mas.yaml").write_text(DELEGATING_MAS, encoding="utf-8")
    else:
        (root / "agent.yaml").write_text(AGENT, encoding="utf-8")
        (root / "mas.yaml").write_text(MAS, encoding="utf-8")
    (root / "echo.tool.yaml").write_text(TOOL, encoding="utf-8")
    (root / "echo.py").write_text(TOOL_CODE, encoding="utf-8")
    (root / "tools.yaml").write_text(
        "apiVersion: infra/v1\nkind: ToolServerRegistry\nmetadata: {name: tools}\nspec:\n  tool_servers:\n"
        f"    - {{id: fares, protocol: {protocol}, usage: connect, transport: streamable-http, url: '{url}',\n"
        "       tools: '*',\n"
        "       pipeline: [{layer: identity, params: {identity: desk-id}}]}\n",
        encoding="utf-8",
    )
    (root / "identity.yaml").write_text(
        "apiVersion: infra/v1\nkind: Identity\nmetadata: {name: ids}\nspec:\n  identities:\n"
        "    - {id: desk-id, scheme: none, params: {}}\n",
        encoding="utf-8",
    )
    (root / "stack.yaml").write_text(
        "apiVersion: infra/v1\nkind: Stack\nmetadata: {name: fares-mcp}\nspec:\n  layers:\n"
        "    - {layer: http}\n    - {layer: identity, params: {identity: desk-id}}\n    - {layer: mcp}\n",
        encoding="utf-8",
    )
    (root / "llm-stack.yaml").write_text(
        "apiVersion: infra/v1\nkind: Stack\nmetadata: {name: desk-llm}\nspec:\n  layers:\n"
        "    - {layer: identity, params: {identity: desk-id}}\n",
        encoding="utf-8",
    )
    return ["tools.yaml", "identity.yaml", "stack.yaml", "llm-stack.yaml", "standard:openai"]


@pytest.fixture(autouse=True)
def _own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """None of the host's user config (its infra files may still hold removed forms)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".data"))


class ScriptedLlm(FakeLlm):
    """A chat-completions server that asks for one tool call on the first request and answers in text after it."""

    def __init__(self, call: tuple[str, dict[str, Any]]) -> None:
        super().__init__()
        self.call = call

    def handle(self, request: Seen) -> Reply:
        if request.method != "POST" or "chat/completions" not in request.path:
            return super().handle(request)
        asked = any(m.get("role") == "tool" for m in (request.json() or {}).get("messages", []))
        if asked:
            return super().handle(request)
        name, arguments = self.call
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
            ],
        }
        return Reply(200, body={"choices": [{"message": message, "finish_reason": "tool_calls"}]})


class DelegatingLlm(ScriptedLlm):
    """The lead delegates to the payer; the payer calls the tool; each answers in text after its tool result."""

    def handle(self, request: Seen) -> Reply:
        body = request.json() or {}
        names = {t.get("function", {}).get("name") for t in body.get("tools", [])}
        self.call = ("delegate_to_payer", {"task": "pay"}) if "delegate_to_payer" in names else ("echo", {})
        return super().handle(request)


def _turn(
    tmp_path: Path,
    server: FakeMcp,
    call: tuple[str, dict[str, Any]],
    *,
    delegating: bool = False,
    gated: bool = False,
    principal: dict[str, Any] | None = None,
    drive: Callable[[SessionController], None] | None = None,
    flavour: dict[str, Any] | None = None,
    agent_extra: str = "",
    scheme: str = "none",
) -> str:
    """One turn of the entry agent, opened the way ``run-mas`` opens it, that calls ``call`` on the tool server.

    ``delegating``: the entry agent is a lead that delegates to a payer, and the payer calls the tool server.
    ``gated``: the entry agent has an identity gate that wants a caller that came through at least one other actor
    (``principal.chain_depth``); ``principal`` is bound around the turn, as an A2A inbound binds the verified one.
    ``drive`` runs the turn itself (an A2A server whose handler is this controller) instead of ``run_turn("go")``.
    """
    entry = "lead" if delegating else "desk"
    with (DelegatingLlm if delegating else ScriptedLlm)(call) as llm:
        refs = [
            str(tmp_path / r) if r.endswith(".yaml") else r
            for r in _write(tmp_path, server.url + "/mcp", "mcp", delegating=delegating)
        ]
        refs.remove("standard:openai")
        identity_file = tmp_path / "identity.yaml"
        identity_file.write_text(identity_file.read_text(encoding="utf-8").replace("scheme: none", f"scheme: {scheme}"))
        (tmp_path / "llm.yaml").write_text(
            "apiVersion: infra/v1\nkind: LLMProxy\nmetadata: {name: openai-direct}\nspec:\n  proxy:\n"
            f"    api_base: {llm.url}/v1\n    api_key: {{secret: {{name: lab, optional: true}}}}\n",
            encoding="utf-8",
        )
        refs.append(str(tmp_path / "llm.yaml"))
        if agent_extra:
            agent = (tmp_path / "agent.yaml").read_text(encoding="utf-8")
            (tmp_path / "agent.yaml").write_text(agent + agent_extra, encoding="utf-8")
        if gated:
            agent = (tmp_path / "agent.yaml").read_text(encoding="utf-8")
            (tmp_path / "agent.yaml").write_text(
                agent
                + "  governance:\n    - identity-gate:\n        requirements:\n          echo:\n"
                + "            identity: desk-id\n            require: [{fact: principal.chain_depth, min: 1}]\n",
                encoding="utf-8",
            )
        result = compose_run(ComposeRequest(manifest=tmp_path / "mas.yaml", validate=False, infra_refs=refs))
        result.resolved_infra = bind_session(
            result.resolved_infra, flavour or {}, agent_ids=agency_ids(result.mas_config)
        )
        args = _EntrySession(
            entry=entry,
            declared=entry,
            delegates=[],
            runtime_params=params_from_mas_config(result.mas_config),
            flavour_spec=flavour or {},
            session_interactive=False,
        )
        opened = open_entry_session(
            materialize_mas_compose(result, mas_base_dir=tmp_path), args, display=_QuietDisplay()
        )
        controller = SessionController(
            instance=opened.instance,
            display=StdoutConversationDisplay(show_labels=False, verbose=0),
            config=ConversationConfig(single_turn=True),
        )
        from mas.library.standard.lib.identity.principal import bind

        if drive is not None:
            drive(controller)
        else:
            with bind(principal or {}):
                controller.run_turn("go")
        close_observability(controller)
        # what the model was told about the call: the tool message of its last request
        last = (llm.seen[-1].json() or {}).get("messages", [])
        return "\n".join(str(m.get("content")) for m in last if m.get("role") == "tool")


def test_the_identity_layer_of_a_tool_server_is_bound_and_the_tool_call_goes_through(tmp_path: Path) -> None:
    """Kills: a session built from the infra as composed, before the Identity row was bound into the step."""
    with FakeMcp() as server:
        text = _turn(tmp_path, server, ("echo", {}))
        assert [name for name, _args, _meta in server.tool_calls] == ["echo"], text
        assert "echo:ok" in text


def test_a_tool_server_that_refuses_the_identity_reaches_the_model_as_a_closed_refusal(tmp_path: Path) -> None:
    """Kills: a refused session setup that reaches the model as ``unclassified:taskgroup`` or a closed connection."""

    def gate(request: Seen) -> Reply | None:
        if "authorization" in request.headers or request.rpc_method() != "tools/call":
            return None
        return Reply(401, headers={"WWW-Authenticate": 'Bearer realm="fares"'}, body={"error": "unauthorized"})

    with FakeMcp(gate=gate) as server:
        text = _turn(tmp_path, server, ("echo", {}))
        assert server.tool_calls == []
        assert "identity: refused:unauthorized" in text, text
        assert "unclassified" not in text and "Connection closed" not in text


def test_a_delegated_turn_reaches_the_tool_server_of_the_agent_it_delegates_to(tmp_path: Path) -> None:
    """Kills: the in-process bus delivering a delegation by a sync call that waits inside the running loop."""
    with FakeMcp() as server:
        text = _turn(tmp_path, server, ("delegate_to_payer", {"task": "pay"}), delegating=True)
        assert "cannot wait for async work" not in text, text
        assert [name for name, _args, _meta in server.tool_calls] == ["echo"], text


def test_a_delegated_turn_that_the_tool_server_refuses_reaches_the_lead_as_a_closed_refusal(tmp_path: Path) -> None:
    """Kills: a delegated turn that fails before it asks the tool server, hiding the refusal behind a bridge error."""

    def gate(request: Seen) -> Reply | None:
        if "authorization" in request.headers or request.rpc_method() != "tools/call":
            return None
        return Reply(401, headers={"WWW-Authenticate": 'Bearer realm="fares"'}, body={"error": "unauthorized"})

    with FakeMcp(gate=gate) as server:
        text = _turn(tmp_path, server, ("delegate_to_payer", {"task": "pay"}), delegating=True)
        assert server.tool_calls == []
        assert "cannot wait for async work" not in text, text


def test_a_verified_principal_reaches_a_gated_tool_call_in_the_kernel_turn(tmp_path: Path) -> None:
    """Kills: a verified caller (an A2A inbound binds it) that is lost before the kernel's egress gate reads it.

    The gate wants a caller that came through another actor. With the principal bound around the turn the call goes
    through; with none it is refused before it starts (a negative control: the gate really is in the path, reading the
    wallet of this run under the scope the control plane stamped).
    """
    with FakeMcp() as server:
        text = _turn(
            tmp_path, server, ("echo", {}), gated=True, principal={"actor": "desk", "subject": "u1", "chain": ["lead"]}
        )
        assert [name for name, _args, _meta in server.tool_calls] == ["echo"], text
    (tmp_path / "bare").mkdir()
    with FakeMcp() as server:
        text = _turn(tmp_path / "bare", server, ("echo", {}), gated=True)
        assert server.tool_calls == [] and "echo:ok" not in text


class _Chain:
    """A verifier for the server end of the A2A hop: `x-chain: yes` is a caller that came through the lead."""

    scheme = "chain"
    capabilities = frozenset({"verify"})
    PARAMS_SCHEMA: dict[str, Any] = {"type": "object"}

    @classmethod
    def from_spec(cls, params: Any) -> _Chain:
        return cls()

    def identities(self) -> list:
        return []

    def select(self, req: Any) -> None:
        return None

    async def present(self, req: Any) -> dict:
        return {}

    async def verify(self, inbound: Any) -> dict:
        from mas.library.standard.lib.identity.contract import IdentityError

        headers = inbound.get("headers", {})
        if "x-who" not in headers:
            raise IdentityError("no_credential")
        verified: dict[str, Any] = {"subject": headers["x-who"], "claims": {}, "source": "other"}
        if headers.get("x-chain") == "yes":
            verified["actors"] = ["lead"]
        return verified


def _a2a_drive(headers: dict[str, str]) -> Callable[[SessionController], None]:
    """Serve the controller behind a verifying A2A server and send it one message, as a peer would."""
    import asyncio
    import uuid

    import httpx
    from library_ioa.plugins.a2a.exposure import A2AExposure
    from library_ioa.plugins.a2a.server.app import build_app
    from library_ioa.plugins.a2a.server.executor import MasLabAgentExecutor
    from mas.library.standard.plugins.identity.layer import IdentityLayer
    from wiring_builders import agent_card

    def drive(controller: SessionController) -> None:
        def handler(prompt: str, **_: Any) -> dict[str, str]:
            controller.run_turn(prompt)
            return {"text": "done"}

        server = IdentityLayer(identity="srv", scheme="chain", params={}, agent_id="desk", schemes={"chain": _Chain})
        app = build_app(
            agent_card(), MasLabAgentExecutor(A2AExposure._adapt_runtime_handler(handler)), inbound=server.serve
        )
        body = {"message": {"messageId": str(uuid.uuid4()), "role": "ROLE_USER", "parts": [{"text": "go"}]}}

        async def send() -> None:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://srv") as client:
                await client.post(
                    "/a2a/rest/message:send",
                    json=body,
                    headers={"a2a-version": "1.0", "content-type": "application/json", **headers},
                )

        asyncio.run(send())

    return drive


def test_a_real_a2a_inbound_reaches_a_gated_tool_call_in_the_kernel_turn(tmp_path: Path) -> None:
    """Kills: a verified A2A caller whose principal is lost between the server's gate and the kernel's egress gate.

    The peer's message is verified by the server layer (it came through the lead), the SDK hands it to a handler that
    runs the entry agent's turn, and the agent's identity gate wants a caller with a chain. A caller verified with no
    chain is refused by that same gate: the control that shows the gate is in the path.
    """
    with FakeMcp() as server:
        _turn(tmp_path, server, ("echo", {}), gated=True, drive=_a2a_drive({"x-who": "alice", "x-chain": "yes"}))
        assert [name for name, _args, _meta in server.tool_calls] == ["echo"]
    (tmp_path / "bare").mkdir()
    with FakeMcp() as server:
        _turn(tmp_path / "bare", server, ("echo", {}), gated=True, drive=_a2a_drive({"x-who": "alice"}))
        assert server.tool_calls == []


class _Delegating:
    """A wallet of one parent identity that can derive narrower ones; it records what it was asked."""

    scheme = "wiring_delegating"
    capabilities = frozenset({"delegate", "describe"})
    PARAMS_SCHEMA: dict[str, Any] = {"type": "object"}
    DELEGATIONS: list[dict[str, Any]] = []

    def __init__(self) -> None:
        self.children: list[dict[str, Any]] = []

    @classmethod
    def from_spec(cls, params: Any) -> _Delegating:
        return cls()

    def identities(self) -> list:
        parent = {
            "id": "parent",
            "kind": "agent",
            "subject": "",
            "labels": [],
            "status": "active",
            "grant": {"scopes": ["read", "write"], "depth": 0},
        }
        return [parent, *self.children]

    def select(self, req: Any) -> str:
        return "parent"

    async def present(self, req: Any) -> dict:
        return {}

    async def describe(self, ref: str) -> dict:
        return {"facts": {"id": ref}}

    async def delegate(self, delegation: Any) -> dict:
        self.DELEGATIONS.append(dict(delegation))
        child = {
            "id": "child-1",
            "kind": "agent",
            "subject": "",
            "labels": [],
            "status": "active",
            "parent": delegation.get("identity") or "parent",
            "grant": dict(delegation["grant"]),
        }
        self.children.append(child)
        return child


@pytest.fixture
def _delegating(monkeypatch: pytest.MonkeyPatch) -> type[_Delegating]:
    from mas.library.standard.plugins.identity import layer as layer_module

    real = layer_module.lookup_scheme
    monkeypatch.setattr(
        layer_module,
        "lookup_scheme",
        lambda name, extra=None: _Delegating if name == "wiring_delegating" else real(name, extra),
    )
    _Delegating.DELEGATIONS.clear()
    return _Delegating


MANAGER = "  system_plugins: [identity]\n  governance:\n    - identity-guard: {allow: {identity_delegate: true}}\n"
OWN = {"bind": {"agents": {"desk": {"llm": {"stack": "desk-llm"}}}}}


def test_the_identity_tools_run_for_the_agent_that_holds_the_wallet(
    tmp_path: Path, _delegating: type[_Delegating]
) -> None:
    """Kills: system tools bound to agent "" (the real agent id never reached the plugin's session).

    The model asks for a delegation the guard allows. It runs against the wallet the identity layer built for this
    agent, through the real turn: the tool plugin is bound by the agent's own session, not by a hand-made one.
    """
    with FakeMcp() as server:
        text = _turn(
            tmp_path,
            server,
            ("identity_delegate", {"identity": "parent", "scopes": ["read"]}),
            scheme="wiring_delegating",
            flavour=OWN,
            agent_extra=MANAGER,
        )
    assert "child-1" in text, text
    assert [d["grant"]["scopes"] for d in _Delegating.DELEGATIONS] == [["read"]]


def test_the_identity_list_shows_the_agents_own_wallet_and_never_the_shared_one(
    tmp_path: Path, _delegating: type[_Delegating]
) -> None:
    """Kills: tools that list (or manage) the wallets of every agent, or of the shared tool-server stack.

    The record is also on the tool server's own stack, a wallet no agent in particular holds; the listing has the
    agent's own wallet once and nothing of the shared one.
    """
    with FakeMcp() as server:
        text = _turn(
            tmp_path,
            server,
            ("identity_list", {}),
            scheme="wiring_delegating",
            flavour=OWN,
            agent_extra=MANAGER,
        )
    start = text.index("[")
    listed = json.loads(text[start : text.rindex("]") + 1])
    assert [(row["record"], row["id"]) for row in listed] == [("desk-id", "parent")], text
