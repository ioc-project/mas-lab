#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Local $ref resolution — whole-file inline (existing) + #/json/pointer (new)."""

from pathlib import Path

import pytest
import yaml
from mas.ctl.validate.schemas import (
    _load_resolved_schema_file,
    _resolve_local_refs,
    clear_schema_caches,
    load_schema,
)


def _write_yaml(path: Path, doc: dict) -> None:
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")


def test_whole_file_ref_still_inlines_verbatim(tmp_path: Path):
    """Backward compatibility: a $ref with no '#' behaves exactly as before."""
    _write_yaml(tmp_path / "fragment.yaml", {"type": "string", "minLength": 1})
    schema = {"properties": {"name": {"$ref": "./fragment.yaml"}}}

    resolved = _resolve_local_refs(schema, tmp_path)

    assert resolved["properties"]["name"] == {"type": "string", "minLength": 1}


def test_json_pointer_ref_extracts_one_nested_property(tmp_path: Path):
    """A #/json/pointer suffix resolves to just that node -- lets a patch
    schema point at a canonical schema's own property definition instead of
    re-declaring its shape by hand."""
    _write_yaml(
        tmp_path / "agent.schema.yaml",
        {
            "properties": {
                "spec": {
                    "properties": {
                        "working_memory": {
                            "type": "object",
                            "properties": {"persistent": {"type": "boolean"}},
                        },
                        "memory": {"type": "string"},
                    }
                }
            }
        },
    )
    patch_schema = {
        "properties": {
            "working_memory": {
                "$ref": "./agent.schema.yaml#/properties/spec/properties/working_memory"
            }
        }
    }

    resolved = _resolve_local_refs(patch_schema, tmp_path)

    assert resolved["properties"]["working_memory"] == {
        "type": "object",
        "properties": {"persistent": {"type": "boolean"}},
    }


def test_json_pointer_ref_resolves_refs_nested_inside_the_pointed_at_node(tmp_path: Path):
    """Whatever the pointer resolves to is itself ref-resolved recursively."""
    _write_yaml(tmp_path / "shared.yaml", {"type": "boolean"})
    _write_yaml(
        tmp_path / "agent.schema.yaml",
        {
            "properties": {
                "spec": {
                    "properties": {
                        "working_memory": {
                            "type": "object",
                            "properties": {"persistent": {"$ref": "./shared.yaml"}},
                        }
                    }
                }
            }
        },
    )
    patch_schema = {
        "working_memory": {
            "$ref": "./agent.schema.yaml#/properties/spec/properties/working_memory"
        }
    }

    resolved = _resolve_local_refs(patch_schema, tmp_path)

    assert resolved["working_memory"]["properties"]["persistent"] == {"type": "boolean"}


def test_pointer_ref_fails_when_an_unused_fragment_ref_is_missing(tmp_path: Path) -> None:
    """A pointer $ref resolves the whole fragment first, so a bad $ref
    in an unused part of that file fails every schema that includes it.
    """
    _write_yaml(
        tmp_path / "fragment.yaml",
        {
            "used": {"type": "string"},
            "dead": {"$ref": "./missing.yaml"},
        },
    )
    schema = {"x": {"$ref": "./fragment.yaml#/used"}}
    with pytest.raises(FileNotFoundError):
        _resolve_local_refs(schema, tmp_path)


def test_json_pointer_ref_missing_segment_raises_keyerror(tmp_path: Path):
    _write_yaml(tmp_path / "agent.schema.yaml", {"properties": {"spec": {"properties": {}}}})
    patch_schema = {"x": {"$ref": "./agent.schema.yaml#/properties/spec/properties/nope"}}

    with pytest.raises(KeyError):
        _resolve_local_refs(patch_schema, tmp_path)


def test_parent_dir_ref_with_pointer_resolves(tmp_path: Path):
    """A ``../`` ref (fragment referencing a file one directory up, the real
    fragments/-to-agent.schema.yaml shape) resolves the same as ``./``."""
    fragments_dir = tmp_path / "fragments"
    fragments_dir.mkdir()
    _write_yaml(
        tmp_path / "agent.schema.yaml",
        {"properties": {"spec": {"properties": {"memory": {"type": "string"}}}}},
    )
    patch_schema = {"memory": {"$ref": "../agent.schema.yaml#/properties/spec/properties/memory"}}

    resolved = _resolve_local_refs(patch_schema, fragments_dir)

    assert resolved["memory"] == {"type": "string"}


def test_json_pointer_ref_handles_tilde_escaping(tmp_path: Path):
    """JSON Pointer escapes '~' as '~0' and '/' as '~1' within a segment name."""
    _write_yaml(tmp_path / "agent.schema.yaml", {"a/b": {"c~d": {"type": "string"}}})
    patch_schema = {"x": {"$ref": "./agent.schema.yaml#/a~1b/c~0d"}}

    resolved = _resolve_local_refs(patch_schema, tmp_path)

    assert resolved["x"] == {"type": "string"}


def test_resolved_schema_picks_up_a_changed_fragment(tmp_path: Path) -> None:
    clear_schema_caches()
    shared = tmp_path / "shared.yaml"
    root = tmp_path / "root.yaml"
    _write_yaml(shared, {"type": "string"})
    _write_yaml(root, {"properties": {"x": {"$ref": "./shared.yaml"}}})
    first = _load_resolved_schema_file(root)
    assert first["properties"]["x"] == {"type": "string"}
    _write_yaml(shared, {"type": "boolean"})
    second = _load_resolved_schema_file(root)
    assert second["properties"]["x"] == {"type": "boolean"}
    assert first is not second


def test_overlay_schema_parses_each_fragment_once(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_schema_caches()
    real = yaml.safe_load
    seen: list[str] = []

    def wrapped(data: object, *args: object, **kwargs: object) -> object:
        name = getattr(data, "name", None)
        if isinstance(name, str) and name.endswith("llm-sampling.schema.yaml"):
            seen.append(name)
        if isinstance(data, str) and "mas/v1#llm-sampling" in data:
            seen.append("text")
        return real(data, *args, **kwargs)

    monkeypatch.setattr("mas.runtime.spec.source.yaml.safe_load", wrapped)
    load_schema("overlay")
    load_schema("overlay")
    assert seen.count("text") == 1
