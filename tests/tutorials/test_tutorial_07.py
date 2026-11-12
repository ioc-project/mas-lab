#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tutorial 07 — SLIM link: the desk, the endpoint rows, the Flavours that link agents to them, fail-closed.

Offline: only the tutorial's own manifests are read. Nothing connects to a real node;
the node checks run over the in-process fake of the SLIM bindings.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from tutorial_support import T07, load_yaml, run_cli

INFRA = T07 / "infra"
FLAVOURS = T07 / "flavours"
SHOW = ["mas-ctl", "infra", "resolved", str(T07 / "mas.yaml")]


def _show(*refs: str, flavour: str = "", json_out: bool = False):
    args = list(SHOW)
    if json_out:
        args.append("--json")
    if flavour:
        args += ["--flavour", str(FLAVOURS / f"{flavour}.yaml")]
    for ref in refs:
        args += ["--infra-ref", str(INFRA / f"{ref}.yaml")]
    return run_cli(args)


def _rows(*refs: str, flavour: str) -> dict[str, dict]:
    r = _show(*refs, flavour=flavour, json_out=True)
    assert r.returncode == 0, r.stderr
    return {row["id"]: row for row in json.loads(r.stdout)["agents"]}


@contextmanager
def _desk(flavour: str, monkeypatch):
    """The tutorial's Flavour plus its node row and the Step 3 agent rows, over the in-process fake node."""
    import slim_node_fake as fake
    from library_ioa.plugins.slim.actor import ConnectionActor
    from mas.ctl.compose.models import ResolvedInfra
    from mas.ctl.executor.stacks import ChainHandles, chain_handle
    from mas.ctl.infra.bind import bind_flavour
    from mas.ctl.infra.resolve import resolve_infra_refs
    from mas.ctl.session.flavour import resolve_flavour
    from mas.ctl.workspace.config import WorkspaceConfig

    monkeypatch.setattr("library_ioa.plugins.slim.actor.load_slim_bindings", lambda: fake)
    monkeypatch.setattr(ConnectionActor, "SUBSCRIBE_SETTLE_S", 0.0)
    monkeypatch.setattr(ConnectionActor, "ROUTE_SETTLE_S", 0.0)
    monkeypatch.setenv("SLIM_SECRET", "e4aaecb9ae0b23b82086bb8a8633e01fba16ae8d9c1379a613c00838")
    fake.FakeWorld.reset()
    resolved: ResolvedInfra = bind_flavour(
        resolve_infra_refs([str(INFRA / "slim-node.yaml")], anchor=T07, workspace=WorkspaceConfig({})),
        resolve_flavour(str(FLAVOURS / f"{flavour}.yaml")),
        agent_ids=["triage", "billing", "outage"],
    )
    handles = ChainHandles()
    try:
        yield (
            fake,
            chain_handle(None, resolved.agent_chains["triage"], handles=handles),
        )
    finally:
        handles.close()
        fake.FakeWorld.reset()


class TestManifests:
    @pytest.mark.parametrize("name", ["mas.yaml", "specialists.yaml"])
    def test_validate(self, name):
        r = run_cli(["mas-ctl", "validate", str(T07 / name)])
        assert r.returncode == 0, r.stderr
        assert "OK" in r.stdout

    def test_every_agent_validates(self):
        for agent in ("triage", "billing", "outage"):
            r = run_cli(["mas-ctl", "validate", str(T07 / "agents" / agent / "agent.yaml")])
            assert r.returncode == 0, r.stderr

    @pytest.mark.parametrize("name", ["in-process", "slim-node", "outage-server", "agent-hub"])
    def test_every_infra_file_validates(self, name):
        r = run_cli(["mas-ctl", "validate", str(INFRA / f"{name}.yaml"), "--kind", "infra"])
        assert r.returncode == 0, r.stdout + r.stderr

    def test_specialists_are_the_desks_specialists(self):
        desk = {a["id"] for a in load_yaml(T07 / "mas.yaml")["spec"]["agency"]["agents"]}
        specialists = {a["id"] for a in load_yaml(T07 / "specialists.yaml")["spec"]["agency"]["agents"]}
        assert specialists == desk - {"triage"}

    def test_infra_names_endpoints_never_agents_and_the_flavours_define_nothing(self):
        agents = {"triage", "billing", "outage"}
        for path in INFRA.glob("*.yaml"):
            for row_name, row in load_yaml(path)["spec"]["endpoints"].items():
                assert row_name not in agents and row["protocol"], (path.name, row_name)
        for path in FLAVOURS.glob("*.yaml"):
            assert set(load_yaml(path)["spec"]) == {"bind"}, path.name


class TestNames:
    def test_the_names_do_not_change_when_the_medium_does(self):
        """Step 3's point: swapping the in-process row for the node row keeps every name."""
        in_process = _rows("in-process", flavour="in-process")
        slim = _rows("slim-node", flavour="slim")
        assert {k: v["identity"] for k, v in in_process.items()} == {k: v["identity"] for k, v in slim.items()}
        assert slim["billing"]["identity"] == "acme/lab/desk/billing"
        assert in_process["billing"]["comm"] == ["local-group"]
        assert slim["billing"]["comm"] == ["slim"]

    def test_with_no_chain_every_agent_has_the_in_process_link_and_no_name(self):
        r = _show(json_out=True)
        assert r.returncode == 0, r.stderr
        rows = {row["id"]: row for row in json.loads(r.stdout)["agents"]}
        assert {k: (v["comm"], v["identity"]) for k, v in rows.items()} == {
            name: (["local"], "") for name in ("triage", "billing", "outage")
        }

    def test_split_desk_names_only_triage(self):
        rows = _rows("slim-node", flavour="front-desk")
        assert rows["triage"]["identity"] == "acme/lab/desk/triage"
        assert not rows["billing"]["identity"] and not rows["outage"]["identity"]
        assert rows["billing"]["comm"] == ["slim"]  # a connect-only row still shows the chain

    def test_specialists_serve_both(self):
        r = run_cli(
            ["mas-ctl", "infra", "resolved", str(T07 / "specialists.yaml"), "--json"]
            + ["--flavour", str(FLAVOURS / "slim.yaml")]
            + ["--infra-ref", str(INFRA / "slim-node.yaml")]
        )
        assert r.returncode == 0, r.stderr
        rows = {row["id"]: row for row in json.loads(r.stdout)["agents"]}
        assert set(rows) == {"billing", "outage"}
        assert all(row["identity"].startswith("acme/lab/desk/") for row in rows.values())


class TestMixedTransports:
    def test_outage_keeps_its_own_url_and_gets_no_name(self):
        rows = _rows("slim-node", "outage-server", flavour="mixed")
        assert rows["billing"]["identity"] == "acme/lab/desk/billing"
        assert rows["outage"] == {"id": "outage", "comm": ["a2a"], "identity": ""}

    def test_outage_server_manifest_serves_the_same_url_the_desk_calls(self):
        desk = load_yaml(FLAVOURS / "mixed.yaml")["spec"]["bind"]["agents"]["outage"]
        server = load_yaml(FLAVOURS / "outage-server.yaml")["spec"]["bind"]["agents"]["outage"]
        assert desk == {"agent_comm": ["outage-server"], "usage": "connect"}
        assert server == {"agent_comm": ["outage-server"], "usage": "listen"}


class TestOverTheNode:
    def test_a_delegation_crosses_the_node(self, monkeypatch):
        """Step 3 offline: the tutorial's Flavour and rows, through the ``delegate_to_*`` tool the model calls."""
        from mas.library.standard.plugins.system_tools.send import SendTool
        from mas.runtime.boundary.agentcomm.routing import AgentCommRoute
        from mas.runtime.engine.tool_dispatch import execute_engine_tool

        with _desk("slim", monkeypatch) as (fake, desk):
            triage = desk.link("triage")
            desk.link("billing", run_turn=lambda agent, task, *_: f"{agent}: {task}")
            ctx = SimpleNamespace(
                agent_id="triage",
                manifest=None,
                agent_comm=triage,
                agent_comm_routes={"billing": AgentCommRoute("billing", desk.plugin_id, triage)},
                delegation_peer_descriptions=None,
                send_peer_descriptions=None,
            )
            reply = execute_engine_tool(
                "delegate_to_billing",
                engine_contracts=[SendTool(allowed_peers=["billing"])],
                ctx=ctx,
                arguments={"task": "I was charged twice"},
            )
            assert reply == "billing: I was charged twice"
            assert any(b"charged twice" in frame for frame in fake.FakeWorld.default().frames)

    def test_a2a_above_the_node_through_the_tool_the_model_calls(self, monkeypatch):
        """Step 5, A2A over the node: ``delegate_to_billing`` through ``execute_engine_tool`` with ``SendTool``,
        over the tutorial's Flavour on the in-process fake node; an inform is refused by the same tool."""
        from mas.library.standard.plugins.system_tools.send import SendTool
        from mas.runtime.boundary.agentcomm.routing import AgentCommRoute
        from mas.runtime.engine.tool_dispatch import execute_engine_tool

        with _desk("a2a-over-slim", monkeypatch) as (fake, desk):
            triage = desk.link("triage")
            desk.link("billing", run_turn=lambda agent, task, *_: f"{agent}: {task}")
            ctx = SimpleNamespace(
                agent_id="triage",
                manifest=None,
                agent_comm=triage,
                agent_comm_routes={"billing": AgentCommRoute("billing", desk.plugin_id, triage)},
                delegation_peer_descriptions=None,
                send_peer_descriptions=None,
            )
            tool = SendTool(allowed_peers=["billing"])

            def call(name: str, **arguments: object) -> str:
                return execute_engine_tool(name, engine_contracts=[tool], ctx=ctx, arguments=arguments)

            reply = call("delegate_to_billing", task="I was charged twice", context_id="case-12")
            assert reply == "billing: I was charged twice"
            request, answer = (json.loads(frame) for frame in fake.FakeWorld.default().frames[:2])
            rpc = json.loads(request["task"])
            assert rpc["method"] == "SendMessage"
            assert rpc["params"]["message"]["parts"] == [{"text": "I was charged twice"}]
            assert rpc["params"]["message"]["contextId"] == "case-12"
            assert json.loads(answer["task"])["result"]["message"]["parts"] == [{"text": reply}]
            sent = len(fake.FakeWorld.default().frames)
            # the model's tool only delegates (intent task): an intent argument is refused before any send
            refused = call("delegate_to_billing", task="fyi", intent="inform")
            assert refused == "[send] extra arguments ['intent'] not allowed on 'delegate_to_billing'"
            # an inform from Python (the path a design pattern takes) is refused by the A2A layer of the chain
            refused = tool.send("billing", "fyi", intent="inform", ctx=ctx)
            assert "intent 'inform' is not supported" in refused
            assert len(fake.FakeWorld.default().frames) == sent  # nothing left for the node


class TestAnAgentOnItsOwnChain:
    def test_billing_alone_rides_a2a_over_its_own_connection(self):
        rows = _rows("slim-node", flavour="billing-over-a2a")
        assert rows["billing"] == {
            "id": "billing",
            "comm": ["slim", "a2a-framing"],
            "identity": "acme/lab/desk-a2a/billing",
        }
        assert rows["outage"] == {"id": "outage", "comm": ["slim"], "identity": "acme/lab/desk/outage"}
        # triage sends to both, so it joins both chains, once each
        assert rows["triage"]["identity"] == "acme/lab/desk/triage, acme/lab/desk-a2a/triage"

    def test_triage_reaches_billing_over_a2a_and_outage_plainly(self, monkeypatch):
        import slim_node_fake as fake
        from library_ioa.plugins.slim.actor import ConnectionActor
        from mas.ctl.executor.stacks import ChainHandles, chain_links
        from mas.ctl.infra.bind import bind_session
        from mas.ctl.infra.identities import agency_ids, peers_of
        from mas.ctl.infra.resolve import resolve_infra_refs
        from mas.ctl.session.flavour import resolve_flavour
        from mas.ctl.workspace.config import WorkspaceConfig

        monkeypatch.setattr("library_ioa.plugins.slim.actor.load_slim_bindings", lambda: fake)
        monkeypatch.setattr(ConnectionActor, "SUBSCRIBE_SETTLE_S", 0.0)
        monkeypatch.setattr(ConnectionActor, "ROUTE_SETTLE_S", 0.0)
        monkeypatch.setenv("SLIM_SECRET", "e4aaecb9ae0b23b82086bb8a8633e01fba16ae8d9c1379a613c00838")
        fake.FakeWorld.reset()
        mas = load_yaml(T07 / "mas.yaml")
        resolved = bind_session(
            resolve_infra_refs([str(INFRA / "slim-node.yaml")], anchor=T07, workspace=WorkspaceConfig({})),
            resolve_flavour(str(FLAVOURS / "billing-over-a2a.yaml")),
            agent_ids=agency_ids(mas),
        )
        handles = ChainHandles()
        try:
            handlers, routes = chain_links(
                None,
                resolved,
                run_turn=lambda agent, task, *_: f"{agent}: {task}",
                local_ids=["triage", "billing", "outage"],
                handles=handles,
                peers=peers_of(mas, agency_ids(mas)),
            )
            assert set(handles) == {"desk-node", "desk-node-a2a+desk-a2a"}
            assert routes["triage"]["billing"].handler.send("billing", "refund?") == "billing: refund?"
            assert routes["triage"]["outage"].handler.send("outage", "down?") == "outage: down?"
            frames = [frame.decode("utf-8", "replace") for frame in fake.FakeWorld.default().frames]
            assert any("SendMessage" in frame and "refund?" in frame for frame in frames)
            assert any("down?" in frame and "SendMessage" not in frame for frame in frames)
            assert handlers["triage"] is routes["triage"]["triage"].handler
        finally:
            handles.close()
            fake.FakeWorld.reset()


class TestOneRowForManyAgents:
    def test_the_hub_row_gives_each_agent_its_own_url_and_outage_overrides_it(self):
        from mas.ctl.infra.bind import bind_session
        from mas.ctl.infra.identities import agency_ids
        from mas.ctl.infra.resolve import resolve_infra_refs
        from mas.ctl.session.flavour import resolve_flavour
        from mas.ctl.workspace.config import WorkspaceConfig

        resolved = bind_session(
            resolve_infra_refs(
                [str(INFRA / "slim-node.yaml"), str(INFRA / "agent-hub.yaml")],
                anchor=T07,
                workspace=WorkspaceConfig({}),
            ),
            resolve_flavour(str(FLAVOURS / "hub.yaml")),
            agent_ids=agency_ids(load_yaml(T07 / "mas.yaml")),
        )
        assert resolved.agent_chains["billing"][0]["params"]["url"] == "https://agents.example.test/a2a/billing"
        assert resolved.agent_chains["outage"][0]["params"]["url"] == "https://outage.example.test/a2a"
        assert resolved.agent_usage["billing"] == ["connect"]
        rows = _rows("slim-node", "agent-hub", flavour="hub")
        assert rows["billing"] == {"id": "billing", "comm": ["http", "a2a-framing"], "identity": ""}

    def test_hosting_an_agent_on_the_connect_only_hub_is_refused(self, tmp_path):
        flavour = tmp_path / "host-on-hub.yaml"
        flavour.write_text(
            "apiVersion: flavour/v1\nkind: Flavour\nmetadata: {name: x}\nspec:\n  bind:\n    agent_comm: [desk-node]\n"
            "    agents:\n      billing: {agent_comm: [agent-hub, desk-a2a], usage: listen}\n",
            encoding="utf-8",
        )
        r = run_cli(
            SHOW
            + ["--flavour", str(flavour), "--infra-ref", str(INFRA / "slim-node.yaml")]
            + ["--infra-ref", str(INFRA / "agent-hub.yaml")]
        )
        assert r.returncode == 1
        assert "bind.agents.billing: usage listen is not a capability of row 'agent-hub'" in r.stderr + r.stdout


class TestFailClosed:
    @pytest.mark.parametrize(
        ("flavour", "refs", "message"),
        [
            ("broken/unknown-agent", ("slim-node",), "Flavour bind.agents names 'billng', which is no agent"),
            ("broken/unknown-row", ("slim-node",), "Flavour bind.agent_comm names 'desk-nod', which is no"),
            (
                "broken/wrong-first-layer",
                ("slim-node",),
                "the first layer must be a transport; 'a2a-framing' needs a layer below it",
            ),
            ("slim", ("broken/bad-identity",), "identity must be a three-part org/ns/service name"),
        ],
    )
    def test_broken_manifest_exits_one_with_one_line(self, flavour, refs, message):
        r = _show(*refs, flavour=flavour)
        assert r.returncode == 1
        assert message in r.stderr + r.stdout
        assert "Traceback" not in r.stderr
