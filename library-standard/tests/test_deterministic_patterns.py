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
    spec = {"communication": {"peers": ["flights", "hotels", "verifier"]}}
    assert _participants(spec, agent_id="planner") == ["flights", "hotels", "verifier"]
    assert _participants(spec) == ["flights", "hotels", "verifier"]  # the spec is the agent's own


def test_empty_delegates_to_is_nobody_not_participants_or_other_nodes() -> None:
    assert _participants({"communication": {"peers": []}}) == []

    assert _participants({"communication": {"peers": []}}) == []

    assert (
        _participants(
            {
                "communication": {"peers": []},
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


def test_entry_and_pattern_share_an_empty_list() -> None:
    from mas.runtime.boundary.delegation.policy import delegation_targets

    nodes_only = {"communication": {"peers": []}}
    listed = {"communication": {"peers": []}}
    for spec in (nodes_only, listed):
        config = KernelConfig(agent_spec=spec, agent_id="mod")
        peers = DeterministicLinearPlugin()._participants_from_spec(config)
        assert peers == []
        assert delegation_targets({"spec": spec}, agent_id="mod") == peers


def test_a_helper_never_lists_itself_or_the_entry_agent() -> None:
    spec = {"communication": {"peers": []}}
    config = KernelConfig(agent_spec=spec, agent_id="solo")
    assert DeterministicLinearPlugin()._participants_from_spec(config) == []
    config = KernelConfig(agent_spec=spec, agent_id="a")
    assert DeterministicLinearPlugin()._participants_from_spec(config) == []


def test_an_agents_leftover_tools_are_its_own_peers() -> None:
    spec = {"communication": {"peers": ["analyst", "verifier"]}}
    assert _participants(spec, agent_id="moderator") == ["analyst", "verifier"]


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


def test_a_kernel_without_an_agent_id_still_reads_its_own_peers() -> None:
    spec = {"communication": {"peers": ["b"]}}
    assert DeterministicLinearPlugin()._participants_from_spec(_cfg(spec)) == ["b"]


def test_delegation_style_none_does_not_empty_a_patterns_next_hop() -> None:
    spec = {
        "behavior": {"delegation_style": "none"},
        "communication": {"peers": ["b"]},
    }
    assert DeterministicLinearPlugin()._participants_from_spec(_cfg(spec, "a")) == ["b"]


def test_a_node_is_found_by_its_id_or_its_agent() -> None:
    spec = {"communication": {"peers": ["bob"]}}
    plugin = DeterministicLinearPlugin()
    assert plugin._participants_from_spec(_cfg(spec, "n1")) == ["bob"]
    assert plugin._participants_from_spec(_cfg(spec, "alice")) == ["bob"]


def test_no_participants_warns(caplog) -> None:
    import logging

    from mas.runtime.kernel.state import QProduct

    with caplog.at_level(logging.WARNING):
        DeterministicLinearPlugin()._no_participants(QProduct())
    assert "no participants" in caplog.text
