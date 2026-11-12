#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
import pytest
from mas.ctl.manifest.spec_bindings import SpecBindingError, validate_agent_spec_bindings


def _spec(params):
    return {"system_plugins": [{"subagents": params}]}


def test_spawn_subagent_accepts_named_validated_templates():
    validate_agent_spec_bindings(
        _spec(
            {
                "templates": [{"id": "worker", "ref": "./agents/worker.yaml", "description": "Do work"}],
                "max_spawns": 4,
                "max_depth": 2,
            }
        )
    )


def test_bounds_default_when_omitted():
    validate_agent_spec_bindings(_spec({"templates": [{"id": "w", "ref": "w.yaml"}]}))


def test_declaring_the_tool_requires_at_least_one_template():
    with pytest.raises(SpecBindingError, match="at least one template"):
        validate_agent_spec_bindings(_spec({}))


def test_a_manifest_without_the_tool_entry_is_valid_and_cannot_spawn():
    """No capability flag exists: absence of the tools entry is the gate."""
    from mas.runtime.engine.tools import is_spawn_subagent_enabled

    spec = {"system_plugins": ["inform_user"]}
    validate_agent_spec_bindings(spec)
    assert is_spawn_subagent_enabled(spec) is False


def test_disabled_tool_entry_is_not_a_spawn_declaration():
    from mas.runtime.engine.tools import is_spawn_subagent_enabled

    spec = {"system_plugins": [{"subagents": {"enabled": False}}]}
    validate_agent_spec_bindings(spec)
    assert is_spawn_subagent_enabled(spec) is False


def test_enabled_spawn_tool_is_the_capability_gate():
    from mas.runtime.engine.tools import is_spawn_subagent_enabled

    spec = _spec({"templates": [{"id": "w", "ref": "w.yaml"}]})
    validate_agent_spec_bindings(spec)
    assert is_spawn_subagent_enabled(spec) is True


def test_create_subagent_is_the_same_capability_as_spawn() -> None:
    from mas.runtime.engine.tools import is_spawn_subagent_enabled, spawn_subagent_params

    spec = {"system_plugins": [{"create_subagent": {"templates": [{"id": "w", "ref": "w.yaml"}]}}]}
    validate_agent_spec_bindings(spec)
    assert is_spawn_subagent_enabled(spec) is True
    assert spawn_subagent_params(spec)["templates"][0]["id"] == "w"


@pytest.mark.parametrize(
    "params",
    [
        {"templates": [{"id": "w", "ref": "w.yaml"}], "unexpected": True},
        {"templates": [{"id": "w", "ref": "w.yaml"}, {"id": "w", "ref": "other.yaml"}]},
        {"templates": [{"id": "w", "ref": "w.yaml", "unexpected": True}]},
        {"templates": [{"id": "w", "ref": "w.yaml"}], "max_depth": True},
        {"templates": [{"id": "w", "ref": "w.yaml"}], "max_spawns": 0},
        {"templates": [{"id": "", "ref": "w.yaml"}]},
    ],
)
def test_malformed_params_are_rejected(params):
    with pytest.raises(SpecBindingError):
        validate_agent_spec_bindings(_spec(params))
