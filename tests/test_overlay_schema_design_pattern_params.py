#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from jsonschema import Draft7Validator
from mas.ctl.validate.schemas import load_schema


def test_overlay_schema_rejects_global_design_pattern_params_on_mas_target() -> None:
    schema = load_schema("overlay")
    doc = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "dp-params"},
        "spec": {
            "target": {"kind": "MAS"},
            "patch": {
                "design_pattern": {
                    "type": "concord",
                    "params": {
                        "max_rounds": 3,
                        "enable_peer_context": False,
                    },
                }
            },
        },
    }

    errors = sorted(Draft7Validator(schema).iter_errors(doc), key=lambda e: e.path)
    assert errors


def test_overlay_schema_rejects_agent_only_patch_on_flavour_target() -> None:
    schema = load_schema("overlay")
    doc = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "bad-flavour-patch"},
        "spec": {
            "target": {"kind": "Flavour"},
            "patch": {
                "design_pattern": {
                    "type": "concord",
                }
            },
        },
    }

    errors = sorted(Draft7Validator(schema).iter_errors(doc), key=lambda e: e.path)
    assert errors


def test_overlay_schema_accepts_explicit_collection_ops_for_agent_target() -> None:
    schema = load_schema("overlay")
    doc = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "agent-ops"},
        "spec": {
            "target": {"kind": "Agent"},
            "patch": {
                "tools": {"$op": {"add": ["calc"], "remove": ["web-search"]}},
                "skills": {"$op": {"replace": ["route-planning"]}},
            },
        },
    }

    errors = sorted(Draft7Validator(schema).iter_errors(doc), key=lambda e: e.path)
    assert not errors, [e.message for e in errors]


def test_overlay_schema_accepts_agent_collection_ops_for_mas_target() -> None:
    schema = load_schema("overlay")
    doc = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "mas-ops"},
        "spec": {
            "target": {"kind": "MAS"},
            "patch": {
                "agents": {
                    "$op": {
                        "remove": ["schedule_agent"],
                        "add": [{"id": "generalist", "ref": "agents/generalist.yaml"}],
                    }
                },
                "workflow": {"entry": "generalist"},
            },
        },
    }

    errors = sorted(Draft7Validator(schema).iter_errors(doc), key=lambda e: e.path)
    assert not errors, [e.message for e in errors]


def test_overlay_schema_rejects_legacy_agent_collection_fields() -> None:
    schema = load_schema("overlay")
    doc = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "legacy-agent-ops"},
        "spec": {
            "target": {"kind": "MAS"},
            "patch": {
                "agents_add": [{"id": "generalist", "ref": "agents/generalist.yaml"}],
                "agents_remove": ["schedule_agent"],
            },
        },
    }

    errors = sorted(Draft7Validator(schema).iter_errors(doc), key=lambda e: e.path)
    assert errors


def test_mas_schema_rejects_legacy_agent_collection_fields() -> None:
    schema = load_schema("mas")
    doc = {
        "apiVersion": "mas/v1",
        "kind": "MAS",
        "metadata": {"name": "legacy-agent-ops"},
        "spec": {
            "agents_add": [{"id": "generalist", "ref": "agents/generalist.yaml"}],
            "agents_remove": ["schedule_agent"],
        },
    }

    errors = sorted(Draft7Validator(schema).iter_errors(doc), key=lambda e: e.path)
    assert errors


def test_overlay_schema_accepts_entry_agent_patch_for_mas_target() -> None:
    schema = load_schema("overlay")
    doc = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "entry-pattern"},
        "spec": {
            "target": {"kind": "MAS"},
            "patch": {
                "workflow": {"entry": "moderator"},
                "agents": {"$entry": {"design_pattern": {"type": "cot", "config": {"max_steps": 10}}}},
            },
        },
    }

    errors = sorted(Draft7Validator(schema).iter_errors(doc), key=lambda e: e.path)
    assert not errors, [e.message for e in errors]


def test_overlay_schema_rejects_missing_target_kind() -> None:
    schema = load_schema("overlay")
    doc = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "missing-target"},
        "spec": {
            "patch": {
                "skills": {"$op": {"add": ["route-planning"]}},
            },
        },
    }

    errors = sorted(Draft7Validator(schema).iter_errors(doc), key=lambda e: e.path)
    assert errors


@pytest.mark.parametrize(
    ("kind", "root"),
    [
        ("Agent", "agent"),
        ("MAS", "mas"),
        ("Infra", "infra"),
        ("Flavour", "flavour"),
        ("Experiment", "experiment"),
        ("Workspace", "workspace"),
    ],
)
def test_overlay_schema_accepts_target_name_and_grouped_overrides(kind: str, root: str) -> None:
    schema = load_schema("overlay")
    doc = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": f"{root}-contract"},
        "spec": {
            "target": {"kind": kind, "name": "named-target"},
            "patch": {},
            "overrides": [f"{root}:spec.value=updated"],
        },
    }

    errors = sorted(Draft7Validator(schema).iter_errors(doc), key=lambda e: e.path)
    assert not errors, [error.message for error in errors]


def test_overlay_schema_rejects_non_string_grouped_override() -> None:
    schema = load_schema("overlay")
    doc = {
        "apiVersion": "mas/v1",
        "kind": "Overlay",
        "metadata": {"name": "bad-overrides"},
        "spec": {
            "target": {"kind": "MAS"},
            "patch": {},
            "overrides": [{"path": "mas:spec.value", "value": "updated"}],
        },
    }

    errors = sorted(Draft7Validator(schema).iter_errors(doc), key=lambda e: e.path)
    assert errors


def test_infra_overlay_fragment_covers_infra_spec_properties() -> None:
    root = Path(__file__).parents[1]
    infra_schema = yaml.safe_load((root / "docs/schemas/runtime/infra.schema.yaml").read_text(encoding="utf-8"))
    overlay_fragment = yaml.safe_load(
        (root / "docs/schemas/runtime/fragments/overlay-infra-patch.schema.yaml").read_text(encoding="utf-8")
    )

    infra_properties = set(infra_schema["properties"]["spec"]["properties"])
    overlay_properties = set(overlay_fragment["properties"])
    assert infra_properties <= overlay_properties
