from types import SimpleNamespace

from mas.library.standard.plugins.design_patterns.linear import DeterministicLinearPlugin
from mas.library.standard.plugins.design_patterns.parallel import (
    DeterministicParallelPlugin,
)
from mas.library.standard.plugins.design_patterns.single import DeterministicSingleAgentPlugin
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.orchestrator import RuntimeKernel
from mas.runtime.kernel.state import DpState
from mas.runtime.schema.egress import RequestCtxAssembly
from mas.runtime.schema.ingress import OperatorSteerReceived


def _participants(agent_spec: dict, *, agent_id: str = "") -> list[str]:
    config = KernelConfig(agent_spec=agent_spec, agent_id=agent_id)
    return DeterministicParallelPlugin()._participants_from_spec(config)


def test_participants_follow_this_agents_delegates_to() -> None:
    spec = {
        "workflow": {
            "entry": "planner",
            "nodes": [
                {
                    "id": "planner",
                    "delegates_to": ["flights", "hotels", "verifier"],
                },
                {"id": "flights"},
                {"id": "hotels"},
                {"id": "verifier"},
            ],
        }
    }
    assert _participants(spec, agent_id="planner") == ["flights", "hotels", "verifier"]
    assert _participants(spec) == []  # without an agent id there is no node to read


def test_empty_delegates_to_is_nobody_not_participants_or_other_nodes() -> None:
    assert (
        _participants(
            {
                "workflow": {
                    "entry": "lead",
                    "participants": ["lead", "analyst", "verifier"],
                }
            }
        )
        == []
    )

    assert (
        _participants(
            {
                "workflow": {
                    "entry": "lead",
                    "nodes": [
                        {"id": "lead"},
                        {"id": "analyst"},
                        {"id": "verifier"},
                    ],
                }
            }
        )
        == []
    )

    assert (
        _participants(
            {
                "workflow": {"entry": "lead"},
                "agency": {
                    "agents": [
                        {"id": "lead"},
                        {"id": "analyst"},
                        {"id": "verifier"},
                    ]
                },
            }
        )
        == []
    )


def test_participants_ignore_a_non_dict_spec() -> None:
    assert DeterministicLinearPlugin()._participants_from_spec(SimpleNamespace(agent_spec="nope", agent_id="")) == []


def test_participants_follow_this_agents_node() -> None:
    spec = {
        "workflow": {
            "entry": "planner",
            "nodes": [
                {"id": "planner", "delegates_to": ["flights", "hotels"]},
                {"id": "flights", "delegates_to": ["hotels"]},
                {"id": "hotels"},
            ],
        }
    }
    flights = KernelConfig(agent_spec=spec, agent_id="flights")
    hotels = KernelConfig(agent_spec=spec, agent_id="hotels")
    assert DeterministicLinearPlugin()._participants_from_spec(flights) == ["hotels"]
    assert DeterministicLinearPlugin()._participants_from_spec(hotels) == []


def test_entry_and_pattern_share_an_empty_list() -> None:
    from mas.runtime.boundary.delegation.policy import delegation_targets

    nodes_only = {"workflow": {"entry": "mod", "nodes": [{"id": "mod"}, {"id": "a"}, {"id": "b"}]}}
    listed = {"workflow": {"entry": "mod", "nodes": [{"id": "mod"}], "participants": ["a", "b"]}}
    for spec in (nodes_only, listed):
        config = KernelConfig(agent_spec=spec, agent_id="mod")
        peers = DeterministicLinearPlugin()._participants_from_spec(config)
        assert peers == []
        assert delegation_targets({"spec": spec}, agent_id="mod") == peers


def test_a_helper_never_lists_itself_or_the_entry_agent() -> None:
    spec = {"workflow": {"entry": "mod", "nodes": [{"id": "mod"}, {"id": "a"}, {"id": "b"}]}}
    config = KernelConfig(agent_spec=spec, agent_id="solo")
    assert DeterministicLinearPlugin()._participants_from_spec(config) == []
    config = KernelConfig(agent_spec=spec, agent_id="a")
    assert DeterministicLinearPlugin()._participants_from_spec(config) == []


def test_workflow_entry_does_not_change_this_agents_leftover_tools() -> None:
    spec = {
        "workflow": {
            "nodes": [
                {
                    "id": "moderator",
                    "delegates_to": ["analyst", "verifier"],
                },
                {"id": "analyst"},
                {"id": "verifier"},
            ]
        }
    }
    assert _participants(spec, agent_id="moderator") == ["analyst", "verifier"]
    assert _participants(spec, agent_id="analyst") == []
    assert _participants(spec) == []


def test_deterministic_patterns_apply_operator_steer() -> None:
    event = OperatorSteerReceived(steer_id="steer-1", context_text="go")
    for plugin in (
        DeterministicLinearPlugin(),
        DeterministicParallelPlugin(),
        DeterministicSingleAgentPlugin(),
    ):
        kernel = RuntimeKernel(plugin=plugin)
        out = kernel.transition(event)
        assert kernel.q.dp is DpState.CTX_BUILD, plugin.plugin_id
        assert isinstance(out.egress[-1], RequestCtxAssembly), plugin.plugin_id
        assert out.egress[-1].collect_id == "steer-1"
        assert plugin._state().original_task == "go"
        assert plugin.STEER_MODES == frozenset({"after"})


def _cfg(spec: dict, agent_id: str = "") -> KernelConfig:
    return KernelConfig(agent_spec=spec, agent_id=agent_id)


def test_a_kernel_without_an_agent_id_has_no_participants() -> None:
    spec = {"workflow": {"entry": "a", "nodes": [{"id": "a", "delegates_to": ["b"]}, {"id": "b"}]}}
    assert DeterministicLinearPlugin()._participants_from_spec(_cfg(spec)) == []


def test_delegation_style_none_does_not_empty_a_patterns_next_hop() -> None:
    spec = {
        "behavior": {"delegation_style": "none"},
        "workflow": {"entry": "a", "nodes": [{"id": "a", "delegates_to": ["b"]}, {"id": "b"}]},
    }
    assert DeterministicLinearPlugin()._participants_from_spec(_cfg(spec, "a")) == ["b"]


def test_a_node_is_found_by_its_id_or_its_agent() -> None:
    spec = {"workflow": {"entry": "n1", "nodes": [{"id": "n1", "agent": "alice", "delegates_to": ["bob"]}]}}
    plugin = DeterministicLinearPlugin()
    assert plugin._participants_from_spec(_cfg(spec, "n1")) == ["bob"]
    assert plugin._participants_from_spec(_cfg(spec, "alice")) == ["bob"]


def test_no_participants_warns(caplog) -> None:
    import logging

    from mas.runtime.kernel.state import QProduct

    with caplog.at_level(logging.WARNING):
        DeterministicLinearPlugin()._no_participants(QProduct())
    assert "no participants" in caplog.text
