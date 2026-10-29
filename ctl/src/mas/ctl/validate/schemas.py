#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Manifest JSON Schema loader — schemas live under docs/schemas/.

Two layouts are supported so this works both in a source checkout (dev /
editable install, where ``docs/schemas`` sits a few parents above this file)
and when installed as a built wheel (e.g. via ``uv tool install``, where
there is no repo root — the schemas are instead packaged inside ``mas.ctl``
itself via ``force-include`` and resolved through ``importlib.resources``).
"""

from __future__ import annotations

import hashlib
from importlib import resources
from pathlib import Path
from typing import Any

from mas.runtime.spec.source import cached_yaml_document, clear_yaml_file_cache


def _repo_checkout_schema_root() -> Path | None:
    # .../mas-lab/ctl/src/mas/ctl/validate/schemas.py -> parents[5] == repo root
    candidate = Path(__file__).resolve().parents[5] / "docs" / "schemas"
    return candidate if candidate.is_dir() else None


def _packaged_schema_root() -> Path:
    # Falls back to the copy of docs/schemas packaged inside mas.ctl (see
    # [tool.hatch.build.targets.wheel.force-include] in ctl/pyproject.toml).
    return Path(str(resources.files("mas.ctl") / "_schemas"))


_SCHEMA_ROOT = _repo_checkout_schema_root() or _packaged_schema_root()

_KIND_MAP: dict[str, str] = {
    "agent": "runtime/agent.schema.yaml",
    "mas": "runtime/mas.schema.yaml",
    "overlay": "runtime/overlay.schema.yaml",
    "infra": "runtime/infra.schema.yaml",
    "flavour": "runtime/flavour.schema.yaml",
    "workflow": "runtime/workflow.schema.yaml",
    "tool": "runtime/tool.schema.yaml",
    "tool_bundle": "runtime/tool_bundle.schema.yaml",
    "prompt_bundle": "runtime/prompt_bundle.schema.yaml",
    "deployment": "deployment.schema.yaml",
    "runtime_profile": "runtime-profile.schema.yaml",
    "runtime-profile": "runtime-profile.schema.yaml",
    "memory_seed": "memory-seed.schema.yaml",
    "memory-seed": "memory-seed.schema.yaml",
    "checkpoint": "checkpoint.schema.yaml",
    "effective_bind": "effective-bind.schema.yaml",
    "effective-bind": "effective-bind.schema.yaml",
    "placement_plan": "placement-plan.schema.yaml",
    "experiment": "lab/experiment.schema.yaml",
    "lab_config": "lab/lab-config.schema.yaml",
    "dataset": "lab/dataset.schema.yaml",
    "pipeline": "lab/pipeline.schema.yaml",
    "library": "library.schema.yaml",
    "config": "config.schema.yaml",
}

_YAML_KIND: dict[str, str] = {
    "Agent": "agent",
    "MAS": "mas",
    "Overlay": "overlay",
    "Workflow": "workflow",
    "Flavour": "flavour",
    "Tool": "tool",
    "PromptBundle": "prompt_bundle",
    "ToolBundle": "tool_bundle",
    "Deployment": "deployment",
    "RuntimeProfile": "runtime_profile",
    "PlacementPlan": "placement_plan",
    "MemorySeed": "memory_seed",
    "InfraBundle": "infra",
    "InfraMiddleware": "infra",
    "InfraInterceptor": "infra",
    "LLMProxy": "infra",
    "LLMLocal": "infra",
    "ToolServerRegistry": "infra",
    "PersonalSecrets": "infra",
    "Application": "infra",
    "ToolRegistry": "infra",
    "ToolProvider": "infra",
    "Datastore": "infra",
    "SecretsProvider": "infra",
    "Infrastructure": "infra",
    "ServiceBundle": "infra",
    "RuntimeEngine": "infra",
    "Library": "library",
}


def schema_root() -> Path:
    return _SCHEMA_ROOT


def declared_kind(data: dict) -> str | None:
    """Return manifest kind from explicit document shape only — no heuristics."""
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("experiment"), dict):
        return "experiment"
    if isinstance(data.get("pipeline"), dict):
        return "pipeline"
    if isinstance(data.get("lab"), dict):
        return "lab-config"
    explicit = data.get("kind")
    if isinstance(explicit, str):
        mapped = _YAML_KIND.get(explicit, explicit.lower())
        if mapped in _KIND_MAP or schema_path_for_kind(mapped) is not None:
            return mapped
        if explicit == "Dataset":
            return "dataset"
    return None


def infer_kind(data: dict) -> str | None:
    """Alias for :func:`declared_kind` (explicit manifest shape only)."""
    return declared_kind(data)


def schema_path_for_kind(kind: str) -> Path | None:
    rel = _KIND_MAP.get(kind.lower().replace("-", "_"))
    if rel is None:
        return None
    path = _SCHEMA_ROOT / rel
    return path if path.exists() else None


def load_schema(kind: str) -> dict:
    """Return the JSON Schema for *kind*.

    The mapping is read-only: fragments are inlined by reference, so a
    mutation can show up in other kinds that share that fragment.
    """
    path = schema_path_for_kind(kind)
    if path is None:
        raise KeyError(f"no schema registered for kind: {kind}")
    return _load_resolved_schema_file(path)


def lab_schema_registry():
    """Registry of ``$id`` documents under docs/schemas (lab fragments + runtime)."""
    from mas.lab.schemas.validate import schema_registry_from_dir

    return schema_registry_from_dir(_SCHEMA_ROOT)


_RESOLVED_SCHEMA: dict[Path, tuple[tuple[tuple[str, str], ...], Any]] = {}


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_stamp(path: Path) -> tuple[str, str]:
    return (str(path), _file_digest(path))


def _stamps_match(stamps: tuple[tuple[str, str], ...]) -> bool:
    for loc, digest in stamps:
        try:
            if _file_digest(Path(loc)) != digest:
                return False
        except FileNotFoundError:
            return False
    return True


def clear_schema_caches() -> None:
    """Drop inlined schema fragments and parsed YAML. Next load re-reads disk."""
    _RESOLVED_SCHEMA.clear()
    clear_yaml_file_cache()


def _load_resolved_schema_file(
    path: Path,
    *,
    collected: list[tuple[str, str]] | None = None,
) -> Any:
    """Load *path* and inline its relative ``$ref``s once per unchanged file set."""
    resolved = Path(path).resolve()
    hit = _RESOLVED_SCHEMA.get(resolved)
    if hit is not None and _stamps_match(hit[0]):
        if collected is not None:
            collected.extend(hit[0])
        return hit[1]
    local: list[tuple[str, str]] = [_file_stamp(resolved)]
    raw = cached_yaml_document(resolved)
    doc = _resolve_local_refs(raw, resolved.parent, collected=local)
    stamps = tuple(local)
    _RESOLVED_SCHEMA[resolved] = (stamps, doc)
    if collected is not None:
        collected.extend(stamps)
    return doc


def _resolve_json_pointer(doc: Any, pointer: str, *, ref: str, source: Path) -> Any:
    target = doc
    for segment in pointer.strip("/").split("/"):
        segment = segment.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, dict) or segment not in target:
            raise KeyError(f"$ref {ref!r} — pointer segment {segment!r} not found in {source}")
        target = target[segment]
    return target


def _is_local_ref(ref: Any) -> bool:
    return isinstance(ref, str) and (ref.startswith("./") or ref.startswith("../"))


def _resolve_local_refs(
    node: Any,
    base_dir: Path,
    *,
    collected: list[tuple[str, str]] | None = None,
) -> Any:
    """Inline relative (``./``, ``../``) JSON Schema refs so validation does
    not depend on ``$id`` URIs.

    Supports an optional ``#/json/pointer`` suffix to reference one specific
    node inside the target file — e.g. ``../agent.schema.yaml#/properties/
    spec/properties/working_memory`` — instead of loading (and inlining) the
    whole file. This lets a schema like an overlay-patch fragment point
    straight at a property the canonical schema already declares, rather
    than re-declaring its shape by hand — one definition, referenced, not
    copied.

    A pointer ``$ref`` resolves the whole fragment file first, then picks
    the pointer. The result is read-only: fragments are shared by reference.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if _is_local_ref(ref):
            file_part, _, pointer_part = ref.partition("#")
            frag_path = (base_dir / file_part).resolve()
            loaded = _load_resolved_schema_file(frag_path, collected=collected)
            if pointer_part:
                return _resolve_json_pointer(loaded, pointer_part, ref=ref, source=frag_path)
            return loaded
        return {
            k: _resolve_local_refs(v, base_dir, collected=collected)
            for k, v in node.items()
            if k != "$ref" or not _is_local_ref(ref)
        }
    if isinstance(node, list):
        return [_resolve_local_refs(item, base_dir, collected=collected) for item in node]
    return node


def list_schema_kinds() -> list[str]:
    return sorted(_KIND_MAP.keys())
