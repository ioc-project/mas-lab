"""Tests for schema-backed, overlay-compatible CLI overrides."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from jsonschema import Draft7Validator
from mas.ctl.overlay import merge_overlay
from mas.ctl.overlay.normalize import normalize_overlay
from mas.ctl.overrides import (
    apply_cli_overrides,
    max_tokens_overrides,
    overlay_for_session_agent,
    overrides_for_root,
    parse_override,
)
from mas.ctl.runtime_cli import load_merged_agent_manifest
from mas.ctl.validate.schemas import load_schema

OVERLAY_CONTRACT_FIXTURES = Path(__file__).parents[2] / "tests/fixtures/overlay-contracts"


def test_parse_root_path_and_yaml_value() -> None:
    parsed = parse_override('agent:spec.context.role="security expert"')

    assert parsed.path.root == "agent"
    assert parsed.path.parent is None
    assert parsed.path.text == ".agent.spec.context.role"
    assert parsed.value == "security expert"


def test_parse_dotted_session_path() -> None:
    parsed = parse_override('.agent.spec.context.role="security expert"')

    assert parsed.path.root == "agent"
    assert parsed.path.text == ".agent.spec.context.role"
    assert parsed.value == "security expert"


def test_parse_rejects_root_prefix() -> None:
    with pytest.raises(ValueError, match="use .agent.spec"):
        parse_override('root.agent.spec.context.role="security expert"')


def test_parse_mas_agent_type_and_id() -> None:
    parsed = parse_override(".mas.agent[planner].spec.context.role=reviewer")

    assert parsed.path.root == "agent"
    assert parsed.path.parent == "mas"
    assert parsed.path.target_name == "planner"
    assert parsed.path.text == ".mas.agent[planner].spec.context.role"
    assert [segment.name for segment in parsed.path.segments] == ["spec", "context", "role"]


def test_parse_mas_agent_id_selector_alias() -> None:
    parsed = parse_override(".mas.agent[id=planner].spec.memory=long")

    assert parsed.path.target_name == "planner"
    assert parsed.path.parent == "mas"


def test_parse_selector_and_equals_in_value() -> None:
    parsed = parse_override("mas:spec.agency.agents[id=qa].spec.context.note=a=b")

    assert parsed.path.segments[2].selector == ("id", "qa")
    assert parsed.value == "a=b"


def test_parse_wildcard_and_yaml_collection() -> None:
    parsed = parse_override('agent:spec.tools={"$op":{"add":["search"]}}')

    assert parsed.path.segments[-1].name == "tools"
    assert parsed.value == {"$op": {"add": ["search"]}}


def test_parse_quoted_map_key_with_dots() -> None:
    parsed = parse_override('agent:spec.context["key.with.dots"]=value')

    assert parsed.path.segments[-1].map_key == "key.with.dots"


def test_parse_rejects_invalid_root_path() -> None:
    with pytest.raises(ValueError, match="session node"):
        parse_override("spec.tools=search")


def test_apply_nested_scalar_uses_overlay_merge() -> None:
    document = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "spec": {"context": {"role": "operator"}},
    }

    result = apply_cli_overrides(
        document,
        ("agent:spec.context.role=reviewer",),
        root="agent",
    )

    assert result["spec"]["context"]["role"] == "reviewer"
    assert document["spec"]["context"]["role"] == "operator"


def test_apply_list_operator_uses_existing_merge_semantics() -> None:
    document = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "spec": {"tools": ["calculator"]},
    }

    result = apply_cli_overrides(
        document,
        ('agent:spec.tools={"$op":{"add":["search"]}}',),
        root="agent",
    )

    assert result["spec"]["tools"] == ["calculator", "search"]


def test_cli_identity_path_is_overlay_patch_row() -> None:
    document = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "spec": {"description": "qa", "models": [{"id": "main", "model": "gpt-4o"}]},
    }

    result = apply_cli_overrides(
        document,
        ("agent:spec.models[id=main].max_tokens=4096",),
        root="agent",
    )

    assert result["spec"]["models"] == [{"id": "main", "model": "gpt-4o", "max_tokens": 4096}]


def test_apply_identity_selector_replaces_selected_list_entry() -> None:
    document = {
        "apiVersion": "mas/v1",
        "kind": "MAS",
        "spec": {
            "agency": {
                "agents": [
                    {"id": "planner", "spec": {"memory": "short"}},
                    {"id": "reviewer", "spec": {"memory": "short"}},
                ]
            }
        },
    }

    result = apply_cli_overrides(
        document,
        ("mas:spec.agency.agents[id=reviewer].spec.memory=long",),
        root="mas",
    )

    assert result["spec"]["agency"]["agents"][0]["spec"]["memory"] == "short"
    assert result["spec"]["agency"]["agents"][1]["spec"]["memory"] == "long"


def test_apply_wildcard_updates_all_matching_entries() -> None:
    document = {
        "apiVersion": "mas/v1",
        "kind": "MAS",
        "spec": {
            "agency": {
                "agents": [
                    {"id": "planner", "spec": {"memory": "short"}},
                    {"id": "reviewer", "spec": {"memory": "short"}},
                ]
            }
        },
    }

    result = apply_cli_overrides(
        document,
        ("mas:spec.agency.agents[*].spec.memory=long",),
        root="mas",
    )

    assert [entry["spec"]["memory"] for entry in result["spec"]["agency"]["agents"]] == [
        "long",
        "long",
    ]


def test_apply_rejects_wrong_root() -> None:
    with pytest.raises(ValueError, match="does not match"):
        apply_cli_overrides(
            {"apiVersion": "mas/v1", "kind": "Agent", "spec": {}},
            ("mas:spec.memory=short",),
            root="agent",
        )


def test_apply_experiment_root_uses_generic_overlay_target() -> None:
    result = apply_cli_overrides(
        {"experiment": {"name": "old", "metadata": {"owner": "lab"}}},
        ("experiment:experiment.name=new",),
        root="experiment",
    )

    assert result["experiment"]["name"] == "new"
    assert result["experiment"]["metadata"]["owner"] == "lab"


def test_apply_workspace_root_is_in_memory_only() -> None:
    document = {"defaults": {"model": "any"}}
    result = apply_cli_overrides(
        document,
        ("workspace:defaults.model=gpt-test",),
        root="workspace",
    )

    assert result == {"defaults": {"model": "gpt-test"}}
    assert document["defaults"]["model"] == "any"


def test_apply_quoted_map_key() -> None:
    result = apply_cli_overrides(
        {"apiVersion": "mas/v1", "kind": "Agent", "spec": {"context": {"key.with.dots": "old"}}},
        ('agent:spec.context["key.with.dots"]=new',),
        root="agent",
    )

    assert result["spec"]["context"]["key.with.dots"] == "new"


def test_apply_rejects_unknown_path_before_merge() -> None:
    with pytest.raises(ValueError, match="not in the schema"):
        apply_cli_overrides(
            {"apiVersion": "mas/v1", "kind": "Agent", "spec": {"context": {}}},
            ("agent:spec.not_a_real_field=value",),
            root="agent",
        )


def test_apply_rejects_missing_identity() -> None:
    document = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "spec": {"description": "qa", "models": [{"id": "main", "model": "gpt-4o"}]},
    }

    with pytest.raises(ValueError, match="no 'models' entry matches id='ghost'"):
        apply_cli_overrides(document, ("agent:spec.models[id=ghost].max_tokens=1",), root="agent")


def test_apply_rejects_selector_on_scalar() -> None:
    document = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "spec": {"description": "qa agent"},
    }

    with pytest.raises(ValueError, match="not a list|not in the schema"):
        apply_cli_overrides(document, ("agent:spec.description[id=qa]=x",), root="agent")


def test_apply_rejects_op_add_on_scalar() -> None:
    document = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "spec": {"description": "qa agent"},
    }

    with pytest.raises(ValueError, match=r"\$op cannot apply"):
        apply_cli_overrides(
            document,
            ('agent:spec.description={"$op":{"add":["x"]}}',),
            root="agent",
        )


def test_apply_creates_schema_declared_leaf_on_selected_rows() -> None:
    document = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "spec": {"models": [{"id": "main", "model": "gpt-4o"}, {"id": "summarizer", "model": "gpt-4o-mini"}]},
    }

    result = apply_cli_overrides(document, ("agent:spec.models[*].max_tokens=4096",), root="agent")

    assert [row["max_tokens"] for row in result["spec"]["models"]] == [4096, 4096]
    assert result["spec"]["models"][0]["model"] == "gpt-4o"
    with pytest.raises(ValueError, match="max_tokenz"):
        apply_cli_overrides(document, ("agent:spec.models[*].max_tokenz=1",), root="agent")


def test_overrides_for_root_keeps_argument_order() -> None:
    sources = ("agent:spec.memory=a", "infra:spec.protocol=openai", "agent:spec.memory=b")

    assert overrides_for_root(sources, "agent") == ("agent:spec.memory=a", "agent:spec.memory=b")
    assert overrides_for_root(sources, "workspace") == ()


def test_max_tokens_flag_is_an_agent_models_override() -> None:
    assert max_tokens_overrides(None) == ()
    assert max_tokens_overrides(4096) == ("agent:spec.models[*].max_tokens=4096",)


def test_agent_loader_applies_max_tokens_alias_without_declared_models() -> None:
    from mas.ctl.runtime_cli import load_merged_agent_manifest

    manifest = {"apiVersion": "mas/v1", "kind": "Agent", "metadata": {"name": "a"}, "spec": {"description": "d"}}
    overrides = (*max_tokens_overrides(2048), "infra:spec.protocol=openai", "workspace:defaults.model=m")

    data, _plugin = load_merged_agent_manifest(manifest, overrides=overrides, validate=False)

    assert data["spec"]["models"] == [{"id": "main", "model": "any", "max_tokens": 2048}]


def test_explicit_override_wins_over_max_tokens_alias() -> None:
    from mas.ctl.runtime_cli import load_merged_agent_manifest

    manifest = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "a"},
        "spec": {"description": "d", "models": [{"id": "main", "model": "gpt-4o", "max_tokens": 100}]},
    }
    overrides = (*max_tokens_overrides(2048), "agent:spec.models[id=main].max_tokens=512")

    data, _plugin = load_merged_agent_manifest(manifest, overrides=overrides, validate=False)

    assert data["spec"]["models"][0]["max_tokens"] == 512


def test_apply_honors_schema_cli_deny(monkeypatch: pytest.MonkeyPatch) -> None:
    from mas.ctl.overrides import schema as override_schema

    monkeypatch.setattr(
        override_schema,
        "load_schema",
        lambda _kind: {"properties": {"spec": {"properties": {"protected": {"x-cli": {"allowed": False}}}}}},
    )
    with pytest.raises(ValueError, match="not allowed"):
        apply_cli_overrides(
            {"apiVersion": "mas/v1", "kind": "Agent", "spec": {"protected": "old"}},
            ("agent:spec.protected=new",),
            root="agent",
        )


def test_override_without_manifest_builds_minimal_agent() -> None:
    result, plugin = load_merged_agent_manifest(
        None,
        overrides=("agent:spec.context.role=reviewer",),
        validate=False,
    )

    assert plugin == "react@v1"
    assert result is not None
    assert result["spec"]["context"]["role"] == "reviewer"


@pytest.mark.parametrize(
    "command_module",
    [
        "mas.ctl.cli.commands.chat",
        "mas.ctl.cli.commands.compile",
        "mas.ctl.cli.commands.compose",
        "mas.ctl.cli.commands.run_mas",
    ],
)
def test_public_commands_expose_override_option(command_module: str) -> None:
    import importlib

    module = importlib.import_module(command_module)
    command_name = {
        "chat": "chat_cmd",
        "compile": "compile_cmd",
        "compose": "compose_cmd",
        "run_mas": "run_mas_cmd",
    }[command_module.rsplit(".", 1)[-1]]
    result = CliRunner().invoke(getattr(module, command_name), ["--help"])

    assert result.exit_code == 0, result.output
    assert "--override PATH=VALUE" in result.output


def test_session_agent_path_becomes_overlay_target_name() -> None:
    overlay = overlay_for_session_agent(".mas.agent[reviewer].spec.memory=long")

    assert overlay["spec"]["target"] == {"kind": "Agent", "name": "reviewer"}
    assert overlay["spec"]["patch"] == {"memory": "long"}
    assert "overrides" not in overlay["spec"]
    result = merge_overlay(
        {
            "apiVersion": "mas/v1",
            "kind": "MAS",
            "spec": {
                "agency": {
                    "agents": [
                        {
                            "id": "planner",
                            "kind": "Agent",
                            "spec": {"memory": "short"},
                        },
                        {
                            "id": "reviewer",
                            "kind": "Agent",
                            "spec": {"memory": "short"},
                        },
                    ]
                }
            },
        },
        overlay,
    )

    agents = result["spec"]["agency"]["agents"]
    assert agents[0]["spec"]["memory"] == "short"
    assert agents[1]["spec"]["memory"] == "long"


def test_overrides_for_root_excludes_nested_mas_agents() -> None:
    sources = (
        ".mas.spec.workflow.entry=moderator",
        ".mas.agent[reviewer].spec.memory=long",
        ".agent.spec.memory=short",
    )

    assert overrides_for_root(sources, "mas") == (".mas.spec.workflow.entry=moderator",)
    assert overrides_for_root(sources, "agent") == (".agent.spec.memory=short",)
    assert overrides_for_root(sources, "agent", parent="mas") == (
        ".mas.agent[reviewer].spec.memory=long",
    )


def test_overlay_manifest_applies_selector_overrides_after_patch() -> None:
    result = merge_overlay(
        {
            "apiVersion": "mas/v1",
            "kind": "MAS",
            "spec": {
                "agency": {
                    "agents": [
                        {"id": "planner", "spec": {"memory": "short", "context": {"environment": "prod"}}},
                        {"id": "reviewer", "spec": {"memory": "short", "context": {"environment": "prod"}}},
                    ]
                }
            },
        },
        {
            "apiVersion": "mas/v1",
            "kind": "Overlay",
            "metadata": {"name": "reviewer-memory"},
            "spec": {
                "target": {"kind": "MAS"},
                "patch": {},
                "overrides": [
                    "mas:spec.agency.agents[id=reviewer].spec.memory=long",
                    "mas:spec.agency.agents[*].spec.context.environment=staging",
                ],
            },
        },
    )

    agents = result["spec"]["agency"]["agents"]
    assert agents[0]["spec"]["memory"] == "short"
    assert agents[1]["spec"]["memory"] == "long"
    assert [agent["spec"]["context"]["environment"] for agent in agents] == [
        "staging",
        "staging",
    ]


@pytest.mark.parametrize(
    ("fixture_name", "base", "assertion"),
    [
        (
            "agent.yaml",
            {
                "apiVersion": "mas/v1",
                "kind": "Agent",
                "metadata": {"name": "qa"},
                "spec": {"context": {"role": "base", "environment": "prod"}},
            },
            lambda result: result["spec"]["context"] == {"role": "selected", "environment": "staging"},
        ),
        (
            "mas.yaml",
            {
                "apiVersion": "mas/v1",
                "kind": "MAS",
                "metadata": {"name": "team"},
                "spec": {
                    "agency": {
                        "agents": [
                            {"id": "planner", "spec": {"context": {"environment": "prod"}}},
                            {"id": "reviewer", "spec": {"context": {"environment": "prod"}}},
                        ]
                    },
                    "workflow": {"entry": "planner"},
                },
            },
            lambda result: (
                result["spec"]["workflow"]["entry"] == "reviewer"
                and all(
                    agent["spec"]["context"]["environment"] == "staging" for agent in result["spec"]["agency"]["agents"]
                )
            ),
        ),
        (
            "infra.yaml",
            {"apiVersion": "mas/v1", "kind": "Infra", "spec": {"protocol": "openai"}},
            lambda result: result["spec"]["protocol"] == "offline",
        ),
        (
            "flavour.yaml",
            {
                "apiVersion": "mas/v1",
                "kind": "Flavour",
                "spec": {"tools": {"remote_tools_enabled": False}},
            },
            lambda result: result["spec"]["tools"]["remote_tools_enabled"] is False,
        ),
        (
            "experiment.yaml",
            {"experiment": {"run": {"n_runs": 1}}},
            lambda result: result["experiment"]["run"]["n_runs"] == 3,
        ),
        (
            "workspace.yaml",
            {"defaults": {"model": "any"}},
            lambda result: result["defaults"]["model"] == "overridden",
        ),
    ],
)
def test_overlay_contract_fixtures_validate_and_apply(fixture_name: str, base: dict, assertion) -> None:
    overlay = yaml.safe_load((OVERLAY_CONTRACT_FIXTURES / fixture_name).read_text(encoding="utf-8"))
    errors = list(Draft7Validator(load_schema("overlay")).iter_errors(overlay))
    assert not errors, [error.message for error in errors]

    result = merge_overlay(base, normalize_overlay(overlay, name=fixture_name.removesuffix(".yaml")))

    assert assertion(result)


def test_agent_overlay_fixture_target_name_selects_one_mas_agent() -> None:
    overlay = yaml.safe_load((OVERLAY_CONTRACT_FIXTURES / "agent.yaml").read_text(encoding="utf-8"))
    from mas.ctl.manifest.mas_agent_merge import apply_loaded_agent_patch

    base = {
        "apiVersion": "mas/v1",
        "kind": "MAS",
        "spec": {
            "agency": {
                "agents": [
                    {"id": "planner", "ref": "agents/planner.yaml"},
                    {"id": "qa", "ref": "agents/qa.yaml"},
                ]
            }
        },
    }

    normalized = normalize_overlay(overlay, name="agent")
    result = merge_overlay(base, normalized)
    from mas.ctl.overlay import loaded_agent_patches

    patches = loaded_agent_patches(normalized, result)
    assert set(patches) == {"qa"}
    qa = apply_loaded_agent_patch(
        {
            "apiVersion": "mas/v1",
            "kind": "Agent",
            "metadata": {"name": "qa"},
            "spec": {
                "description": "QA agent",
                "context": {"role": "base", "environment": "prod"},
            },
        },
        agent_id="qa",
        agent_patches=patches,
        mas_config=result,
    )

    assert qa["spec"]["context"] == {"role": "selected", "environment": "staging"}
