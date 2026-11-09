#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Flavour ``bind``: agents linked to endpoint rows, the session wiring, every entry point, ``--flavour PATH``."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import slim_node_fake as slim_fake
import yaml
from mas.ctl.compose.models import AgentBindSlice, EffectiveBindManifest, PlacementPlan, ResolvedInfra
from mas.ctl.compose.runner import ComposeResult
from mas.ctl.executor.mas_session import MaterializedMas, wire_peer_delegation
from mas.ctl.executor.stacks import ChainHandles
from mas.ctl.infra.bind import BindError, bind_flavour
from mas.ctl.infra.resolve import resolve_infra_refs
from mas.ctl.session.flavour import FlavourError, resolve_flavour
from mas.ctl.validate import validate_file
from mas.ctl.workspace.config import WorkspaceConfig
from mas.library.standard.plugins.system_tools.send import SendTool

FakeWorld = slim_fake.FakeWorld
_SECRET = "e4aaecb9ae0b23b82086bb8a8633e01fba16ae8d9c1379a613c00838"
_BOTH = ["connect", "listen"]
_NODE = {
    "protocol": "slim",
    "url": "http://127.0.0.1:46357",
    "identity": "acme/lab/desk",
    "secret_env": "SLIM_SECRET",
    "usage": _BOTH,
}
_NODE_A2A = {**_NODE, "identity": "acme/lab/desk-a2a"}
_ROWS = {
    "desk-node": _NODE,
    "desk-node-a2a": _NODE_A2A,
    "desk-a2a": {"protocol": "a2a-framing", "usage": _BOTH},
    "desk-group": {"protocol": "local-group", "identity": "acme/lab/desk", "usage": _BOTH},
    "outage-server": {"protocol": "a2a", "url": "http://127.0.0.1:9003", "usage": _BOTH},
    "agent-hub": {"protocol": "http", "url": "https://hub.example.test/agents/{agent}"},
}
_DESK = ["triage", "billing", "outage"]


def _write(path: Path, doc: dict) -> Path:
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def _rows(**extra: dict) -> dict:
    return {**{name: dict(row) for name, row in _ROWS.items()}, **extra}


def _bind(bind: dict, *, agents: list[str] | None = None, rows: dict | None = None) -> ResolvedInfra:
    return bind_flavour(
        ResolvedInfra(applications=rows if rows is not None else _rows()), {"bind": bind}, agent_ids=agents or _DESK
    )


# --- bind_flavour -----------------------------------------------------------------------------------------------


def test_the_default_chain_links_every_agent_and_infra_keeps_no_endpoint_row() -> None:
    resolved = _bind({"agent_comm": ["desk-node", "desk-a2a"]})
    chain = resolved.agent_chains["triage"]
    assert [layer["row"] for layer in chain] == ["desk-node", "desk-a2a"]
    assert chain[0]["params"] == {
        "url": "http://127.0.0.1:46357",
        "identity": "acme/lab/desk",
        "secret_env": "SLIM_SECRET",
    }
    assert chain[0]["usage"] == _BOTH
    assert set(resolved.agent_chains) == set(_DESK)
    assert all(usage == ["connect"] for usage in resolved.agent_usage.values())
    assert "desk-node" not in resolved.applications and "desk-a2a" not in resolved.applications


def test_an_agent_entry_sets_its_own_chain_and_its_usage() -> None:
    resolved = _bind(
        {
            "agent_comm": ["desk-node"],
            "agents": {"billing": {"agent_comm": ["desk-node-a2a", "desk-a2a"]}, "outage": {"usage": "connect"}},
        }
    )
    assert [layer["row"] for layer in resolved.agent_chains["billing"]] == ["desk-node-a2a", "desk-a2a"]
    assert [layer["row"] for layer in resolved.agent_chains["outage"]] == ["desk-node"]
    assert resolved.agent_usage == {
        "triage": ["connect"],
        "billing": ["connect"],
        "outage": ["connect"],
    }


def test_bind_usage_hosts_every_agent_that_omits_its_own() -> None:
    resolved = _bind({"agent_comm": ["desk-node"], "usage": ["connect", "listen"]})
    assert all(usage == _BOTH for usage in resolved.agent_usage.values())
    resolved = _bind(
        {"agent_comm": ["desk-node"], "usage": ["connect", "listen"], "agents": {"outage": {"usage": "connect"}}}
    )
    assert resolved.agent_usage == {"triage": _BOTH, "billing": _BOTH, "outage": ["connect"]}


def test_no_bind_leaves_released_agent_rows_as_they_are_and_links_no_agent() -> None:
    rows = {"outage": {"protocol": "a2a", "url": "http://127.0.0.1:9003", "usage": "connect"}}
    resolved = bind_flavour(ResolvedInfra(applications=rows), None, agent_ids=_DESK)
    assert resolved.agent_chains == {}
    assert resolved.applications == rows


def test_a_released_agent_row_keeps_its_direct_path_beside_a_default_chain() -> None:
    released = {"protocol": "a2a", "url": "http://127.0.0.1:9003", "usage": "connect"}
    resolved = _bind({"agent_comm": ["desk-node"]}, rows=_rows(outage=released))
    assert "outage" not in resolved.agent_chains
    assert resolved.applications["outage"] == released


def test_a_one_row_chain_on_an_a2a_server_is_the_released_direct_path() -> None:
    resolved = _bind(
        {"agent_comm": ["desk-node"], "agents": {"outage": {"agent_comm": ["outage-server"], "usage": "connect"}}}
    )
    assert "outage" not in resolved.agent_chains
    assert resolved.applications["outage"] == {
        "protocol": "a2a",
        "url": "http://127.0.0.1:9003",
        "usage": ["connect"],
    }
    with pytest.raises(BindError, match="usage listen is not a capability of row 'outage-server'"):
        _bind(
            {"agents": {"outage": {"agent_comm": ["outage-server"], "usage": "listen"}}},
            rows=_rows(**{"outage-server": {"protocol": "a2a", "url": "http://127.0.0.1:9003"}}),
        )
    assert "outage-server" not in resolved.applications


@pytest.mark.parametrize(
    ("bind", "rows", "message"),
    [
        ({"agent_comm": ["nowhere"]}, None, "names 'nowhere', which is no Application row"),
        ({"agent_comm": ["desk-node"]}, _rows(**{"desk-node": {"url": "http://x"}}), "needs protocol"),
        (
            {"agent_comm": ["desk-node"], "usage": "listen"},
            _rows(**{"desk-node": {**_NODE, "usage": "connect"}}),
            "bind.agents.triage: usage listen is not a capability of row 'desk-node'",
        ),
        (
            {"agent_comm": ["desk-node"], "usage": "listen"},
            _rows(**{"desk-node": {k: v for k, v in _NODE.items() if k != "usage"}}),
            "usage listen is not a capability of row 'desk-node' \\(its usage is \\['connect'\\]\\)",
        ),
        (
            {"agent_comm": ["desk-node"], "agents": {"billing": {"params": {"desk-a2a": {"x": 1}}}}},
            None,
            "bind.agents.billing.params names 'desk-a2a', which is not a row of the agent's chain",
        ),
        (
            {"agent_comm": ["desk-node"], "agents": {"billing": {"params": {"desk-node": {"identity": "{team}"}}}}},
            None,
            "identity holds \\{team\\}; the only placeholder is \\{agent\\}",
        ),
        (
            {"agent_comm": ["desk-node"], "agents": {"billing": {"params": {"desk-node": {"secret": "x"}}}}},
            None,
            "params: \\['secret'\\] would hold a secret",
        ),
        ({"agents": {"billing": {"params": {"desk-node": {"x": 1}}}}}, None, "has usage or params but no chain"),
        ({"agent_comm": ["desk-node"]}, _rows(**{"desk-node": {**_NODE, "secret": "x" * 40}}), "would hold a secret"),
        (
            {"agent_comm": ["desk-node"]},
            _rows(**{"desk-node": {**_NODE, "headers": {"Authorization": "b"}}}),
            "would hold a secret",
        ),
        ({"agent_comm": ["bogus"]}, _rows(bogus={"protocol": "nope"}), "unknown layer 'nope'"),
        ({"agent_comm": ["desk-a2a"]}, None, "the first layer must be a transport; 'a2a-framing' needs a layer below"),
        ({"agent_comm": ["outage-server"]}, None, "'outage-server' is a link to one agent's own url"),
        ({"agent_comm": ["desk-a2a", "desk-node"]}, None, "the first layer must be a transport; 'a2a-framing' needs"),
        ({"agent_comm": ["desk-node", "outage-server"]}, None, "layer 1 \\('a2a'\\) is a transport; only the first"),
        ({"agent_comm": ["outage-server", "desk-node"]}, None, "layer 1 \\('slim'\\) is a transport; only the first"),
        ({"agent_comm": "desk-node"}, None, "list of Application row names"),
        ({"agent_comm": ["desk-node", "desk-node"]}, None, "names a row twice"),
        ({"agents": {"ghost": {"usage": "connect"}}}, None, "bind.agents names 'ghost', which is no agent"),
        ({"agents": {"billing": ["desk-node"]}}, None, "bind.agents.billing takes agent_comm"),
        ({"agents": {"billing": {"agent_comm": []}}}, None, "bind.agents.billing.agent_comm is a list"),
        ({"agents": {"billing": {"usage": "serve"}}}, None, "usage is connect, listen"),
        ({"agents": {"billing": {"usage": "connect"}}}, None, "has usage or params but no chain"),
        ({"agent_comm": ["desk-node"], "wires": []}, None, r"bind: unknown keys \['wires'\]"),
        ({"agent_comm": ["triage"]}, _rows(triage=dict(_NODE)), "names a row that is also an agent"),
        ({"agent_comm": ["desk-node"]}, _rows(billing={"usage": "connect"}), "named by an agent but names no protocol"),
        (
            {"agent_comm": ["desk-node"]},
            _rows(billing={"protocol": "a2a", "usage": "connect"}),
            "'billing': an agent-keyed row needs url",
        ),
        (
            {"agent_comm": ["desk-node"], "agents": {"billing": {"agent_comm": ["desk-node", "desk-a2a"]}}},
            None,
            "its bottom row 'desk-node' is also the bottom of another chain",
        ),
        (
            {"agents": {"outage": {"agent_comm": ["outage-server"]}}},
            _rows(**{"outage-server": {"protocol": "a2a", "usage": _BOTH}}),
            "'outage-server': bind.agents.outage.agent_comm reaches the agent at this row, so it needs url",
        ),
        (
            {"agent_comm": ["desk-node"], "agents": {"outage": {"usage": "connect"}}},
            _rows(outage={"protocol": "a2a", "url": "http://127.0.0.1:9", "usage": "connect"}),
            "is an agent-keyed row, so bind.agents.outage cannot also link it",
        ),
    ],
)
def test_a_bad_bind_is_refused(bind: dict, rows: dict | None, message: str) -> None:
    with pytest.raises(BindError, match=message):
        _bind(bind, rows=rows)


def test_bind_must_be_a_map() -> None:
    with pytest.raises(BindError, match="bind must be a map"):
        bind_flavour(ResolvedInfra(applications=_rows()), {"bind": ["desk-node"]}, agent_ids=_DESK)


def test_a_connect_only_transport_cannot_declare_listen_and_connect_is_fine() -> None:
    rows = _rows(web={"protocol": "http", "url": "https://agents.example.test", "usage": _BOTH})
    with pytest.raises(BindError, match="Application 'web': usage listen: the http transport only connects"):
        _bind({"agent_comm": ["web"]}, rows=rows)
    rows = _rows(web={"protocol": "http", "url": "https://agents.example.test"})
    resolved = _bind({"agent_comm": ["web"]}, rows=rows)
    assert all(usage == ["connect"] for usage in resolved.agent_usage.values())
    with pytest.raises(BindError, match="usage listen is not a capability of row 'web'"):
        _bind({"agent_comm": ["web"], "usage": "listen"}, rows=rows)
    resolved = _bind({"agent_comm": ["web"], "agents": {a: {"usage": "connect"} for a in _DESK}}, rows=rows)
    assert resolved.agent_chains["triage"][0]["type"] == "http"


@pytest.mark.parametrize("agent_id", ["a/b", "a?b", "a#b", "a b", "a%2Fb", "é", ".", ".."])
def test_an_agent_id_outside_the_safe_set_cannot_fill_a_template(agent_id: str) -> None:
    flavour = {"agent_comm": ["desk-node"], "agents": {agent_id: {"agent_comm": ["agent-hub"], "usage": "connect"}}}
    with pytest.raises(BindError, match=r"cannot fill \{agent\} in url; an id there holds only letters"):
        _bind(flavour, agents=["triage", agent_id])
    safe = {"agent_comm": ["desk-node"], "agents": {"b.c_d-1": {"agent_comm": ["agent-hub"], "usage": "connect"}}}
    assert _bind(safe, agents=["b.c_d-1"]).agent_chains["b.c_d-1"][0]["params"]["url"].endswith("/agents/b.c_d-1")


@pytest.mark.parametrize(
    ("agent_id", "message"),
    [
        ("a.b", r"agent 'a.b' cannot ride row 'desk-node' \(layer 'slim'\): SLIM agent id cannot contain '\.': 'a\.b'"),
        ("a b", r"agent 'a b' cannot ride row 'desk-node' \(layer 'slim'\): illegal name part 'a b'"),
        ("x/y", r"agent 'x/y' cannot ride row 'desk-node' \(layer 'slim'\): agent id 'x/y' is outside the identity"),
    ],
)
def test_an_agent_id_the_layer_cannot_name_is_refused_at_bind_time(agent_id: str, message: str) -> None:
    """The SLIM grammar is stricter than the ``{agent}`` url grammar; the row does not use ``{agent}``."""
    with pytest.raises(BindError, match=message):
        _bind({"agent_comm": ["desk-node"]}, agents=["triage", agent_id])
    with pytest.raises(BindError, match=message):  # a per-agent chain is checked too
        _bind({"agents": {agent_id: {"agent_comm": ["desk-node", "desk-a2a"]}}}, agents=["triage", agent_id])


def test_the_layers_above_the_bottom_and_the_identity_are_checked_too() -> None:
    rows = _rows(**{"desk-node": {**_NODE, "identity": "acme/lab/de.sk"}})
    with pytest.raises(BindError, match=r"agent 'triage' cannot ride row 'desk-node' .*service cannot contain '\.'"):
        _bind({"agent_comm": ["desk-node"]}, rows=rows)
    rows = _rows(**{"desk-node": {**_NODE, "identity": "acme/desk"}})
    with pytest.raises(BindError, match=r"agent 'triage' cannot ride row 'desk-node' .*three-part"):
        _bind({"agent_comm": ["desk-node"]}, rows=rows)


def test_an_id_with_a_dot_rides_layers_whose_grammar_allows_it() -> None:
    for chain in (["desk-group"], ["agent-hub"]):
        resolved = _bind({"agent_comm": chain, "agents": {"a.b": {"usage": "connect"}}}, agents=["a.b"])
        assert set(resolved.agent_chains) == {"a.b"}


def test_a_template_row_serves_each_agent_at_its_own_url_and_params_override_it() -> None:
    resolved = _bind(
        {
            "agent_comm": ["desk-node"],
            "agents": {
                "billing": {"agent_comm": ["agent-hub", "desk-a2a"], "usage": "connect"},
                "outage": {
                    "agent_comm": ["agent-hub", "desk-a2a"],
                    "usage": "connect",
                    "params": {"agent-hub": {"url": "https://outage.example.test/a2a"}},
                },
            },
        }
    )
    assert resolved.agent_chains["billing"][0]["params"]["url"] == "https://hub.example.test/agents/billing"
    assert resolved.agent_chains["outage"][0]["params"]["url"] == "https://outage.example.test/a2a"
    assert resolved.agent_chains["billing"][0]["chain"] == "agent-hub+desk-a2a@billing"
    assert resolved.agent_chains["triage"][0]["chain"] == "desk-node"  # no template, one chain for all
    templated = _bind(
        {
            "agent_comm": ["desk-node"],
            "agents": {"billing": {"params": {"desk-node": {"identity": "acme/{agent}/desk"}}}},
        }
    )
    assert templated.agent_chains["billing"][0]["params"]["identity"] == "acme/billing/desk"


def test_endpoint_rows_validate_against_the_infra_schema_and_a_row_still_needs_protocol(tmp_path: Path) -> None:
    doc = {
        "apiVersion": "infra/v1",
        "kind": "Application",
        "metadata": {"name": "desk"},
        "spec": {"endpoints": {name: dict(row) for name, row in _ROWS.items()}},
    }
    assert validate_file(_write(tmp_path / "desk.yaml", doc), kind="infra", strict=True).ok
    doc["spec"]["endpoints"]["desk-node"]["secret"] = "x" * 40
    assert not validate_file(_write(tmp_path / "secret.yaml", doc), kind="infra", strict=True).ok
    agent_only = {**doc, "spec": {"endpoints": {"billing": {"usage": ["connect", "listen"]}}}}
    assert not validate_file(_write(tmp_path / "agent.yaml", agent_only), kind="infra", strict=True).ok


# --- --flavour PATH ---------------------------------------------------------------------------------------------


def _flavour(tmp_path: Path, spec: dict, *, kind: str = "Flavour") -> Path:
    return _write(
        tmp_path / "desk.yaml", {"apiVersion": "flavour/v1", "kind": kind, "metadata": {"name": "desk"}, "spec": spec}
    )


def test_a_flavour_file_is_read_validated_and_patched(tmp_path: Path) -> None:
    path = _flavour(tmp_path, {"bind": {"agent_comm": ["desk-node"], "agents": {"billing": {"usage": "connect"}}}})
    assert resolve_flavour(str(path))["bind"]["agents"] == {"billing": {"usage": "connect"}}
    patched = resolve_flavour(str(path), overrides=['.flavour.spec.bind.agent_comm=["other"]'])
    assert patched["bind"]["agent_comm"] == ["other"]
    assert resolve_flavour("local") == {}  # a bundled name still works


def test_a_row_holding_a_secret_is_refused_even_when_no_agent_rides_it() -> None:
    rows = _rows(**{"desk-node": {**_NODE, "secret": "x" * 40}})
    with pytest.raises(BindError, match=r"Application 'desk-node': \['secret'\] would hold a secret; name its env var"):
        bind_flavour(ResolvedInfra(applications=rows), {"bind": {"agent_comm": ["desk-node"]}}, agent_ids=[])


_SECRET_NAMES = [
    "X-Api-Key",
    "Authorization",
    "access_token",
    "Set-Cookie",
    "accessToken",
    "authToken",
    "clientSecret",
    "sessionToken",
    "private_key",
    "passphrase",
    "credentials",
    "client-secret",
    "auth_password",
    "apiKey",
    "TOKEN",
]
_PLAIN_NAMES = ["secret_env", "max_tokens", "timeout", "identity", "tokenizer", "author", "content-type", "api_key_env"]


@pytest.mark.parametrize("key", _SECRET_NAMES)
def test_a_header_or_param_that_ends_in_a_secret_word_is_refused(key: str) -> None:
    rows = _rows(**{"desk-node": {**_NODE, "headers": {key: "v"}}})
    with pytest.raises(BindError, match="would hold a secret"):
        bind_flavour(ResolvedInfra(applications=rows), {"bind": {"agent_comm": ["desk-node"]}}, agent_ids=[])


@pytest.mark.parametrize("key", _PLAIN_NAMES)
def test_a_key_that_only_contains_a_secret_word_is_kept(key: str) -> None:
    from mas.ctl.infra.bind import secret_keys

    assert secret_keys({key: "v"}) == []


def test_the_schema_and_ctl_refuse_the_same_secret_keys() -> None:
    import jsonschema
    import yaml
    from mas.ctl.infra.bind import SECRET_KEY, secret_keys

    path = Path(__file__).resolve().parents[2] / "docs/schemas/runtime/fragments/application-endpoint.schema.yaml"
    schema = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert schema["propertyNames"]["not"]["pattern"] == SECRET_KEY.pattern
    assert schema["properties"]["headers"]["propertyNames"] == schema["propertyNames"]
    validator = jsonschema.Draft7Validator(schema)
    for key in _SECRET_NAMES + _PLAIN_NAMES:
        refused_by_ctl = bool(secret_keys({"headers": {key: "v"}}))
        refused_by_schema = not validator.is_valid({"protocol": "http", "headers": {key: "v"}})
        refused_as_param = not jsonschema.Draft7Validator({"propertyNames": schema["propertyNames"]}).is_valid({key: 1})
        assert refused_by_ctl == refused_by_schema == refused_as_param == (key in _SECRET_NAMES), key


def test_a_bad_flavour_file_is_a_flavour_error(tmp_path: Path) -> None:
    with pytest.raises(FlavourError, match="not a kind: Flavour document"):
        resolve_flavour(str(_flavour(tmp_path, {}, kind="Agent")))
    with pytest.raises(FlavourError, match="manifest is invalid"):
        resolve_flavour(str(_flavour(tmp_path, {"bind": {"agent_comm": "desk-node"}})))
    with pytest.raises(FlavourError, match="manifest is invalid"):
        resolve_flavour(str(_flavour(tmp_path, {"bind": {"agents": {"billing": {"usage": "serve"}}}})))
    with pytest.raises(FlavourError, match="cannot be read"):
        resolve_flavour(str(tmp_path / "missing.yaml"))
    with pytest.raises(
        FlavourError, match=r"bundled names: local, prod\); pass --flavour PATH to a Flavour file"
    ) as exc:
        resolve_flavour("nope")
    assert "flavour list" not in str(exc.value)


# --- session wiring ---------------------------------------------------------------------------------------------

_TRIAGE_TO_BILLING = {"id": "triage", "delegates_to": ["billing"]}


def _materialized(agent_ids: list[str], tmp_path: Path, resolved: ResolvedInfra, nodes: list[dict] | None = None):
    instances = {
        aid: SimpleNamespace(driver=SimpleNamespace(agent_id=None, engine=MagicMock(inner=None))) for aid in agent_ids
    }
    compose = ComposeResult(
        mas_id="desk",
        mas_config={
            "spec": {
                "workflow": {"entry": "triage", "nodes": nodes or [_TRIAGE_TO_BILLING, {"id": "billing"}]},
                "agency": {"agents": [{"id": aid} for aid in agent_ids]},
            }
        },
        effective_bind={},
        placement_plan={},
        deployment={},
        infra_refs=[],
        bind=EffectiveBindManifest(
            mas_id="desk",
            spec_revision="",
            runtime_id="mas-runtime-py",
            deployment_name="local",
            agents=[AgentBindSlice(agent_id=aid, pattern_plugin_id="react@v1") for aid in agent_ids],
        ),
        plan=PlacementPlan(),
        resolved_infra=resolved,
    )
    return SimpleNamespace(
        compose=compose,
        materialized=SimpleNamespace(instances=instances),
        mas_base_dir=tmp_path,
        stack_handles=ChainHandles(),
    )


def _wire(materialized, *, already_wired: set[str] | None = None) -> list[dict]:
    wired: list[dict] = []

    def run_turn(agent_id: str, task: str, *_args: object) -> str:
        return f"ok:{agent_id}:{task}"

    def record(engine, *a, **k):
        del engine, a
        wired.append(k)

    with patch("mas.ctl.executor.mas_session.make_workflow_send", return_value=run_turn):
        with patch(
            "mas.ctl.executor.mas_session.load_agent_manifest_from_bind",
            return_value={"metadata": {"name": "x"}, "spec": {}},
        ):
            with patch("mas.ctl.executor.mas_session.wire_entry_engine_delegation", side_effect=record):
                wire_peer_delegation(materialized, entry_id="triage", already_wired=already_wired or set())
    return wired


@pytest.fixture()
def fake_node(monkeypatch: pytest.MonkeyPatch):
    from library_ioa.plugins.slim.actor import ConnectionActor

    monkeypatch.setenv("SLIM_SECRET", _SECRET)
    monkeypatch.setattr("library_ioa.plugins.slim.actor.load_slim_bindings", lambda: slim_fake)
    monkeypatch.setattr(ConnectionActor, "SUBSCRIBE_SETTLE_S", 0.0)
    monkeypatch.setattr(ConnectionActor, "ROUTE_SETTLE_S", 0.0)
    FakeWorld.reset()
    yield FakeWorld.default()
    FakeWorld.reset()


def test_a_slim_chain_carries_the_send_across_the_node(tmp_path: Path, fake_node) -> None:
    rows = _write(
        tmp_path / "desk.yaml",
        {"apiVersion": "infra/v1", "kind": "Application", "metadata": {"name": "desk"}, "spec": {"endpoints": _ROWS}},
    )
    resolved = bind_flavour(
        resolve_infra_refs([str(rows)], anchor=tmp_path, workspace=WorkspaceConfig({})),
        {"bind": {"agent_comm": ["desk-node"], "usage": ["connect", "listen"]}},
        agent_ids=["triage", "billing"],
    )
    materialized = _materialized(["triage", "billing"], tmp_path, resolved)
    try:
        caller = next(item for item in _wire(materialized) if item["entry_agent_id"] == "triage")
        assert caller["comm"].plugin_id == "slim"
        assert caller["comm"]._layers[0].link.name == "acme/lab/desk/triage"
        assert caller["peers"] == ["billing"]
        tool = SendTool(comm=caller["comm"], routes=caller["routes"], allowed_peers=["billing"])
        assert tool.send("billing", "refund") == "ok:billing:refund"
        assert any(b"refund" in frame for frame in fake_node.frames)
        assert _SECRET not in repr(caller["comm"])
        actor = next(iter(materialized.stack_handles.values()))._services[0].actor
    finally:
        materialized.stack_handles.close()
    assert materialized.stack_handles == {}
    assert actor.thread is None or not actor.thread.is_alive()


def test_a_local_group_chain_routes_in_process(tmp_path: Path) -> None:
    resolved = _bind({"agent_comm": ["desk-group"], "usage": ["connect", "listen"]}, agents=["triage", "billing"])
    materialized = _materialized(["triage", "billing"], tmp_path, resolved)
    try:
        caller = next(item for item in _wire(materialized) if item["entry_agent_id"] == "triage")
        assert caller["comm"]._layers[0].link.name == "acme/lab/desk/triage"
        tool = SendTool(comm=caller["comm"], routes=caller["routes"], allowed_peers=["billing"])
        assert tool.send("billing", "refund") == "ok:billing:refund"
    finally:
        materialized.stack_handles.close()


def test_a_direct_agent_and_a_connect_only_agent_never_join_the_node(tmp_path: Path, fake_node) -> None:
    del fake_node
    resolved = _bind(
        {
            "agent_comm": ["desk-node"],
            "usage": ["connect", "listen"],
            "agents": {"outage": {"agent_comm": ["outage-server"], "usage": "connect"}, "notes": {"usage": "connect"}},
        },
        agents=["triage", "billing", "outage", "notes"],
    )
    triage = {"id": "triage", "delegates_to": ["billing", "outage", "notes"]}
    nodes = [triage, {"id": "billing"}, {"id": "outage"}, {"id": "notes"}]
    materialized = _materialized(["triage", "billing", "outage", "notes"], tmp_path, resolved, nodes)
    try:
        caller = next(item for item in _wire(materialized) if item["entry_agent_id"] == "triage")
        routes = caller["routes"]
        assert routes["billing"].handler is caller["comm"]  # over the chain, on the caller's own link
        assert routes["notes"].handler is caller["comm"]  # dialled over the chain; served elsewhere
        assert routes["outage"].kind == "a2a" and routes["outage"].handler is not caller["comm"]
        handle = next(iter(materialized.stack_handles.values()))
        assert set(handle._links) == {"triage", "billing"}  # outage and notes are not served here
    finally:
        materialized.stack_handles.close()


def test_with_no_chain_every_agent_keeps_the_in_process_link(tmp_path: Path) -> None:
    resolved = bind_flavour(ResolvedInfra(applications=_rows()), None, agent_ids=["triage", "billing"])
    materialized = _materialized(["triage", "billing"], tmp_path, resolved)
    caller = next(item for item in _wire(materialized) if item["entry_agent_id"] == "triage")
    assert caller["routes"]["billing"].kind == "local"
    assert materialized.stack_handles == {}


def test_a_sender_that_is_not_served_still_gets_a_link_to_reach_its_targets(tmp_path: Path, fake_node) -> None:
    del fake_node
    resolved = _bind(
        {
            "agent_comm": ["desk-node"],
            "usage": ["connect", "listen"],
            "agents": {"triage": {"usage": "connect"}},
        },
        agents=["triage", "billing"],
    )
    materialized = _materialized(["triage", "billing"], tmp_path, resolved)
    try:
        caller = next(item for item in _wire(materialized) if item["entry_agent_id"] == "triage")
        assert caller["comm"].plugin_id == "slim"
        assert caller["routes"]["billing"].handler is caller["comm"]
        assert caller["routes"]["triage"].kind == "local"  # not served on the chain, so only the in-process route
    finally:
        materialized.stack_handles.close()


def test_routes_are_set_on_an_agent_wired_before(tmp_path: Path, fake_node) -> None:
    del fake_node
    resolved = _bind({"agent_comm": ["desk-node"]}, agents=["triage", "billing"])
    materialized = _materialized(["triage", "billing"], tmp_path, resolved)
    engine = MagicMock(inner=None)
    materialized.materialized.instances["triage"].driver.engine = engine
    try:
        with patch("mas.ctl.executor.subagent_spawner.wire_subagent_spawning"):
            _wire(materialized, already_wired={"triage", "billing"})
        routes = engine.delegation.set_routes.call_args[0][0]
        assert routes["billing"].kind == "slim"
    finally:
        materialized.stack_handles.close()


def test_a_process_with_no_sender_serves_its_agents_and_dials_no_one(tmp_path: Path, fake_node) -> None:
    del fake_node
    resolved = _bind({"agent_comm": ["desk-node"], "usage": ["connect", "listen"]}, agents=["billing"])
    materialized = _materialized(["billing"], tmp_path, resolved, [{"id": "billing"}])
    try:
        _wire(materialized)
        handle = next(iter(materialized.stack_handles.values()))
        assert set(handle._links) == {"billing"}
    finally:
        materialized.stack_handles.close()


def test_an_unreachable_node_is_a_chain_start_error_and_closes_the_chains(
    tmp_path: Path, fake_node, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mas.ctl.executor.stacks import ChainStartError

    async def unreachable(self, config: object) -> int:
        raise RuntimeError("node unreachable")

    monkeypatch.setattr(slim_fake.FakeService, "connect_async", unreachable)
    resolved = _bind({"agent_comm": ["desk-node"]}, agents=["triage", "billing"])
    materialized = _materialized(["triage", "billing"], tmp_path, resolved)
    with pytest.raises(ChainStartError, match="bind.agent_comm 'desk-node': agent '.*': .*node unreachable") as err:
        _wire(materialized)
    assert materialized.stack_handles == {}
    assert _SECRET not in str(err.value)


def test_a_missing_secret_names_the_row_and_nothing_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLIM_SECRET", raising=False)
    resolved = _bind({"agent_comm": ["desk-node"], "usage": ["connect", "listen"]}, agents=["triage"])
    materialized = _materialized(["triage"], tmp_path, resolved, [{"id": "triage"}])
    with pytest.raises(RuntimeError, match="Application 'desk-node': missing secret in environment variable"):
        _wire(materialized)
    assert materialized.stack_handles == {}


def test_the_session_chains_close_once_and_closing_none_is_a_no_op(tmp_path: Path) -> None:
    handle = MagicMock()
    owner = MaterializedMas(
        compose=_materialized([], tmp_path, ResolvedInfra()).compose,
        materialized=SimpleNamespace(),
        mas_base_dir=tmp_path,
    )
    owner.stack_handles.add("desk-node", handle)  # a frozen MaterializedMas still holds its chains
    owner.stack_handles.close()
    owner.stack_handles.close()
    handle.close.assert_called_once()
    assert owner.stack_handles == {}
    ChainHandles().close()  # a session that never opened a chain


def test_closing_the_session_chains_closes_every_one_when_one_fails(caplog: pytest.LogCaptureFixture) -> None:
    broken, fine = MagicMock(), MagicMock()
    broken.close.side_effect = RuntimeError("node is gone")
    handles = ChainHandles()
    handles.add("a", broken)
    handles.add("b", fine)
    handles.close()  # cleanup never raises over the error that ended the session
    fine.close.assert_called_once()
    assert handles == {}
    assert any("failed to close stack a" in rec.getMessage() for rec in caplog.records)


# --- per-agent chains -------------------------------------------------------------------------------------------

_PER_AGENT = {
    "agent_comm": ["desk-node"],
    "usage": ["connect", "listen"],
    "agents": {"billing": {"agent_comm": ["desk-node-a2a", "desk-a2a"]}},
}


def test_a_sender_joins_its_targets_chain_and_each_chain_gets_one_handle(tmp_path: Path, fake_node) -> None:
    resolved = _bind(_PER_AGENT)
    nodes = [{"id": "triage", "delegates_to": ["billing", "outage"]}, {"id": "billing"}, {"id": "outage"}]
    materialized = _materialized(_DESK, tmp_path, resolved, nodes)
    try:
        caller = next(item for item in _wire(materialized) if item["entry_agent_id"] == "triage")
        handles = materialized.stack_handles
        assert set(handles) == {"desk-node", "desk-node-a2a+desk-a2a"}
        assert set(handles["desk-node"]._links) == {"triage", "outage"}
        assert set(handles["desk-node-a2a+desk-a2a"]._links) == {"triage", "billing"}
        assert caller["comm"] is handles["desk-node"]._links["triage"]
        billing_route = caller["routes"]["billing"].handler
        assert billing_route is handles["desk-node-a2a+desk-a2a"]._links["triage"]
        assert billing_route._layers[0].link.name == "acme/lab/desk-a2a/triage"
        tool = SendTool(comm=caller["comm"], routes=caller["routes"], allowed_peers=["billing", "outage"])
        assert tool.send("billing", "refund") == "ok:billing:refund"
        assert tool.send("outage", "down?") == "ok:outage:down?"
        frames = [frame.decode("utf-8", "replace") for frame in fake_node.frames]
        assert any("SendMessage" in frame and "refund" in frame for frame in frames)  # billing's chain frames A2A
        assert any("down?" in frame and "SendMessage" not in frame for frame in frames)  # outage's does not
    finally:
        materialized.stack_handles.close()


# --- the commands -----------------------------------------------------------------------------------------------


def _desk_files(tmp_path: Path, *, bind: dict | None = None, two: bool = False) -> tuple[Path, Path, Path]:
    for agent, role in (("triage", "front desk"), ("billing", "refunds")):
        (tmp_path / f"{agent}.yaml").write_text(
            f"apiVersion: mas/v1\nkind: Agent\nmetadata: {{name: {agent}}}\nspec:\n  description: {role}\n",
            encoding="utf-8",
        )
    agents = [{"id": "triage", "ref": "triage.yaml"}]
    nodes: list[dict] = [{"id": "triage"}]
    if two:
        agents.append({"id": "billing", "ref": "billing.yaml"})
        nodes = [{"id": "triage", "delegates_to": ["billing"]}, {"id": "billing"}]
    mas = _write(
        tmp_path / "mas.yaml",
        {
            "apiVersion": "mas/v1",
            "kind": "MAS",
            "metadata": {"name": "desk"},
            "spec": {"agency": {"agents": agents}, "workflow": {"entry": "triage", "nodes": nodes}},
        },
    )
    rows = _write(
        tmp_path / "rows.yaml",
        {"apiVersion": "infra/v1", "kind": "Application", "metadata": {"name": "desk"}, "spec": {"endpoints": _ROWS}},
    )
    flavour = _flavour(tmp_path, {"bind": bind or {"agent_comm": ["desk-group"], "usage": ["connect", "listen"]}})
    return mas, rows, flavour


def _simulated():
    from mas.ctl.session.engine_factory import EngineSelection
    from mas.runtime.engine.simulated import SimulatedEngine

    return EngineSelection(
        engine=SimulatedEngine(llm_next_step=lambda _cid: "STOP", stop_text="hello"), mode="injected"
    )


def _real_materialize(result, **kwargs):
    from mas.ctl.executor.mas_session import materialize_mas_compose

    return materialize_mas_compose(result, **kwargs)


def test_run_mas_applies_the_flavour_bind_and_closes_the_chain(tmp_path: Path) -> None:
    from mas.ctl.executor.run_mas import execute_run_mas

    mas, rows, flavour = _desk_files(tmp_path)
    seen: list[ResolvedInfra] = []
    with patch.object(ChainHandles, "close", autospec=True) as close:
        with patch("mas.ctl.session.bootstrap.build_engine", return_value=_simulated()):
            with patch(
                "mas.ctl.executor.run_mas.materialize_mas_compose",
                side_effect=lambda result, **kw: seen.append(result.resolved_infra) or _real_materialize(result, **kw),
            ):
                rc = execute_run_mas(
                    mas,
                    prompt="hello",
                    validate=False,
                    infra_refs=["standard:openai", str(rows)],
                    flavour_spec=resolve_flavour(str(flavour)),
                )
    assert rc == 0
    assert [layer["type"] for layer in seen[0].agent_chains["triage"]] == ["local-group"]
    close.assert_called()


def test_run_mas_with_a_bad_bind_exits_one(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    from mas.ctl.executor.run_mas import execute_run_mas

    mas, rows, flavour = _desk_files(tmp_path, bind={"agent_comm": ["desk-group"], "agents": {"billng": {}}})
    flavour_spec = yaml.safe_load(flavour.read_text(encoding="utf-8"))["spec"]
    flavour_spec["bind"]["agents"] = {"billng": {"usage": "connect"}}
    caplog.set_level("ERROR")
    rc = execute_run_mas(
        mas, prompt="hello", validate=False, infra_refs=["standard:openai", str(rows)], flavour_spec=flavour_spec
    )
    assert rc == 1
    assert any("bind.agents names 'billng', which is no agent" in rec.message for rec in caplog.records)


def test_run_mas_with_a_chain_that_cannot_start_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from mas.ctl.executor.run_mas import execute_run_mas

    monkeypatch.delenv("SLIM_SECRET", raising=False)
    mas, rows, flavour = _desk_files(tmp_path, bind={"agent_comm": ["desk-node"], "usage": ["connect", "listen"]})
    caplog.set_level("ERROR")
    with patch("mas.ctl.session.bootstrap.build_engine", return_value=_simulated()):
        rc = execute_run_mas(
            mas,
            prompt="hello",
            validate=False,
            infra_refs=["standard:openai", str(rows)],
            flavour_spec=resolve_flavour(str(flavour)),
        )
    assert rc == 1
    assert any(
        "Application 'desk-node': missing secret in environment variable" in rec.message for rec in caplog.records
    )


def test_run_mas_closes_the_chain_when_wiring_the_peers_cannot_start(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from mas.ctl.executor.run_mas import execute_run_mas
    from mas.ctl.executor.stacks import ChainStartError

    mas, rows, flavour = _desk_files(tmp_path)
    caplog.set_level("ERROR")
    with patch.object(ChainHandles, "close", autospec=True) as close:
        with patch("mas.ctl.session.bootstrap.build_engine", return_value=_simulated()):
            with patch("mas.ctl.executor.run_mas.wire_peer_delegation", side_effect=ChainStartError("node is down")):
                rc = execute_run_mas(
                    mas,
                    prompt="hello",
                    validate=False,
                    infra_refs=["standard:openai", str(rows)],
                    flavour_spec=resolve_flavour(str(flavour)),
                )
    assert rc == 1
    assert any("node is down" in rec.message for rec in caplog.records)
    close.assert_called_once()


def test_run_mas_closes_the_chain_when_the_setup_after_it_fails(tmp_path: Path) -> None:
    from mas.ctl.executor.run_mas import execute_run_mas

    mas, rows, flavour = _desk_files(tmp_path)
    with patch.object(ChainHandles, "close", autospec=True) as close:
        with patch("mas.ctl.session.bootstrap.build_engine", return_value=_simulated()):
            with patch("mas.ctl.session.observability.setup_run_observability", side_effect=RuntimeError("no sink")):
                with pytest.raises(RuntimeError, match="no sink"):
                    execute_run_mas(
                        mas,
                        prompt="hello",
                        validate=False,
                        infra_refs=["standard:openai", str(rows)],
                        flavour_spec=resolve_flavour(str(flavour)),
                    )
    close.assert_called_once()


def _agent_file(tmp_path: Path, name: str = "billing") -> Path:
    agent = tmp_path / f"{name}.yaml"
    agent.write_text(
        f"apiVersion: mas/v1\nkind: Agent\nmetadata: {{name: {name}}}\nspec:\n  description: d\n"
        "  context: {role: r}\n  models:\n    - model: gpt-4o-mini\n",
        encoding="utf-8",
    )
    return agent


def _rows_file(tmp_path: Path, **extra: dict) -> Path:
    endpoints = {**_ROWS, **extra}
    return _write(
        tmp_path / "rows.yaml",
        {"apiVersion": "infra/v1", "kind": "Application", "metadata": {"name": "r"}, "spec": {"endpoints": endpoints}},
    )


def test_serve_refuses_an_agent_that_rides_a_chain(tmp_path: Path) -> None:
    from click.testing import CliRunner
    from mas.ctl.cli.commands.serve import serve_agent_cmd

    agent, rows = _agent_file(tmp_path), _rows_file(tmp_path)
    flavour = _flavour(tmp_path, {"bind": {"agent_comm": ["desk-node"]}})
    result = CliRunner().invoke(serve_agent_cmd, [str(agent), "--infra-ref", str(rows), "--flavour", str(flavour)])
    assert result.exit_code != 0
    assert "Agent 'billing' rides the Flavour chain 'desk-node'" in result.output
    assert "Traceback" not in result.output


def test_serve_hosts_an_agent_the_flavour_links_to_an_a2a_server(tmp_path: Path) -> None:
    from click.testing import CliRunner
    from mas.ctl.cli.commands.serve import serve_agent_cmd

    agent, rows = _agent_file(tmp_path, "outage"), _rows_file(tmp_path)
    flavour = _flavour(tmp_path, {"bind": {"agents": {"outage": {"agent_comm": ["outage-server"], "usage": "listen"}}}})
    with patch("mas.ctl.cli.commands.serve.instantiate_runtime", side_effect=SystemExit(7)) as inst:
        result = CliRunner().invoke(serve_agent_cmd, [str(agent), "--infra-ref", str(rows), "--flavour", str(flavour)])
    assert result.exit_code == 7, result.output  # got past the endpoint check to instantiation
    resolved = inst.call_args[0][0].resolved_infra
    assert resolved.applications["outage"] == {"protocol": "a2a", "url": "http://127.0.0.1:9003", "usage": ["listen"]}


def test_flavour_list_still_works_and_warns_on_stderr() -> None:
    from click.testing import CliRunner
    from mas.ctl.cli.commands.workspace import FLAVOUR_LIST_WARNING
    from mas.ctl.cli.main import app

    result = CliRunner().invoke(app, ["flavour", "list"])
    assert result.exit_code == 0, result.output
    assert "standard:local" in result.stdout and FLAVOUR_LIST_WARNING not in result.stdout
    assert result.stderr.strip() == FLAVOUR_LIST_WARNING
    assert "deprecated" in FLAVOUR_LIST_WARNING and "--flavour PATH" in FLAVOUR_LIST_WARNING
    assert "bundled names (local, prod)" in FLAVOUR_LIST_WARNING


# --- every entry point honours bind ------------------------------------------------------------------------------


def _solo(tmp_path: Path, *, bind: dict | None = None) -> tuple[Path, Path, Path]:
    agent = tmp_path / "solo.yaml"
    agent.write_text(
        "apiVersion: mas/v1\nkind: Agent\nmetadata: {name: solo}\nspec:\n  description: d\n"
        "  models:\n    - model: gpt-4o-mini\n",
        encoding="utf-8",
    )
    flavour = _flavour(tmp_path, {"bind": bind or {"agent_comm": ["desk-group"], "usage": ["connect", "listen"]}})
    return agent, _rows_file(tmp_path), flavour


class _Spy:
    """Records what ``open_single_agent_chains`` linked, then lets the real function run."""

    def __init__(self) -> None:
        from mas.ctl.executor import mas_session

        self.real = mas_session.open_single_agent_chains
        self.opened: list = []
        self.links: list[set[str]] = []

    def __call__(self, *args, **kwargs):
        chains = self.real(*args, **kwargs)
        self.opened.append(chains)
        self.links.append({name for handle in chains.values() for name in handle._links})
        return chains


def test_chat_serves_its_agent_on_the_flavour_chain_and_closes_it(tmp_path: Path) -> None:
    from click.testing import CliRunner
    from mas.ctl.cli.commands.chat import chat_cmd

    agent, rows, flavour = _solo(tmp_path)
    spy = _Spy()
    turn = MagicMock(
        text="ok", awaiting_hitl=False, responses=[], trace=MagicMock(boundary_errors=[], client_responses=[])
    )
    with patch("mas.ctl.cli.chains.open_single_agent_chains", side_effect=spy):
        with patch("mas.ctl.cli.commands.chat.instantiate_runtime", return_value=(MagicMock(kernel=None), None)):
            with patch("mas.ctl.cli.commands.chat.SessionController") as controller:
                controller.return_value.run_turn.return_value = turn
                with patch("mas.ctl.cli.commands.chat.setup_observability", return_value=None):
                    result = CliRunner().invoke(
                        chat_cmd,
                        [str(agent), "--prompt", "hi", "--no-validate", "-I", "--no-control"]
                        + ["--infra-ref", str(rows), "--flavour", str(flavour)],
                    )
    assert result.exit_code == 0, result.output
    assert spy.links == [{"solo"}]
    assert spy.opened[0] == {}  # closed when the session ended
    # the user's turns take the served agent's turn lock, the one its chain requests take
    assert controller.return_value.turn_guard is spy.opened[0].turn_lock("solo")


def test_chat_closes_the_chain_when_the_exposures_cannot_start(tmp_path: Path) -> None:
    from click.testing import CliRunner
    from mas.ctl.cli.commands.chat import chat_cmd

    agent, rows, flavour = _solo(tmp_path)
    spy = _Spy()
    with patch("mas.ctl.cli.chains.open_single_agent_chains", side_effect=spy):
        with patch("mas.ctl.session.exposure.start_hosted_exposures", side_effect=RuntimeError("port in use")):
            with patch("mas.ctl.cli.commands.chat.instantiate_runtime", return_value=(MagicMock(kernel=None), None)):
                with patch("mas.ctl.cli.commands.chat.SessionController"):
                    with patch("mas.ctl.cli.commands.chat.setup_observability", return_value=None):
                        result = CliRunner().invoke(
                            chat_cmd,
                            [str(agent), "--prompt", "hi", "--no-validate", "-I", "--no-control"]
                            + ["--infra-ref", str(rows), "--flavour", str(flavour)],
                        )
    assert isinstance(result.exception, RuntimeError)
    assert spy.links == [{"solo"}]
    assert spy.opened[0] == {}


def test_tui_closes_the_chain_when_the_exposures_cannot_start(tmp_path: Path) -> None:
    from click.testing import CliRunner
    from mas.ctl.cli.commands.tui import tui_cmd

    agent, rows, flavour = _solo(tmp_path)
    spy = _Spy()
    with patch("mas.ctl.cli.chains.open_single_agent_chains", side_effect=spy):
        with patch("mas.ctl.session.exposure.start_hosted_exposures", side_effect=RuntimeError("port in use")):
            with patch("mas.ctl.cli.commands.tui.instantiate_runtime", return_value=(MagicMock(), None)):
                with patch("mas.ctl.cli.commands.tui.setup_observability", return_value=None):
                    with patch("mas.ctl.cli.commands.tui.build_curses_controller", return_value=MagicMock()):
                        result = CliRunner().invoke(
                            tui_cmd, [str(agent), "--infra-ref", str(rows), "--flavour", str(flavour)]
                        )
    assert isinstance(result.exception, RuntimeError)
    assert spy.links == [{"solo"}]
    assert spy.opened[0] == {}


def test_chat_with_a_connect_only_usage_serves_nothing(tmp_path: Path) -> None:
    from click.testing import CliRunner
    from mas.ctl.cli.commands.chat import chat_cmd

    agent, rows, flavour = _solo(
        tmp_path, bind={"agent_comm": ["desk-group"], "agents": {"solo": {"usage": "connect"}}}
    )
    spy = _Spy()
    turn = MagicMock(
        text="ok", awaiting_hitl=False, responses=[], trace=MagicMock(boundary_errors=[], client_responses=[])
    )
    with patch("mas.ctl.cli.chains.open_single_agent_chains", side_effect=spy):
        with patch("mas.ctl.cli.commands.chat.instantiate_runtime", return_value=(MagicMock(kernel=None), None)):
            with patch("mas.ctl.cli.commands.chat.SessionController") as controller:
                controller.return_value.run_turn.return_value = turn
                with patch("mas.ctl.cli.commands.chat.setup_observability", return_value=None):
                    result = CliRunner().invoke(
                        chat_cmd,
                        [str(agent), "--prompt", "hi", "--no-validate", "-I", "--no-control"]
                        + ["--infra-ref", str(rows), "--flavour", str(flavour)],
                    )
    assert result.exit_code == 0, result.output
    assert spy.links == [set()]


def test_chat_refuses_a_bad_bind_in_one_line(tmp_path: Path) -> None:
    from click.testing import CliRunner
    from mas.ctl.cli.commands.chat import chat_cmd

    agent, rows, flavour = _solo(tmp_path, bind={"agent_comm": ["nowhere"]})
    result = CliRunner().invoke(
        chat_cmd,
        [str(agent), "--prompt", "hi", "--no-validate", "-I", "--no-control", "--infra-ref", str(rows)]
        + ["--flavour", str(flavour)],
    )
    assert result.exit_code == 1
    assert "names 'nowhere', which is no Application row" in result.output
    assert "Traceback" not in result.output


def test_tui_serves_its_agent_on_the_flavour_chain_and_closes_it(tmp_path: Path) -> None:
    from click.testing import CliRunner
    from mas.ctl.cli.commands.tui import tui_cmd

    agent, rows, flavour = _solo(tmp_path)
    spy = _Spy()
    with patch("mas.ctl.cli.chains.open_single_agent_chains", side_effect=spy):
        with patch("mas.ctl.cli.commands.tui.instantiate_runtime", return_value=(MagicMock(), None)):
            with patch("mas.ctl.cli.commands.tui.setup_observability", return_value=None):
                with patch("mas.ctl.cli.commands.tui.build_curses_controller", return_value=MagicMock()):
                    with patch("mas.ctl.cli.commands.tui.run_curses_session") as run:
                        result = CliRunner().invoke(
                            tui_cmd, [str(agent), "--infra-ref", str(rows), "--flavour", str(flavour)]
                        )
    assert result.exit_code == 0, result.output
    run.assert_called_once()
    assert spy.links == [{"solo"}]
    assert spy.opened[0] == {}


def test_tui_refuses_a_bad_bind_in_one_line(tmp_path: Path) -> None:
    from click.testing import CliRunner
    from mas.ctl.cli.commands.tui import tui_cmd

    agent, rows, flavour = _solo(tmp_path, bind={"agent_comm": ["nowhere"]})
    result = CliRunner().invoke(tui_cmd, [str(agent), "--infra-ref", str(rows), "--flavour", str(flavour)])
    assert result.exit_code == 1
    assert "names 'nowhere', which is no Application row" in result.output


def test_a_one_agent_benchmark_run_serves_its_agent_on_the_chain(tmp_path: Path) -> None:
    from mas.ctl.benchmark.runner import MasBenchRunner
    from mas.lab.runners.protocol import RunResult

    agent, rows, flavour = _solo(tmp_path)
    spy = _Spy()
    with patch("mas.ctl.benchmark.runner.open_single_agent_chains", side_effect=spy):
        with patch("mas.ctl.benchmark.runner.instantiate_runtime", return_value=(MagicMock(), None)):
            with patch.object(MasBenchRunner, "_run_controller_turns", return_value=RunResult(content="ok")) as turns:
                result = MasBenchRunner().run(
                    "hello",
                    config=yaml.safe_load(agent.read_text(encoding="utf-8")),
                    spec_path=agent,
                    output_dir=tmp_path / "out",
                    infra_refs=[str(rows)],
                    flavour=yaml.safe_load(flavour.read_text(encoding="utf-8")),
                )
    assert result.status == "ok", result.error
    turns.assert_called_once()
    assert spy.links == [{"solo"}]
    assert spy.opened[0] == {}


def test_a_benchmark_run_with_a_bad_bind_fails_with_the_reason(tmp_path: Path) -> None:
    from mas.ctl.benchmark.runner import MasBenchRunner

    agent, rows, flavour = _solo(tmp_path, bind={"agent_comm": ["nowhere"]})
    result = MasBenchRunner().run(
        "hello",
        config=yaml.safe_load(agent.read_text(encoding="utf-8")),
        spec_path=agent,
        output_dir=tmp_path / "out",
        infra_refs=[str(rows)],
        flavour=yaml.safe_load(flavour.read_text(encoding="utf-8")),
    )
    assert result.status == "error"
    assert "names 'nowhere', which is no Application row" in result.error


def test_a_benchmark_run_whose_chain_cannot_start_fails_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mas.ctl.benchmark.runner import MasBenchRunner

    monkeypatch.delenv("SLIM_SECRET", raising=False)
    agent, rows, _flavour_path = _solo(tmp_path)
    with patch("mas.ctl.benchmark.runner.instantiate_runtime", return_value=(MagicMock(), None)):
        result = MasBenchRunner().run(
            "hello",
            config=yaml.safe_load(agent.read_text(encoding="utf-8")),
            spec_path=agent,
            output_dir=tmp_path / "out",
            infra_refs=[str(rows)],
            flavour={"spec": {"bind": {"agent_comm": ["desk-node"], "usage": ["connect", "listen"]}}},
        )
    assert result.status == "error"
    assert "Application 'desk-node': missing secret in environment variable" in result.error


def test_a_mas_benchmark_run_links_its_agents_and_closes_the_chain(tmp_path: Path) -> None:
    from mas.ctl.benchmark.runner import MasBenchRunner
    from mas.ctl.executor import mas_session
    from mas.lab.runners.protocol import RunResult

    mas, rows, flavour = _desk_files(tmp_path, two=True)
    owners: list = []
    seen: list = []

    def materialize(result, **kw):
        owner = mas_session.materialize_mas_compose(result, **kw)
        owners.append(owner)
        return owner

    def run_turns(self, **kwargs):
        del self, kwargs
        seen.append({name: set(handle._links) for name, handle in owners[-1].stack_handles.items()})
        return RunResult(content="ok")

    with patch("mas.ctl.benchmark.runner.materialize_mas_compose", side_effect=materialize):
        with patch("mas.ctl.session.bootstrap.build_engine", return_value=_simulated()):
            with patch.object(MasBenchRunner, "_run_controller_turns", run_turns):
                result = MasBenchRunner().run(
                    "hello",
                    config=yaml.safe_load(mas.read_text(encoding="utf-8")),
                    spec_path=mas,
                    output_dir=tmp_path / "out",
                    infra_refs=["standard:openai", str(rows)],
                    flavour=yaml.safe_load(flavour.read_text(encoding="utf-8")),
                )
    assert result.status == "ok", result.error
    assert seen == [{"desk-group": {"triage", "billing"}}]
    assert owners[-1].stack_handles == {}


def test_a_mas_benchmark_run_closes_the_chain_when_the_setup_after_it_fails(tmp_path: Path) -> None:
    from mas.ctl.benchmark.runner import MasBenchRunner
    from mas.ctl.executor import mas_session

    mas, rows, flavour = _desk_files(tmp_path, two=True)
    owners: list = []

    def materialize(result, **kw):
        owner = mas_session.materialize_mas_compose(result, **kw)
        owners.append(owner)
        return owner

    with patch("mas.ctl.benchmark.runner.materialize_mas_compose", side_effect=materialize):
        with patch("mas.ctl.session.bootstrap.build_engine", return_value=_simulated()):
            with patch("mas.ctl.session.observability.setup_run_observability", side_effect=RuntimeError("no sink")):
                with pytest.raises(RuntimeError, match="no sink"):
                    MasBenchRunner().run(
                        "hello",
                        config=yaml.safe_load(mas.read_text(encoding="utf-8")),
                        spec_path=mas,
                        output_dir=tmp_path / "out",
                        infra_refs=["standard:openai", str(rows)],
                        flavour=yaml.safe_load(flavour.read_text(encoding="utf-8")),
                    )
    assert owners and owners[-1].stack_handles == {}


@pytest.mark.parametrize("command", ["chat", "tui"])
def test_a_chain_that_cannot_start_is_one_error_line_and_exit_one(tmp_path: Path, command: str) -> None:
    from click.testing import CliRunner
    from mas.ctl.cli.commands.chat import chat_cmd
    from mas.ctl.cli.commands.tui import tui_cmd
    from mas.ctl.executor.stacks import ChainStartError

    agent, rows, flavour = _solo(tmp_path)
    args = [str(agent), "--infra-ref", str(rows), "--flavour", str(flavour)]
    if command == "chat":
        args += ["--prompt", "hi", "--no-validate", "-I", "--no-control"]
    with patch("mas.ctl.cli.chains.open_single_agent_chains", side_effect=ChainStartError("node is down")):
        with patch(f"mas.ctl.cli.commands.{command}.instantiate_runtime", return_value=(MagicMock(kernel=None), None)):
            with patch(f"mas.ctl.cli.commands.{command}.setup_observability", return_value=None):
                with patch("mas.ctl.cli.commands.chat.SessionController"):
                    with patch("mas.ctl.cli.commands.tui.build_curses_controller", return_value=MagicMock()):
                        result = CliRunner().invoke(chat_cmd if command == "chat" else tui_cmd, args)
    assert result.exit_code == 1
    assert "error: node is down" in result.output
    assert "Traceback" not in result.output


# --- the steps of bind_flavour, one by one ----------------------------------------------------------------------


def test_planned_chains_names_each_chain_and_marks_direct_links() -> None:
    from mas.ctl.infra.bind import _planned_chains

    wheres = {"bind.agent_comm": ["desk-node", "desk-a2a"], "bind.agents.outage.agent_comm": ["outage-server"]}
    raw, direct = _planned_chains(wheres, _rows(), known=_DESK)
    assert [layer["row"] for layer in raw["bind.agent_comm"]] == ["desk-node", "desk-a2a"]
    assert direct == {"bind.agents.outage.agent_comm"}


@pytest.mark.parametrize(
    ("wheres", "message"),
    [
        ({"bind.agent_comm": ["triage"]}, "also an agent of this session"),
        ({"bind.agent_comm": ["nowhere"]}, "which is no Application row"),
        ({"bind.agent_comm": ["desk-a2a"]}, "must be a transport"),
        ({"bind.agent_comm": ["outage-server"]}, "link to one agent's own url"),
        ({"bind.agent_comm": ["agent-hub"]}, "usage listen: the http transport only connects"),
    ],
)
def test_planned_chains_refuses(wheres: dict, message: str) -> None:
    from mas.ctl.infra.bind import _planned_chains

    rows = _rows(triage={"protocol": "a2a", "url": "http://127.0.0.1:1"}, **{"agent-hub": {**_ROWS["agent-hub"]}})
    rows["agent-hub"]["usage"] = _BOTH
    with pytest.raises(BindError, match=message):
        _planned_chains(wheres, rows, known=_DESK)


@pytest.mark.parametrize(
    ("row", "entries", "message"),
    [
        ({"url": "http://127.0.0.1:1"}, {}, "names no protocol"),
        ({"protocol": "a2a"}, {}, "needs url"),
        ({"protocol": "a2a", "url": "http://127.0.0.1:1"}, {"outage": {}}, "cannot also link it"),
    ],
)
def test_an_agent_keyed_row_is_checked(row: dict, entries: dict, message: str) -> None:
    from mas.ctl.infra.bind import _check_agent_keyed_row

    with pytest.raises(BindError, match=message):
        _check_agent_keyed_row("outage", row, entries)
    _check_agent_keyed_row("outage", {"protocol": "a2a", "url": "http://127.0.0.1:1"}, {})


def test_an_agent_on_a_chain_is_checked_for_its_id_and_its_usage() -> None:
    from mas.ctl.infra.bind import _check_agent_on_chain, _for_agent, _layer

    chain = _for_agent([_layer("desk-node", _rows(), "bind.agent_comm")], "triage", {}, "bind.agents.triage")
    _check_agent_on_chain("triage", chain, ["connect", "listen"])
    with pytest.raises(BindError, match="cannot ride row 'desk-node'"):
        _check_agent_on_chain("tri.age", chain, ["connect"])
    chain[0]["usage"] = ["connect"]
    with pytest.raises(BindError, match="usage listen is not a capability of row 'desk-node'"):
        _check_agent_on_chain("triage", chain, ["connect", "listen"])


def test_two_chains_on_one_bottom_row_are_refused() -> None:
    from mas.ctl.infra.bind import _refuse_shared_bottoms

    bottom = {"type": "slim", "params": {"identity": "a/b/c"}, "row": "node"}
    _refuse_shared_bottoms({"x": [{**bottom, "chain": "node"}], "y": [{**bottom, "chain": "node"}]})  # one chain
    with pytest.raises(BindError, match="also the bottom of another chain"):
        _refuse_shared_bottoms({"x": [{**bottom, "chain": "node+a"}], "y": [{**bottom, "chain": "node+b"}]})


@pytest.mark.parametrize("command", ["chat", "tui", "serve"])
def test_a_relative_flavour_path_names_the_file_from_the_shell_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """These commands change into the manifest's directory; the Flavour path is still read from where it was typed."""
    from click.testing import CliRunner
    from mas.ctl.cli.commands.chat import chat_cmd
    from mas.ctl.cli.commands.serve import serve_agent_cmd
    from mas.ctl.cli.commands.tui import tui_cmd

    (tmp_path / "agents").mkdir()
    agent = _agent_file(tmp_path / "agents", "solo")
    rows = _rows_file(tmp_path)
    _flavour(tmp_path, {"bind": {"agent_comm": ["nowhere"]}})
    monkeypatch.chdir(tmp_path)
    cmd = {"chat": chat_cmd, "tui": tui_cmd, "serve": serve_agent_cmd}[command]
    extra = ["--prompt", "hi", "--no-validate", "-I", "--no-control"] if command == "chat" else []
    result = CliRunner().invoke(cmd, [str(agent), *extra, "--infra-ref", str(rows), "--flavour", "desk.yaml"])
    assert result.exit_code != 0
    assert "names 'nowhere', which is no Application row" in result.output  # the file was found and read


def test_a_flavour_overlay_can_patch_bind(tmp_path: Path) -> None:
    from mas.ctl.validate import validate_data

    overlay = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "front-desk"},
        "spec": {"target": {"kind": "Flavour"}, "patch": {"bind": {"agents": {"billing": {"usage": "connect"}}}}},
    }
    assert validate_data(overlay, kind="overlay").ok
    overlay["spec"]["patch"]["bind"]["agents"]["billing"]["usage"] = "serve"
    assert not validate_data(overlay, kind="overlay").ok


# --- one turn at a time per served agent ------------------------------------------------------------------------


def test_a_served_agent_answers_one_request_at_a_time_and_waits_for_the_users_turn(fake_node) -> None:
    import threading
    import time

    from mas.ctl.executor.stacks import ChainHandles, chain_links
    from mas.ctl.session.controller import SessionController

    resolved = _bind({"agent_comm": ["desk-node"], "usage": ["connect", "listen"]}, agents=["a", "b", "c"])
    running: list[str] = []
    peak = [0]
    guard = threading.Lock()

    def run_turn(agent_id: str, task: str, *_args: object) -> str:
        with guard:
            running.append(agent_id)
            peak[0] = max(peak[0], running.count("b"))
        time.sleep(0.3)
        with guard:
            running.remove(agent_id)
        return f"{agent_id}:{task}"

    handles = ChainHandles()
    try:
        _handlers, routes = chain_links(
            None,
            resolved,
            run_turn=run_turn,
            local_ids=["a", "b", "c"],
            handles=handles,
            peers={"a": {"b"}, "c": {"b"}},
        )
        replies: list[str] = []
        callers = [
            threading.Thread(target=lambda caller=caller: replies.append(routes[caller]["b"].handler.send("b", caller)))
            for caller in ("a", "c")
        ]
        for thread in callers:
            thread.start()
        for thread in callers:
            thread.join(timeout=5)
        assert sorted(replies) == ["b:a", "b:c"]
        assert peak[0] == 1

        # the user's own turn on b holds the same lock: a request over the chain waits for it to end
        controller = SessionController(instance=MagicMock(), display=MagicMock())
        controller.turn_guard = handles.turn_lock("b")
        order: list[str] = []
        with controller.turn_guard:
            sender = threading.Thread(target=lambda: order.append(routes["a"]["b"].handler.send("b", "late")))
            sender.start()
            time.sleep(0.2)
            order.append("user turn ended")
        sender.join(timeout=5)
        assert order == ["user turn ended", "b:late"]
    finally:
        handles.close()


def test_a_request_that_waits_too_long_for_a_busy_agent_fails() -> None:
    import threading

    from mas.ctl.executor.stacks import ChainHandles
    from mas.runtime.boundary.agentcomm.protocol import AgentCommError

    handles = ChainHandles(turn_wait_s=0.1)
    turn = handles.serialized(lambda agent_id, task, *_: f"{agent_id}:{task}")
    lock = handles.turn_lock("b")
    lock.acquire()
    try:
        raised: list[BaseException] = []

        def call() -> None:
            try:
                turn("b", "late", 0, "", "")
            except BaseException as exc:
                raised.append(exc)

        waiter = threading.Thread(target=call)
        waiter.start()
        waiter.join(timeout=5)
        assert waiter.is_alive() is False
        assert raised and "did not become free" in str(raised[0])
        assert isinstance(raised[0], AgentCommError)
    finally:
        lock.release()


def test_a_connect_only_link_answers_no_request(fake_node) -> None:
    from mas.ctl.executor.stacks import ChainHandles, chain_links
    from mas.runtime.boundary.agentcomm.protocol import AgentCommError

    resolved = _bind(
        {"agent_comm": ["desk-node"], "usage": ["connect", "listen"], "agents": {"b": {"usage": "connect"}}},
        agents=["a", "b"],
    )
    handles = ChainHandles()
    try:
        _handlers, routes = chain_links(
            None,
            resolved,
            run_turn=lambda agent_id, task, *_: f"{agent_id}:{task}",
            local_ids=["a", "b"],
            handles=handles,
            peers={"a": {"b"}, "b": {"a"}},
        )
        assert routes["b"]["a"].handler.send("a", "hi") == "a:hi"  # a is served here
        with pytest.raises(AgentCommError, match="the agent is not served on this link"):
            routes["a"]["b"].handler.send("b", "hi")  # b only sends on this chain: nobody here runs its turn
    finally:
        handles.close()


def test_a_user_turn_holds_the_turn_guard_while_it_runs() -> None:
    import threading

    from mas.ctl.session.controller import SessionController

    guard = threading.RLock()
    controller = SessionController(instance=MagicMock(), display=MagicMock(), turn_guard=guard)
    held: list[bool] = []

    def turn(*_args: object, **_kwargs: object) -> str:
        probe = threading.Thread(target=lambda: held.append(not guard.acquire(timeout=0.05)))
        probe.start()
        probe.join()
        return "ok"

    controller._run_turn_guarded = turn  # type: ignore[method-assign]
    assert controller.run_turn("hello") == "ok"
    assert held[0] is True
    assert guard.acquire(timeout=0.05)  # released after the turn
    guard.release()


def test_a_bundled_flavour_name_is_never_a_file_in_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mas.ctl.session.flavour import FlavourError, flavour_argument, resolve_flavour

    monkeypatch.chdir(tmp_path)
    (tmp_path / "local").write_text("not a flavour", encoding="utf-8")
    (tmp_path / "desk").write_text("not a flavour", encoding="utf-8")
    assert resolve_flavour("local") == resolve_flavour(None)
    assert flavour_argument("local") == "local"
    with pytest.raises(FlavourError, match="not supported yet"):
        resolve_flavour("desk")  # a bare word is a name, not the file next to it
    with pytest.raises(FlavourError, match="is not a kind: Flavour document"):
        resolve_flavour("./desk")  # a path separator makes it a path


@pytest.mark.parametrize(
    "url",
    ["https://user:pw@hub.example.test/a2a", "https://hub.example.test/a2a?api_key=abc", "https://h.test/?Token=1"],
)
def test_a_url_that_carries_a_secret_is_refused_and_the_secret_is_not_shown(url: str) -> None:
    rows = _rows(web={"protocol": "http", "url": url, "usage": "connect"})
    with pytest.raises(BindError, match="would hold a secret") as err:
        _bind({"agent_comm": ["web"], "agents": {a: {"usage": "connect"} for a in _DESK}}, rows=rows)
    assert "pw" not in str(err.value) and "abc" not in str(err.value)
    plain = _rows(web={"protocol": "http", "url": "https://hub.example.test/a2a?page=2", "usage": "connect"})
    _bind({"agent_comm": ["web"], "agents": {a: {"usage": "connect"} for a in _DESK}}, rows=plain)
