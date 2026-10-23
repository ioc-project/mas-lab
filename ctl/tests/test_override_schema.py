"""Schema walker for CLI overrides: every documented field is addressable."""

from __future__ import annotations

import pytest
from mas.ctl.overlay.merge import OverlayTargetError, merge_overlay
from mas.ctl.overrides.apply import apply_cli_overrides, apply_manifest_overrides
from mas.ctl.overrides.parser import parse_override
from mas.ctl.overrides.schema import path_is_declared, path_may_create
from mas.ctl.validate.schemas import load_schema


def _path(source: str):
    return parse_override(source).path


def test_walker_unions_oneof_alternatives() -> None:
    schema = {
        "oneOf": [
            {"additionalProperties": False, "properties": {"a": {"type": "string"}}},
            {"additionalProperties": False, "properties": {"b": {"type": "number"}}},
        ]
    }

    assert path_is_declared(schema, _path("doc:a=x"))
    assert path_is_declared(schema, _path("doc:b=1"))
    assert not path_is_declared(schema, _path("doc:c=1"))


def test_walker_follows_if_then_plugin_params() -> None:
    schema = {
        "properties": {
            "spec": {
                "properties": {
                    "middleware": {"type": "string"},
                    "params": {"type": "object", "additionalProperties": False},
                },
                "allOf": [
                    {
                        "if": {"properties": {"middleware": {"const": "cache"}}},
                        "then": {
                            "properties": {
                                "params": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {"allow_write": {"type": "boolean"}},
                                }
                            }
                        },
                    }
                ],
            }
        }
    }

    assert path_is_declared(schema, _path("doc:spec.params.allow_write=true"))
    assert not path_is_declared(schema, _path("doc:spec.params.not_a_knob=true"))


def test_walker_open_map_and_closed_object() -> None:
    schema = {
        "properties": {
            "open": {"type": "object", "additionalProperties": True},
            "closed": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"keep": {"type": "string"}},
            },
        }
    }

    assert path_is_declared(schema, _path("doc:open.anything=1"))
    assert path_is_declared(schema, _path("doc:closed.keep=x"))
    assert not path_is_declared(schema, _path("doc:closed.extra=x"))


def test_agent_schema_documents_envelope_and_open_params() -> None:
    schema = load_schema("agent")

    assert path_is_declared(schema, _path("agent:metadata.version=1.0.0"))
    assert path_is_declared(schema, _path("agent:spec.params.sidecar=true"))
    assert path_is_declared(schema, _path("agent:spec.models[*].max_tokens=4096"))
    assert path_is_declared(schema, _path('agent:spec.context["key.with.dots"]=x'))
    assert not path_is_declared(schema, _path("agent:spec.not_a_real_field=value"))
    assert not path_is_declared(schema, _path("agent:spec.models[*].max_tokenz=1"))


def test_flavour_schema_documents_open_spec_maps() -> None:
    schema = load_schema("flavour")

    assert path_is_declared(schema, _path("flavour:spec.capabilities.remote_eval=true"))
    assert path_may_create("flavour", _path("flavour:spec.capabilities.remote_eval=true"))


def test_apply_creates_missing_context_chunk() -> None:
    agent = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "qa"},
        "spec": {"description": "qa agent"},
    }

    result = apply_cli_overrides(agent, ('agent:spec.context.role="reviewer"',), root="agent")
    assert result["spec"]["context"]["role"] == "reviewer"
    assert "context" not in agent["spec"]


def test_apply_creates_schema_declared_envelope_and_open_map() -> None:
    agent = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "qa"},
        "spec": {"description": "qa agent"},
    }

    renamed = apply_cli_overrides(agent, ("agent:metadata.name=renamed",), root="agent")
    assert renamed["metadata"]["name"] == "renamed"
    assert "version" not in agent["metadata"]

    versioned = apply_manifest_overrides(agent, ("agent:metadata.version=1.0.0",))
    assert versioned["metadata"]["version"] == "1.0.0"

    parametrized = apply_cli_overrides(agent, ("agent:spec.params.sidecar=trace",), root="agent")
    assert parametrized["spec"]["params"]["sidecar"] == "trace"


def test_apply_creates_missing_flavour_capability() -> None:
    flavour = {
        "apiVersion": "mas/v1",
        "kind": "Flavour",
        "metadata": {"name": "local"},
        "spec": {"tools": {"remote_tools_enabled": False}},
    }

    result = apply_cli_overrides(
        flavour,
        ("flavour:spec.capabilities.remote_eval=true",),
        root="flavour",
    )
    assert result["spec"]["tools"]["remote_tools_enabled"] is False
    assert result["spec"]["capabilities"]["remote_eval"] is True


def test_apply_creates_missing_middleware_param() -> None:
    document = {
        "apiVersion": "infra/v1",
        "kind": "InfraMiddleware",
        "metadata": {"name": "cache"},
        "spec": {"middleware": "llm_cache", "params": {"allow_read": True}},
    }

    result = apply_manifest_overrides(document, ("infra:spec.params.allow_write=true",))
    assert result["spec"]["params"]["allow_read"] is True
    assert result["spec"]["params"]["allow_write"] is True


def test_walker_if_then_intersects_closed_plugin_params() -> None:
    schema = {
        "properties": {
            "spec": {
                "properties": {
                    "middleware": {"type": "string"},
                    "params": {"type": "object", "additionalProperties": True},
                },
                "allOf": [
                    {
                        "if": {"properties": {"middleware": {"const": "cache"}}, "required": ["middleware"]},
                        "then": {
                            "properties": {
                                "params": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {"allow_write": {"type": "boolean"}},
                                }
                            }
                        },
                    }
                ],
            }
        }
    }
    document = {"spec": {"middleware": "cache", "params": {"allow_read": True}}}

    assert path_is_declared(schema, _path("doc:spec.params.allow_write=true"), instance=document)
    assert not path_is_declared(schema, _path("doc:spec.params.not_a_knob=true"), instance=document)
    unknown = {"spec": {"middleware": "custom", "params": {}}}
    assert path_is_declared(schema, _path("doc:spec.params.not_a_knob=true"), instance=unknown)


def test_apply_rejects_undeclared_closed_field() -> None:
    document = {
        "apiVersion": "infra/v1",
        "kind": "InfraMiddleware",
        "metadata": {"name": "cache"},
        "spec": {"middleware": "llm_cache", "params": {"allow_read": True}},
    }

    with pytest.raises(ValueError, match="not in the schema"):
        apply_manifest_overrides(
            document, ("infra:metadata.not_a_real_field=true",), validate=False
        )


def test_apply_rejects_unknown_llm_cache_param() -> None:
    document = {
        "apiVersion": "infra/v1",
        "kind": "InfraMiddleware",
        "metadata": {"name": "cache"},
        "spec": {"middleware": "llm_cache", "params": {"allow_read": True}},
    }

    with pytest.raises(ValueError, match="not in the schema"):
        apply_manifest_overrides(document, ("infra:spec.params.not_a_knob=true",), validate=False)


def test_walker_enters_plugin_binding_list_items() -> None:
    flavour = load_schema("flavour")
    agent = load_schema("agent")

    assert path_is_declared(flavour, _path("flavour:spec.observability[*].native.path=traces/x.jsonl"))
    assert not path_is_declared(flavour, _path("flavour:spec.observability[*].not_a_sink.path=x"))
    assert path_is_declared(agent, _path("agent:spec.governance[*].sample_governance.hitl_on_tool=true"))


def test_overlay_patch_accepts_plugin_binding_rows() -> None:
    flavour = {
        "apiVersion": "mas/v1",
        "kind": "Flavour",
        "metadata": {"name": "local"},
        "spec": {"observability": ["native"]},
    }
    merged_flavour = merge_overlay(
        flavour,
        {
            "apiVersion": "mas/v1",
            "kind": "Overlay",
            "metadata": {"name": "events-file"},
            "spec": {
                "target": {"kind": "Flavour"},
                "patch": {"observability": [{"native": {"path": "traces/custom.jsonl"}}]},
            },
        },
    )
    assert merged_flavour["spec"]["observability"] == [{"native": {"path": "traces/custom.jsonl"}}]

    agent = {
        "apiVersion": "mas/v1",
        "kind": "Agent",
        "metadata": {"name": "moderator"},
        "spec": {"description": "moderator", "governance": ["sample_governance"]},
    }
    merged_agent = merge_overlay(
        agent,
        {
            "apiVersion": "mas/v1",
            "kind": "Overlay",
            "metadata": {"name": "guardrail"},
            "spec": {
                "target": {"kind": "Agent"},
                "patch": {
                    "governance": [{"sample_governance": {"policies": [{"name": "forbidden-destination"}]}}]
                },
            },
        },
    )
    names = [
        item if isinstance(item, str) else next(iter(item))
        for item in merged_agent["spec"]["governance"]
    ]
    assert "sample_governance" in names


def test_overlay_patch_rejects_unknown_observability_plugin() -> None:
    flavour = {
        "apiVersion": "mas/v1",
        "kind": "Flavour",
        "metadata": {"name": "local"},
        "spec": {"observability": ["native"]},
    }
    with pytest.raises(OverlayTargetError, match="not in the schema"):
        merge_overlay(
            flavour,
            {
                "apiVersion": "mas/v1",
                "kind": "Overlay",
                "metadata": {"name": "unknown-sink"},
                "spec": {
                    "target": {"kind": "Flavour"},
                    "patch": {"observability": [{"not_a_sink": {"path": "x"}}]},
                },
            },
        )
