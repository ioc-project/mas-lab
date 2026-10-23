#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Resolve infra/v1 refs to merged LLM proxy configuration."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.infra.env_resolve import resolve_manifest_values
from mas.ctl.infra.models import InfraManifest, ModelsSpec, ProxySpec
from mas.ctl.infra.pipeline_chain import BidirectionalInfraPipeline
from mas.ctl.libraries.bundles import list_manifest_libraries
from mas.ctl.workspace.config import UserConfig, WorkspaceConfig
from mas.runtime.spec.schema_bindings_generated import INFRA_MIDDLEWARE_PATH_PARAM_KEYS
from mas.runtime.spec.source import load_yaml_file, resolve_ref_with_search
from mas.runtime.xdg import mas_infra_dir, mas_runtime_dir

logger = logging.getLogger(__name__)


class InfraResolveError(RuntimeError):
    """One or more infra refs or interceptors failed to load."""

    def __init__(self, errors: list[tuple[str, Exception]]) -> None:
        self.errors = list(errors)
        parts = [f"{ref!r}: {exc}" for ref, exc in self.errors]
        super().__init__("infra resolution failed: " + "; ".join(parts))


_LEAF_KINDS = {
    "Infrastructure",
    "LLMProxy",
    "LLMLocal",
    "InfraMiddleware",
    "InfraInterceptor",
    "ToolServerRegistry",
    "PersonalSecrets",
    "Application",
    "ToolRegistry",
    "ToolProvider",
    "SecretsProvider",
    "Datastore",
    "RuntimeEngine",
}
_VALID_KINDS = _LEAF_KINDS | {"InfraBundle"}

_APPLICATION_USAGES = {"use", "deploy", "use-and-deploy"}


def application_endpoint_usage(endpoint: dict[str, Any]) -> str:
    """Return the endpoint's explicit usage, preserving legacy expose behavior."""
    usage = endpoint.get("usage")
    if usage is None:
        return "use-and-deploy" if endpoint.get("expose") is True else "use"
    value = str(usage)
    if value not in _APPLICATION_USAGES:
        raise ValueError("Application endpoint usage must be 'use', 'deploy', or 'use-and-deploy'")
    return value


def application_endpoint_is_used(endpoint: dict[str, Any]) -> bool:
    return application_endpoint_usage(endpoint) in {"use", "use-and-deploy"}


def application_endpoint_is_deployed(endpoint: dict[str, Any]) -> bool:
    return application_endpoint_usage(endpoint) in {"deploy", "use-and-deploy"}


def resolve_infra_refs(
    refs: list[str],
    *,
    overrides: list[str] | None = None,
    anchor: Path | None = None,
    workspace: WorkspaceConfig | None = None,
    user: UserConfig | None = None,
    interceptors: list[str] | None = None,
    runtime_refs: list[str] | None = None,
) -> ResolvedInfra:
    """Load and merge infra manifests; populate ``ResolvedInfra.llm_proxy``."""
    from mas.ctl.workspace.config import (
        merge_infra_interceptors,
        merge_runtime_refs,
    )
    from mas.runtime.spec.runtime_engine import merge_runtime_engine_layers

    ws = workspace or WorkspaceConfig.load(anchor)
    usr = user or UserConfig.load()
    effective = list(refs)
    if not effective:
        if ws.effective_infra_refs:
            effective = list(ws.effective_infra_refs)
        elif usr.default_infra:
            effective = [usr.default_infra]
        else:
            effective = ["standard:production"]

    root = resolution_anchor(anchor, ws)

    merged = InfraManifest(name="merged")
    errors: list[tuple[str, Exception]] = []
    implicit_refs = ["standard:local-tools"]
    refs_to_load = implicit_refs + [ref for ref in effective if ref not in implicit_refs]
    for ref in refs_to_load:
        try:
            part = _load_ref(ref, anchor=root, workspace=ws)
            merged = _merge(merged, part)
        except Exception as exc:
            errors.append((ref, exc))

    merged_interceptors = merge_infra_interceptors(
        workspace_interceptors=list(ws.infra_interceptors),
        cli_interceptors=list(interceptors or []),
    )
    for ref in merged_interceptors:
        try:
            part = _load_ref(ref, anchor=root, workspace=ws)
            merged.pipeline = _merge_pipeline(merged.pipeline, part.pipeline)
        except Exception as exc:
            errors.append((ref, exc))

    runtime_effective = merge_runtime_refs(
        workspace_refs=ws.effective_runtime_refs,
        user_refs=[usr.default_runtime] if usr.default_runtime else [],
        cli_refs=list(runtime_refs or []),
        workspace_found=ws.found,
    )
    runtime_engine: dict[str, Any] = dict(merged.runtime_engine)
    for ref in runtime_effective:
        try:
            part = _load_runtime_ref(ref, anchor=root, workspace=ws)
            runtime_engine = merge_runtime_engine_layers(runtime_engine, part.runtime_engine)
        except Exception as exc:
            errors.append((ref, exc))

    merged.runtime_engine = runtime_engine
    if overrides:
        from mas.ctl.overrides import apply_cli_overrides
        from mas.ctl.validate import validate_data

        merged_document = apply_cli_overrides(merged.to_document(), tuple(overrides), root="infra")
        validate_data(merged_document, source="CLI overrides", kind="infra").raise_if_failed()
        merged = InfraManifest.from_document(merged_document)
        for entry in merged.pipeline:
            entry["params"] = _resolve_pipeline_param_paths(dict(entry.get("params") or {}), anchor=root)
        runtime_engine = dict(merged.runtime_engine)

    if errors:
        for ref, exc in errors:
            logger.error("infra ref %r failed: %s", ref, exc)
        raise InfraResolveError(errors)

    llm = merged.to_llm_proxy_dict()
    cache_path = usr.cache_dir / "llm_cache.json"
    llm["cache_path"] = str(cache_path)
    llm["pipeline"] = _filter_pipeline_for_target(llm.get("pipeline") or [], "LLM_CALL")
    _hydrate_pipeline_cache_paths(llm.get("pipeline") or [], cache_path=cache_path)

    tool_server_registry = {
        str(item["id"]): dict(item) for item in merged.tool_servers if isinstance(item, dict) and item.get("id")
    }

    return ResolvedInfra(
        refs=effective,
        llm_proxy=llm,
        tool_server_registry=tool_server_registry,
        observability={},
        runtime_engine=dict(runtime_engine),
        runtime_refs=list(runtime_effective),
        applications=dict(merged.applications),
    )


def bidirectional_pipeline_for(
    llm_proxy: dict[str, Any],
    *,
    handlers: dict[str, Any] | None = None,
) -> BidirectionalInfraPipeline:
    """Build a bidirectional infra chain from a resolved ``llm_proxy`` dict."""
    steps = list(llm_proxy.get("pipeline") or [])
    return BidirectionalInfraPipeline.from_pipeline_steps(steps, handlers=handlers)


def bidirectional_pipeline_for_infra(
    infra: ResolvedInfra,
    *,
    handlers: dict[str, Any] | None = None,
) -> BidirectionalInfraPipeline:
    """Convenience wrapper for :func:`bidirectional_pipeline_for`."""
    return bidirectional_pipeline_for(infra.llm_proxy, handlers=handlers)


def _load_runtime_ref(ref: str, *, anchor: Path, workspace: WorkspaceConfig) -> InfraManifest:
    path = _resolve_runtime_ref_path(ref, anchor=anchor, workspace=workspace)
    manifest = _load_file(path, workspace=workspace, allow_runtime_engine=True)
    if manifest.kind != "RuntimeEngine":
        raise ValueError(f"{path}: expected kind RuntimeEngine, got {manifest.kind!r}")
    return manifest


def _load_ref(ref: str, *, anchor: Path, workspace: WorkspaceConfig) -> InfraManifest:
    if ref.startswith(("http://", "https://")):
        is_local = "localhost" in ref or "127.0.0.1" in ref
        return InfraManifest(
            name=f"inline:{ref}",
            kind="LLMLocal" if is_local else "LLMProxy",
            proxy=ProxySpec(api_base=ref),
        )

    path = _resolve_ref_path(ref, anchor=anchor, workspace=workspace)
    return _load_file(path, workspace=workspace)


def resolution_anchor(anchor: Path | None, workspace: WorkspaceConfig) -> Path:
    """Directory used to resolve relative infra refs (never the process CWD alone)."""
    if anchor is not None:
        return Path(anchor).expanduser().resolve()
    if workspace.root:
        return workspace.root.resolve()
    raise InfraResolveError(
        [
            (
                "<anchor>",
                ValueError(
                    "infra resolution requires anchor= (MAS or agent app root) or a "
                    "workspace with a discovered root (config.yaml); relative refs are "
                    "not resolved from the process working directory"
                ),
            )
        ]
    )


def _resolve_ref_path(ref: str, *, anchor: Path, workspace: WorkspaceConfig) -> Path:
    if ":" in ref and "://" not in ref:
        lib_path = workspace.resolve_library_path(ref)
        if lib_path is not None:
            return lib_path
        bundle_path = _entry_point_bundle_path(ref)
        if bundle_path is not None:
            return bundle_path

    candidate = Path(ref).expanduser()
    if candidate.is_absolute() and candidate.is_file():
        return candidate.resolve()

    # Relative refs are resolved from the manifest/workspace anchor only.
    anchored = anchor / candidate
    if anchored.is_file():
        return anchored.resolve()

    return resolve_ref_with_search(
        ref,
        anchor,
        search_dirs=_infra_search_dirs(anchor, workspace),
    )


def _resolve_runtime_ref_path(ref: str, *, anchor: Path, workspace: WorkspaceConfig) -> Path:
    if ":" in ref and "://" not in ref:
        lib_path = workspace.resolve_library_path(ref)
        if lib_path is not None:
            return lib_path
        bundle_path = _entry_point_bundle_path(ref)
        if bundle_path is not None:
            return bundle_path

    candidate = Path(ref).expanduser()
    if candidate.is_absolute() and candidate.is_file():
        return candidate.resolve()

    anchored = anchor / candidate
    if anchored.is_file():
        return anchored.resolve()

    return resolve_ref_with_search(
        ref,
        anchor,
        search_dirs=_runtime_search_dirs(anchor, workspace),
    )


def _runtime_search_dirs(anchor: Path, workspace: WorkspaceConfig) -> list[Path]:
    dirs: list[Path] = []
    if workspace.root:
        dirs.append(workspace.root)
        dirs.append(workspace.root / "runtime")
        dirs.append(workspace.root / "config" / "runtime")
    dirs.extend(
        [
            anchor,
            anchor.parent,
            mas_runtime_dir(),
        ]
    )
    return dirs


def _infra_search_dirs(anchor: Path, workspace: WorkspaceConfig) -> list[Path]:
    dirs: list[Path] = []
    if workspace.root:
        # Workspace refs like ``standard:openai`` resolve from workspace root.
        dirs.append(workspace.root)
        dirs.append(workspace.root / "infra")
        dirs.append(workspace.root / "config" / "infra")
    dirs.extend(
        [
            anchor,
            anchor.parent,
            mas_infra_dir(),
        ]
    )
    return dirs


def _entry_point_bundle_path(ref: str) -> Path | None:
    if ":" not in ref:
        return None
    lib_name, bundle_name = ref.split(":", 1)
    libs = list_manifest_libraries()
    pkg = libs.get(lib_name)
    if pkg:
        path = _bundle_path_in_package(pkg, lib_name, bundle_name)
        if path is not None:
            return path

    mas_lab = os.environ.get("MAS_LAB_ROOT")
    if mas_lab:
        rel = bundle_name.replace(":", "/")
        for root in (
            Path(mas_lab) / "library-standard" / "src" / "mas" / "library" / "standard" / "libs" / lib_name,
            Path(mas_lab) / "library-standard" / "libs" / lib_name,
        ):
            for candidate in (root / f"{rel}.yaml", root / rel / "bundle.yaml"):
                if candidate.is_file():
                    return candidate.resolve()
    return None


def _bundle_path_in_package(pkg: str, lib_name: str, bundle_name: str) -> Path | None:
    try:
        from importlib.resources import as_file, files

        rel = bundle_name.replace(":", "/")
        bundle_path = files(pkg) / "libs" / lib_name / f"{rel}.yaml"
        with as_file(bundle_path) as p:
            path = Path(p)
            if path.is_file():
                return path.resolve()
    except Exception:
        return None
    return None


_RUNTIME_ENGINE_SPEC_KEYS = frozenset({"engine", "cache", "stream", "parallel"})
_RUNTIME_ENGINE_CACHE_KEYS = frozenset({"enabled", "read", "write"})
_RUNTIME_ENGINE_ENGINE_KEYS = frozenset({"queue_depth", "max_auto_steps", "timeout"})


def _validate_runtime_engine_spec(spec: dict[str, Any]) -> None:
    for key in spec:
        if key not in _RUNTIME_ENGINE_SPEC_KEYS:
            raise ValueError(f"RuntimeEngine spec: unknown field {key!r}")
    engine = spec.get("engine")
    if engine is not None:
        if not isinstance(engine, dict):
            raise ValueError("RuntimeEngine spec.engine must be an object")
        for key in engine:
            if key not in _RUNTIME_ENGINE_ENGINE_KEYS:
                raise ValueError(f"RuntimeEngine spec.engine: unknown field {key!r}")
    for block, allowed in (("cache", _RUNTIME_ENGINE_CACHE_KEYS),):
        raw = spec.get(block)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            raise ValueError(f"RuntimeEngine spec.{block} must be an object")
        for key in raw:
            if key not in allowed:
                raise ValueError(f"RuntimeEngine spec.{block}: unknown field {key!r}")


def _load_file(
    path: Path,
    *,
    workspace: WorkspaceConfig,
    _seen: frozenset[Path] | None = None,
    allow_runtime_engine: bool = False,
) -> InfraManifest:
    path = path.resolve()
    seen = (_seen or frozenset()) | {path}
    data = load_yaml_file(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected mapping")
    data = resolve_manifest_values(data)
    kind = data.get("kind", "")
    if kind not in _VALID_KINDS:
        raise ValueError(f"{path}: unsupported kind {kind!r}")

    if kind == "InfraBundle":
        parts: list[InfraManifest] = []
        spec = data.get("spec") or {}
        pipeline: list[dict[str, Any]] = []
        entries = spec.get("entries")
        if isinstance(entries, list) and entries:
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                ref = entry.get("ref")
                if not ref:
                    continue
                child = _resolve_ref_path(str(ref), anchor=path.parent, workspace=workspace)
                if child in seen:
                    raise ValueError(f"circular InfraBundle reference: {path} -> {child}")
                parts.append(
                    _load_file(child, workspace=workspace, _seen=seen, allow_runtime_engine=allow_runtime_engine)
                )
                for step in entry.get("pipeline") or []:
                    pipeline.append(_resolve_pipeline_entry(step, anchor=path.parent, workspace=workspace))
        else:
            for entry in spec.get("interceptors") or spec.get("pipeline") or []:
                pipeline.append(_resolve_pipeline_entry(entry, anchor=path.parent, workspace=workspace))
            for ref in spec.get("includes", []):
                child = _resolve_ref_path(str(ref), anchor=path.parent, workspace=workspace)
                if child in seen:
                    raise ValueError(f"circular InfraBundle reference: {path} -> {child}")
                parts.append(
                    _load_file(child, workspace=workspace, _seen=seen, allow_runtime_engine=allow_runtime_engine)
                )
        merged = _merge_many(parts)
        merged.pipeline = _merge_pipeline(merged.pipeline, pipeline)
        meta = data.get("metadata") or {}
        merged.name = meta.get("name", path.stem)
        merged.kind = "InfraBundle"
        return merged

    if kind == "RuntimeEngine":
        if not allow_runtime_engine:
            raise ValueError(
                f"{path}: RuntimeEngine must be listed in workspace runtime_refs, "
                "$XDG_CONFIG_HOME/mas/runtime/, or --runtime-ref — not infra_refs"
            )
        from mas.runtime.spec.runtime_engine import normalize_runtime_engine_spec

        spec = data.get("spec") or {}
        _validate_runtime_engine_spec(spec if isinstance(spec, dict) else {})
        return InfraManifest(
            name=str((data.get("metadata") or {}).get("name", path.stem)),
            kind=kind,
            runtime_engine=normalize_runtime_engine_spec(spec if isinstance(spec, dict) else {}),
            raw=data,
        )

    if kind in {"InfraMiddleware", "InfraInterceptor"}:
        spec = data.get("spec") or {}
        applies = spec.get("applies_to") or ["LLM_CALL"]
        if isinstance(applies, str):
            applies = [applies]
        params = _resolve_pipeline_param_paths(dict(spec.get("params") or {}), anchor=path.parent)
        return InfraManifest(
            name=str((data.get("metadata") or {}).get("name", path.stem)),
            kind=kind,
            pipeline=[
                {
                    "middleware": spec.get("middleware") or path.stem.replace("-", "_"),
                    "params": params,
                    "applies_to": list(applies),
                }
            ],
            raw=data,
        )

    return InfraManifest.from_document(data)


def _merge(a: InfraManifest, b: InfraManifest) -> InfraManifest:
    return _merge_many([a, b])


def _merge_many(parts: list[InfraManifest]) -> InfraManifest:
    if not parts:
        return InfraManifest(name="empty")
    proxy = ProxySpec()
    allowed: list[str] = []
    seen_allowed: set[str] = set()
    mappings: dict[str, str] = {}
    generation: dict[str, Any] = {}
    default_llm: str | None = None
    default_embed: str | None = None
    model_access: dict[str, Any] = {}
    protocol = ""
    applications: dict[str, dict[str, Any]] = {}
    pipeline: list[dict[str, Any]] = []
    runtime_engine: dict[str, Any] = {}
    tool_servers_by_id: dict[str, dict[str, Any]] = {}
    tool_server_order: list[str] = []
    name = parts[-1].name

    from mas.runtime.spec.runtime_engine import merge_runtime_engine_layers

    for m in parts:
        if m.runtime_engine:
            runtime_engine = merge_runtime_engine_layers(runtime_engine, m.runtime_engine)
        if m.proxy.api_base:
            proxy = ProxySpec(api_base=m.proxy.api_base, api_key_env=m.proxy.api_key_env, timeout=m.proxy.timeout)
        elif m.proxy.timeout is not None:
            proxy = ProxySpec(api_base=proxy.api_base, api_key_env=proxy.api_key_env, timeout=m.proxy.timeout)
        pipeline = _merge_pipeline(pipeline, m.pipeline)
        for item in m.models.allowed:
            if item not in seen_allowed:
                seen_allowed.add(item)
                allowed.append(item)
        mappings.update(m.models.mappings)
        generation.update(m.models.generation)
        if m.models.default_llm:
            default_llm = m.models.default_llm
        if m.models.default_embed:
            default_embed = m.models.default_embed
        model_access.update(m.model_access)
        if m.protocol:
            protocol = m.protocol
        for server in m.tool_servers:
            sid = str(server.get("id") or "").strip()
            if not sid:
                continue
            if sid not in tool_servers_by_id:
                tool_server_order.append(sid)
            previous = tool_servers_by_id.get(sid) or {}
            tool_servers_by_id[sid] = {**previous, **server}
        applications.update(m.applications)

    return InfraManifest(
        name=name,
        kind="InfraBundle" if len(parts) > 1 else parts[0].kind,
        proxy=proxy,
        models=ModelsSpec(
            allowed=allowed,
            default_llm=default_llm,
            default_embed=default_embed,
            mappings=mappings,
            generation=generation,
        ),
        model_access=model_access,
        protocol=protocol,
        pipeline=pipeline,
        runtime_engine=runtime_engine,
        tool_servers=[tool_servers_by_id[sid] for sid in tool_server_order],
        applications=applications,
    )


def _resolve_pipeline_param_paths(params: dict[str, Any], *, anchor: Path) -> dict[str, Any]:
    out = dict(params)
    for key in INFRA_MIDDLEWARE_PATH_PARAM_KEYS:
        value = out.get(key)
        if not isinstance(value, str):
            continue
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = (anchor / candidate).resolve()
        out[key] = str(candidate)
    return out


def _resolve_pipeline_entry(
    entry: Any,
    *,
    anchor: Path,
    workspace: WorkspaceConfig,
) -> dict[str, Any]:
    if isinstance(entry, str):
        child_path = _resolve_ref_path(entry, anchor=anchor, workspace=workspace)
        loaded = _load_file(child_path, workspace=workspace)
        if loaded.pipeline:
            step = dict(loaded.pipeline[0])
            step["params"] = _resolve_pipeline_param_paths(
                dict(step.get("params") or {}),
                anchor=child_path.parent,
            )
            return step
        return {"middleware": entry.split(":")[-1].replace("-", "_"), "params": {}}
    if isinstance(entry, dict):
        if "ref" in entry:
            return _resolve_pipeline_entry(str(entry["ref"]), anchor=anchor, workspace=workspace)
        mid = entry.get("middleware") or entry.get("id")
        params = _resolve_pipeline_param_paths(dict(entry.get("params") or {}), anchor=anchor)
        return {"middleware": mid, "params": params}
    raise ValueError(f"invalid pipeline entry: {entry!r}")


def _merge_pipeline(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(a) + list(b)


def _filter_pipeline_for_target(pipeline: list[dict[str, Any]], target: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in pipeline:
        applies = entry.get("applies_to") or ["LLM_CALL"]
        if isinstance(applies, str):
            applies = [applies]
        if target in applies:
            out.append(entry)
    return out


def _hydrate_pipeline_cache_paths(pipeline: list[dict[str, Any]], *, cache_path: Path) -> None:
    for entry in pipeline:
        mid = str(entry.get("middleware") or "")
        if mid not in {"llm_cache", "llm-cache"}:
            continue
        params = entry.setdefault("params", {})
        if not params.get("cache_path"):
            params["cache_path"] = str(cache_path)


def api_key_for_infra(llm_proxy: dict[str, Any]) -> str:
    """Read secret from env var named by the infra manifest ``api_key_env`` field."""
    env_name = llm_proxy.get("api_key_env") or "OPENAI_API_KEY"
    return os.environ.get(str(env_name), "").strip()
