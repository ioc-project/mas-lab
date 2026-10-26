#!/usr/bin/env python3
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Generate Python bindings and defaults from docs/schemas (single source of truth).

Outputs (committed):
  runtime/src/mas/runtime/spec/schema_bindings_generated.py
  runtime/src/mas/runtime/spec/schema_defaults_generated.py

Usage:
  python scripts/gen_schema_artifacts.py          # write files
  python scripts/gen_schema_artifacts.py --check  # exit 1 if outputs differ
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_ROOT = _REPO_ROOT / "docs" / "schemas" / "runtime"
_FRAGMENTS = _SCHEMA_ROOT / "fragments"
_RUNTIME_SPEC = _REPO_ROOT / "runtime" / "src" / "mas" / "runtime" / "spec"
_BINDINGS_OUT = _RUNTIME_SPEC / "schema_bindings_generated.py"
_DEFAULTS_OUT = _RUNTIME_SPEC / "schema_defaults_generated.py"


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected mapping")
    return data


def _resolve_ref(ref: str, base: Path) -> dict[str, Any]:
    if ref.startswith("./"):
        return _load((base / ref[2:]).resolve())
    raise ValueError(f"unsupported $ref: {ref}")


def _merged_properties(schema: dict[str, Any], base: Path) -> dict[str, Any]:
    """Collect properties from a schema node, merging allOf $ref fragments."""
    props: dict[str, Any] = {}
    if "properties" in schema:
        props.update(schema["properties"])
    for part in schema.get("allOf") or []:
        if not isinstance(part, dict):
            continue
        ref = part.get("$ref")
        if ref:
            props.update(_merged_properties(_resolve_ref(ref, base), base))
        else:
            props.update(_merged_properties(part, base))
    return props


def _property_keys(schema: dict[str, Any], base: Path) -> frozenset[str]:
    return frozenset(_merged_properties(schema, base).keys())


def _filesystem_path_param_keys(schema: dict[str, Any], base: Path) -> frozenset[str]:
    keys: list[str] = []
    for key, prop in _merged_properties(schema, base).items():
        if isinstance(prop, dict) and prop.get("x-filesystem-path"):
            keys.append(key)
    return frozenset(keys)


def _infra_plugin_param_schemas() -> list[Path]:
    """Closed per-plugin middleware param fragments (not the open default)."""
    skip = {
        "infra-middleware-params.schema.yaml",
        "infra-middleware-params-by-id.schema.yaml",
        "infra-middleware-inline.schema.yaml",
    }
    return sorted(
        path
        for path in _FRAGMENTS.glob("infra-*-params.schema.yaml")
        if path.name not in skip
    )


def _infra_middleware_path_param_keys() -> frozenset[str]:
    keys: set[str] = set()
    for path in _infra_plugin_param_schemas():
        keys.update(_filesystem_path_param_keys(_load(path), _FRAGMENTS))
    return frozenset(keys)


def _property_defaults(schema: dict[str, Any], base: Path) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    for key, prop in _merged_properties(schema, base).items():
        if isinstance(prop, dict) and "default" in prop:
            defaults[key] = prop["default"]
    return defaults


def _nested_keys(schema: dict[str, Any], *path: str) -> frozenset[str]:
    node: Any = schema
    for key in path:
        node = (node.get("properties") or {}).get(key)
        if not isinstance(node, dict):
            return frozenset()
    props = node.get("properties") or {}
    return frozenset(props.keys())


def _object_branch(schema: dict[str, Any]) -> dict[str, Any]:
    """Prefer the object alternative when a slot is ``string | object``."""
    if schema.get("properties"):
        return schema
    for alt in schema.get("oneOf") or []:
        if isinstance(alt, dict) and (alt.get("type") == "object" or alt.get("properties")):
            return alt
    return schema


def _agent_binding_keys(agent: dict[str, Any], binding: str) -> frozenset[str]:
    spec = (agent.get("properties") or {}).get("spec") or {}
    spec_props = spec.get("properties") or {}
    binding_schema = _object_branch(spec_props.get(binding) or {})
    props = binding_schema.get("properties") or {}
    return frozenset(props.keys())


def _render_frozenset(name: str, keys: frozenset[str]) -> str:
    ordered = ",\n    ".join(repr(k) for k in sorted(keys))
    if not keys:
        return f"{name} = frozenset()\n"
    return f"{name} = frozenset(\n    {{\n    {ordered},\n    }}\n)\n"


def _render_bindings(agent: dict[str, Any]) -> str:
    llm = _load(_FRAGMENTS / "llm-binding.schema.yaml")
    execution = _load(_FRAGMENTS / "execution-binding.schema.yaml")
    control = _load(_FRAGMENTS / "control-binding.schema.yaml")
    assembly = _load(_FRAGMENTS / "context-manager-assembly-params.schema.yaml")
    strategy = _load(_FRAGMENTS / "context-manager-strategy-params.schema.yaml")

    lines = [
        "#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates",
        "#  SPDX-License-Identifier: Apache-2.0",
        '"""AUTO-GENERATED by scripts/gen_schema_artifacts.py — do not edit."""',
        "",
        "from __future__ import annotations",
        "",
    ]
    chunks = [
        ("LLM_BINDING_KEYS", _property_keys(llm, _FRAGMENTS)),
        ("EXECUTION_BINDING_KEYS", _property_keys(execution, _FRAGMENTS)),
        ("EXECUTION_CACHE_KEYS", _nested_keys(execution, "cache")),
        ("CONTROL_BINDING_KEYS", _property_keys(control, _FRAGMENTS)),
        ("DESIGN_PATTERN_BINDING_KEYS", _agent_binding_keys(agent, "design_pattern")),
        ("CONTEXT_MANAGER_BINDING_KEYS", _agent_binding_keys(agent, "context_manager")),
        ("ASSEMBLER_BINDING_KEYS", _agent_binding_keys(agent, "assembler")),
        (
            "CONTEXT_MANAGER_ASSEMBLY_PARAM_KEYS",
            _property_keys(assembly, _FRAGMENTS),
        ),
        (
            "CONTEXT_MANAGER_STRATEGY_PARAM_KEYS",
            _property_keys(strategy, _FRAGMENTS),
        ),
        (
            "INFRA_MIDDLEWARE_PATH_PARAM_KEYS",
            _infra_middleware_path_param_keys(),
        ),
    ]
    for name, keys in chunks:
        lines.append(_render_frozenset(name, keys))
    return "\n".join(lines) + "\n"


def _agent_model_item_defaults(agent: dict[str, Any]) -> dict[str, Any]:
    spec = (agent.get("properties") or {}).get("spec") or {}
    models = (spec.get("properties") or {}).get("models") or {}
    items = models.get("items") or {}
    props = items.get("properties") or {}
    defaults: dict[str, Any] = {}
    for key, prop in props.items():
        if isinstance(prop, dict) and "default" in prop:
            defaults[key] = prop["default"]
    return defaults


def _config_archive_defaults() -> dict[str, Any]:
    config = _load(_REPO_ROOT / "docs" / "schemas" / "config.schema.yaml")
    mas_ctl = ((config.get("properties") or {}).get("mas_ctl") or {}).get("properties") or {}
    archive = (mas_ctl.get("archive") or {}).get("properties") or {}
    return {
        "enabled": (archive.get("enabled") or {}).get("default", False),
        "dir_name": (archive.get("dir_name") or {}).get("default", "archive"),
        "memory_watermark_bytes": (archive.get("memory_watermark_bytes") or {}).get("default", 67108864),
    }


def _render_defaults() -> str:
    execution = _load(_FRAGMENTS / "execution-binding.schema.yaml")
    assembly = _load(_FRAGMENTS / "context-manager-assembly-params.schema.yaml")
    strategy = _load(_FRAGMENTS / "context-manager-strategy-params.schema.yaml")
    chunk_meta = _load(_FRAGMENTS / "chunk-meta.schema.yaml")
    agent = _load(_SCHEMA_ROOT / "agent.schema.yaml")
    exec_defaults = _property_defaults(execution, _FRAGMENTS)
    asm_defaults = _property_defaults(assembly, _FRAGMENTS)
    strategy_defaults = _property_defaults(strategy, _FRAGMENTS)
    chunk_defaults = _property_defaults(chunk_meta, _FRAGMENTS)
    chunk_props = chunk_meta.get("properties") or {}
    pin_enum = list((chunk_props.get("pin_policy") or {}).get("enum") or ("never", "prefer", "ephemeral"))
    semantic_enum = list(
        (chunk_props.get("semantic") or {}).get("enum")
        or ("catalog", "instruction", "history", "tool_result", "summary")
    )
    model_defaults = _agent_model_item_defaults(agent)
    archive_defaults = _config_archive_defaults()
    asm_props = _merged_properties(assembly, _FRAGMENTS)
    trimmer_props = (asm_props.get("trimmer") or {}).get("properties") or {}
    reserve_default = (trimmer_props.get("reserve_tokens") or {}).get("default", 512)
    margin_default = (trimmer_props.get("safety_margin_ratio") or {}).get("default", 0.1)

    lines = [
        "#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates",
        "#  SPDX-License-Identifier: Apache-2.0",
        '"""AUTO-GENERATED by scripts/gen_schema_artifacts.py — do not edit."""',
        "",
        "from __future__ import annotations",
        "",
        f"EXECUTION_MAX_AUTO_STEPS = {exec_defaults['max_auto_steps']!r}",
        f"EXECUTION_ENGINE_QUEUE_DEPTH = {exec_defaults['engine_queue_depth']!r}",
        f"CONTEXT_MANAGER_WORKING_MEMORY_MESSAGES = {asm_defaults.get('working_memory_messages', 0)!r}",
        f"CONTEXT_MANAGER_RESERVE_TOKENS = {reserve_default!r}",
        f"CONTEXT_MANAGER_SAFETY_MARGIN_RATIO = {margin_default!r}",
        f"CONTEXT_MANAGER_KEEP_TURNS = {strategy_defaults.get('keep_turns', 10)!r}",
        f"CONTEXT_MANAGER_HYSTERESIS_RATIO = {strategy_defaults.get('hysteresis_ratio', 0.1)!r}",
        f"DEFAULT_MODEL_CONTEXT_WINDOW = {model_defaults.get('context_window', 128000)!r}",
        f"DEFAULT_MODEL_MAX_TOKENS = {model_defaults.get('max_tokens', 2000)!r}",
        f"CHUNK_MESSAGE_KEY = {chunk_defaults.get('message_key', 'mas')!r}",
        f"CHUNK_PIN_POLICY_DEFAULT = {chunk_defaults.get('pin_policy', 'ephemeral')!r}",
        f"CHUNK_SEMANTIC_DEFAULT = {chunk_defaults.get('semantic', 'history')!r}",
        f"CHUNK_SUMMARY_HEADING = {chunk_defaults.get('summary_heading', '[Conversation summary —')!r}",
        *[f"CHUNK_PIN_{str(value).upper().replace('-', '_')} = {value!r}" for value in pin_enum],
        f"CHUNK_PIN_POLICIES = {tuple(pin_enum)!r}",
        *[f"CHUNK_SEMANTIC_{str(value).upper().replace('-', '_')} = {value!r}" for value in semantic_enum],
        f"CHUNK_SEMANTICS = {tuple(semantic_enum)!r}",
        f"ARCHIVE_ENABLED = {archive_defaults['enabled']!r}",
        f"ARCHIVE_DIR_NAME = {archive_defaults['dir_name']!r}",
        f"ARCHIVE_MEMORY_WATERMARK_BYTES = {archive_defaults['memory_watermark_bytes']!r}",
        "",
    ]
    return "\n".join(lines)


def generate() -> tuple[str, str]:
    agent = _load(_SCHEMA_ROOT / "agent.schema.yaml")
    return _render_bindings(agent), _render_defaults()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if outputs are stale")
    args = parser.parse_args()

    bindings, defaults = generate()
    if args.check:
        ok = True
        if _BINDINGS_OUT.read_text(encoding="utf-8") != bindings:
            print(f"stale: {_BINDINGS_OUT.relative_to(_REPO_ROOT)}", file=sys.stderr)
            ok = False
        if _DEFAULTS_OUT.read_text(encoding="utf-8") != defaults:
            print(f"stale: {_DEFAULTS_OUT.relative_to(_REPO_ROOT)}", file=sys.stderr)
            ok = False
        return 0 if ok else 1

    _BINDINGS_OUT.write_text(bindings, encoding="utf-8")
    _DEFAULTS_OUT.write_text(defaults, encoding="utf-8")
    print(f"wrote {_BINDINGS_OUT.relative_to(_REPO_ROOT)}")
    print(f"wrote {_DEFAULTS_OUT.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
