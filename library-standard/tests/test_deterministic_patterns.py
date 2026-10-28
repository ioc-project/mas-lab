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


def _participants(agent_spec: dict) -> list[str]:
    config = KernelConfig(agent_spec=agent_spec)
    return DeterministicParallelPlugin()._participants_from_spec(config)


def test_participants_follow_configured_entry_delegates() -> None:
    assert _participants(
        {
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
    ) == ["flights", "hotels", "verifier"]


def test_participant_sources_exclude_configured_entry() -> None:
    assert _participants(
        {
            "workflow": {
                "entry": "lead",
                "participants": ["lead", "analyst", "verifier"],
            }
        }
    ) == ["analyst", "verifier"]

    assert _participants(
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
    ) == ["analyst", "verifier"]

    assert _participants(
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
    ) == ["analyst", "verifier"]


def test_moderator_remains_default_entry() -> None:
    assert _participants(
        {
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
    ) == ["analyst", "verifier"]


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
