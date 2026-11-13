#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Select EngineContract implementation from manifest + resolved infra."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.infra.resolve import api_key_for_infra, resolution_anchor, resolve_infra_refs
from mas.ctl.session.manifest_config import engine_use_tool_loop, kernel_config_from_manifest
from mas.ctl.workspace.config import UserConfig, WorkspaceConfig, merge_infra_refs
from mas.runtime.agent_defaults import default_pattern_plugin_id
from mas.runtime.driver.mocks import AutoCtxAssembler
from mas.runtime.engine.infra_pipeline import filter_pipeline_for_kind, split_pipeline_layers
from mas.runtime.engine.llm_cache import resolve_cache_path
from mas.runtime.engine.llm_live import LiveLlmEngine
from mas.runtime.engine.llm_output_limits import resolve_output_limits
from mas.runtime.engine.llm_reasoning import reasoning_settings_from_manifest
from mas.runtime.engine.llm_request import model_entry_from_manifest
from mas.runtime.engine.tool_workers import ToolPlacement
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.spec.model_ref import concrete_model, first_concrete, primary_model_binding

logger = logging.getLogger(__name__)

_LLM_SPEC_DEPRECATION = "spec.llm is deprecated; declare model settings under spec.models[] instead"


def _primary_model_entry(spec: dict[str, Any], *, model: str | None = None) -> dict[str, Any] | None:
    if model:
        return model_entry_from_manifest({"spec": spec}, model=model)
    entry = primary_model_binding({"spec": spec} if spec else {})
    return entry or model_entry_from_manifest({"spec": spec}, model=model)


def _warn_llm_spec_fallback(field: str) -> None:
    logger.warning("%s (read %s from spec.llm)", _LLM_SPEC_DEPRECATION, field)


@dataclass(frozen=True)
class EngineSelection:
    engine: Any
    mode: str  # live | replay
    reason: str = ""


def _strict_replay(llm_proxy: dict[str, Any] | None) -> bool:
    """True when an llm_cache pipeline step will error on miss (offline replay)."""
    from mas.runtime.engine.infra_pipeline import is_cache_middleware

    for step in (llm_proxy or {}).get("pipeline") or []:
        if not isinstance(step, dict) or not is_cache_middleware(step):
            continue
        params = step.get("params") or {}
        if params.get("raise_on_miss") is True:
            return True
    return False


def _require_leaf_credentials(
    llm_proxy: dict[str, Any],
    llm_proxies: dict[str, dict[str, Any]],
    *,
    strict_replay: bool,
) -> None:
    """Each leaf resolves its own key; the engine never forwards the first leaf's."""
    if strict_replay:
        return
    leaves = dict(llm_proxies or {})
    if not leaves and llm_proxy.get("api_base"):
        leaves = {"default": llm_proxy}
    missing: list[str] = []
    for name, leaf in leaves.items():
        if not str((leaf or {}).get("api_base") or "").strip():
            continue
        env_name = str((leaf or {}).get("api_key_env") or "OPENAI_API_KEY")
        key = api_key_for_infra(leaf)
        if not key:
            missing.append(f"{env_name} (leaf {name})")
            continue
        if not key.isascii():
            raise RuntimeError(
                f"{env_name} contains characters that cannot be in an API key (non-ASCII). "
                "Did you paste a placeholder such as '…' instead of your key?"
            )
    if missing:
        raise RuntimeError(
            "Live LLM configured but API key env unset: "
            + ", ".join(missing)
            + ". Set the key, or replay recorded answers only with "
            "--cache-read-only (or an llm_cache fixture with raise_on_miss: true)."
        )


class UnresolvedModelError(RuntimeError):
    """spec.models is any/omitted and nothing explicit pinned a provider id."""


_NO_LLM_PATTERNS = frozenset({"scripted_response"})


def _design_pattern_base(manifest: dict | None, pattern_plugin_id: str | None = None) -> str:
    spec = (manifest or {}).get("spec") or {}
    from mas.runtime.spec.plugin_binding import plugin_binding_id

    from_manifest = plugin_binding_id(spec.get("design_pattern"), field="spec.design_pattern")
    raw = from_manifest or pattern_plugin_id or ""
    return str(raw).split("@", 1)[0].strip().lower()


def _requires_concrete_model(manifest: dict | None, pattern_plugin_id: str | None = None) -> bool:
    """False for patterns that never call an LLM (e.g. scripted_response)."""
    return _design_pattern_base(manifest, pattern_plugin_id) not in _NO_LLM_PATTERNS


def resolve_model_name(
    manifest: dict | None,
    infra: ResolvedInfra | None,
    *,
    workspace_default: str | None = None,
    forced: str | None = None,
    parent_default: str | None = None,
    experiment_default: str | None = None,
) -> str:
    """Resolve the turn-model LiteLLM id.

    Precedence: CLI ``--model`` → last-resort env override → Agent
    ``spec.models[id=main]`` → MAS default → ``experiment.models.main`` /
    ``experiment.model`` → workspace ``config.yaml`` ``defaults.model``.
    An env override is logged, never silent.

    ``model: any`` (or an omitted pin) does **not** fall through to the
    package ``defaults.yaml`` value. If nothing explicit remains, this
    raises :class:`UnresolvedModelError` instead of substituting
    ``gpt-4o-mini``.
    """
    llm_proxy = (infra.llm_proxy if infra else {}) or {}
    from mas.ctl.env import env_model_override

    forced = (forced or "").strip()
    if not forced:
        forced = env_model_override() or ""
    if forced:
        raw = forced
    else:
        spec = (manifest or {}).get("spec") or {}
        entry = _primary_model_entry(spec)
        declared = concrete_model(entry.get("model") if entry else None)
        if not declared:
            llm = spec.get("llm") or {}
            if llm.get("model"):
                _warn_llm_spec_fallback("model")
                declared = concrete_model(llm.get("model"))
        raw, _source = first_concrete(
            (declared, "spec.models"),
            (parent_default, "mas.spec.models"),
            (experiment_default, "experiment.model"),
            (workspace_default, "config.defaults.model"),
        )
        if not raw:
            raise UnresolvedModelError(
                "No concrete LLM model: spec.models is 'any' or omitted and "
                "nothing pinned it. Set spec.models[].model, experiment.models.main, "
                "config.yaml defaults.model, or pass --model / MAS_CTL_MODEL. "
                "mas-lab will not substitute gpt-4o-mini."
            )
    leaves = (infra.llm_proxies if infra is not None else None) or {}
    if leaves:
        from mas.runtime.registry.llm_provider_registry import (
            canonical_model_name,
            leaf_for_model,
        )

        leaf = leaf_for_model(raw, leaves, default=llm_proxy)
        return canonical_model_name(raw, leaf)
    mappings = llm_proxy.get("mappings") or {}
    return str(mappings.get(raw, raw))


def _resolve_sampling_param(manifest: dict | None, key: str, default: float, *, model: str | None = None) -> float:
    """Read a sampling param from the matching ``spec.models[]`` row, then ``spec.llm``."""
    spec = (manifest or {}).get("spec") or {}
    entry = _primary_model_entry(spec, model=model)
    if entry and key in entry:
        return float(entry[key])
    llm = spec.get("llm") or {}
    if key in llm:
        _warn_llm_spec_fallback(key)
        return float(llm[key])
    from mas.runtime.engine.llm_model_catalog import default_model_catalog

    name = model or (entry or {}).get("model")
    info = default_model_catalog().get(str(name) if name else None)
    if info is not None and key in info.defaults:
        return float(info.defaults[key])
    return default


def _resolve_model_option(manifest: dict | None, key: str, *, model: str | None = None) -> str | None:
    """Read a string model option from the matching ``spec.models[]`` row, then ``spec.llm``."""
    spec = (manifest or {}).get("spec") or {}
    entry = _primary_model_entry(spec, model=model)
    value = entry.get(key) if entry else None
    if value is None:
        llm = spec.get("llm") or {}
        value = llm.get(key)
        if value is not None:
            _warn_llm_spec_fallback(key)
    return str(value) if value else None


def _resolve_infra_for_engine(
    manifest: dict | None,
    infra: ResolvedInfra | None,
    *,
    anchor: Path,
    workspace: WorkspaceConfig | None = None,
    runtime_refs_cli: list[str] | None = None,
) -> ResolvedInfra:
    if infra is not None and infra.llm_proxy:
        return infra
    ws = workspace or WorkspaceConfig.load(anchor)
    user = UserConfig.load()
    merged_refs = merge_infra_refs(
        workspace_refs=ws.effective_infra_refs,
        user_refs=[user.default_infra] if user.default_infra else [],
        cli_refs=[],
        workspace_found=ws.found,
    )
    if not merged_refs:
        return infra or ResolvedInfra(refs=[], llm_proxy={})
    return resolve_infra_refs(
        merged_refs,
        anchor=anchor,
        workspace=ws,
        user=user,
        runtime_refs=list(runtime_refs_cli or []),
    )


def build_engine(
    ctx: AutoCtxAssembler,
    manifest: dict | None,
    infra: ResolvedInfra | None,
    *,
    pattern_plugin_id: str | None = None,
    workspace_default_model: str | None = None,
    parent_default_model: str | None = None,
    experiment_default_model: str | None = None,
    anchor: Path | None = None,
    workspace: WorkspaceConfig | None = None,
    kernel_config: KernelConfig | None = None,
    cache_read_override: bool | None = None,
    cache_write_override: bool | None = None,
    cache_read_only: bool = False,
    stream_override: bool | None = None,
    runtime_refs_cli: list[str] | None = None,
    model_override: str | None = None,
    flavour_spec: dict | None = None,
    agent_id: str = "",
    entry_id: str = "",
    delegate_ids: list[str] | tuple[str, ...] = (),
) -> EngineSelection:
    pid = pattern_plugin_id or default_pattern_plugin_id()
    if not _requires_concrete_model(manifest, pattern_plugin_id):
        from mas.runtime.engine.simulated import SimulatedEngine

        return EngineSelection(
            engine=SimulatedEngine(),
            mode="scripted",
            reason="design_pattern scripted_response",
        )
    kernel_cfg = (
        kernel_config
        if kernel_config is not None
        else kernel_config_from_manifest(
            manifest,
            pattern_plugin_id=pid,
            flavour_spec=flavour_spec,
            agent_id=agent_id,
            entry_id=entry_id,
            delegate_ids=delegate_ids,
        )
    )
    tool_loop = engine_use_tool_loop(manifest, kernel_cfg)
    ws = workspace or WorkspaceConfig.load(anchor)
    ref_anchor = resolution_anchor(anchor, ws)

    resolved = _resolve_infra_for_engine(
        manifest,
        infra,
        anchor=ref_anchor,
        workspace=ws,
        runtime_refs_cli=runtime_refs_cli,
    )
    llm_proxy = dict(resolved.llm_proxy or {})
    spec = (manifest or {}).get("spec") or {}
    reliability = getattr(kernel_cfg, "reliability", None)
    if reliability is None:
        from mas.runtime.reliability.policy import ReliabilitySettings

        reliability = ReliabilitySettings.from_spec(spec)
    from mas.ctl.session.llm_retry_env import llm_http_retry_overrides

    retry_overlay = llm_http_retry_overrides() or None
    if cache_read_only:
        # Exclusive replay: answer from the cache only, never write, never call the provider.
        cache_read_override, cache_write_override = True, False
    strict_replay = _strict_replay(llm_proxy) or cache_read_only

    infra_base = str(llm_proxy.get("api_base") or "").strip()
    env_base = _env_llm_base()
    api_base = infra_base or env_base
    if not infra_base and env_base:
        from mas.ctl.infra.env_resolve import warn_endpoint_from_env

        warn_endpoint_from_env(
            "OPENAI_BASE_URL" if os.environ.get("OPENAI_BASE_URL", "").strip() else "OPENAI_API_BASE", env_base
        )
    if infra_base and env_base and infra_base.rstrip("/") != env_base.rstrip("/"):
        logger.warning(
            "OPENAI_BASE_URL (%s) is ignored: the LLMProxy infra manifest sets api_base %s. "
            "Edit proxy.api_base there, or drop it to use the environment variable.",
            env_base,
            infra_base,
        )
    api_key_env = str(llm_proxy.get("api_key_env") or "OPENAI_API_KEY")
    http_timeout = float(llm_proxy["timeout"]) if llm_proxy.get("timeout") is not None else None

    if not api_base and not strict_replay:
        raise RuntimeError(
            "No LLM configured: set proxy.api_base on an LLMProxy infra manifest "
            "(workspace infra_refs or --infra-ref), or set OPENAI_BASE_URL. "
            "For offline CI, attach llm_cache replay (raise_on_miss) recorded against a live provider."
        )
    cache_raw = llm_proxy.get("cache_path")
    runtime_engine = dict(resolved.runtime_engine or {})
    cache_read = _cache_read_enabled(runtime_engine, override=cache_read_override)
    cache_write = _cache_write_enabled(runtime_engine, override=cache_write_override)
    llm_proxies = getattr(resolved, "llm_proxies", None) or {}
    if env_base and not infra_base:
        # The provider is built from these dicts, not from ``api_base`` above, so the
        # environment address has to be in them or the provider quietly uses OpenAI's.
        llm_proxy["api_base"] = env_base
        llm_proxies = {
            name: ({**leaf, "api_base": env_base} if not str((leaf or {}).get("api_base") or "").strip() else leaf)
            for name, leaf in llm_proxies.items()
        }
    cache_path = Path(str(cache_raw)) if cache_raw else resolve_cache_path()
    _ensure_llm_cache_pipeline(
        llm_proxy,
        cache_read=cache_read,
        cache_write=cache_write,
        cache_read_only=cache_read_only,
        cache_path=cache_path,
        cache_read_override=cache_read_override,
        cache_write_override=cache_write_override,
        cache_disabled=_cache_settings(runtime_engine).get("enabled") is False,
    )
    _require_leaf_credentials(llm_proxy, llm_proxies, strict_replay=strict_replay)
    if strict_replay:
        mode = "replay"
        reason = "cache read-only" if cache_read_only else "llm_cache raise_on_miss"
    else:
        mode = "live"
        reason = f"resolved infra → {api_base}"

    model = resolve_model_name(
        manifest,
        resolved,
        workspace_default=workspace_default_model,
        forced=model_override,
        parent_default=parent_default_model,
        experiment_default=experiment_default_model,
    )
    stream = _stream_enabled(runtime_engine, override=stream_override)

    from mas.runtime.registry.llm_provider_registry import llm_provider_from_infra

    reasoning = reasoning_settings_from_manifest(manifest, model=model)
    llm_provider = llm_provider_from_infra(
        llm_proxy,
        llm_proxies=llm_proxies or None,
        manifest=manifest,
        stream=stream,
        reasoning_effort=reasoning.effort,
        reasoning=reasoning.to_spec_dict(),
        wrap_cache=False,
        retry_overlay=retry_overlay,
    )
    engine = LiveLlmEngine(
        ctx=ctx,
        manifest=manifest,
        api_base=api_base or "https://api.openai.com/v1",
        api_key_env=api_key_env,
        model=model,
        temperature=_resolve_sampling_param(manifest, "temperature", 0.7, model=model),
        output_limits=resolve_output_limits(
            manifest,
            model=model,
            generation=llm_proxy.get("generation"),
        ),
        reasoning_effort=reasoning.effort,
        reasoning=reasoning.to_spec_dict(),
        cache_path=None,
        use_cache=False,
        cache_read=False,
        cache_write=False,
        stream=stream,
        use_tool_loop=tool_loop,
        tool_placement=ToolPlacement(worker=kernel_cfg.tool_worker, concurrency=kernel_cfg.tool_concurrency),
        llm_proxy=llm_proxy,
        llm_proxies=llm_proxies or None,
        http_timeout=http_timeout,
        llm_provider=llm_provider,
        reliability=reliability,
    )
    from mas.runtime.layers.control import resolve_call_pipeline

    common, _destination = split_pipeline_layers(llm_proxy.get("pipeline") or [])
    engine.install_infra_pipeline(filter_pipeline_for_kind(common, "llm"))
    tool_steps = resolve_call_pipeline(spec, list(resolved.tool_pipeline or []), kind="tool")
    tool_common, _tool_dest = split_pipeline_layers(tool_steps)
    engine.install_tool_pipeline(tool_common, destinations=resolved.tool_server_registry)
    return EngineSelection(engine=engine, mode=mode, reason=reason)


def _llm_cache_step(pipeline: list[Any]) -> dict[str, Any] | None:
    from mas.runtime.engine.infra_pipeline import is_cache_middleware

    for step in pipeline:
        if isinstance(step, dict) and is_cache_middleware(step):
            return step
    return None


def _ensure_llm_cache_pipeline(
    llm_proxy: dict[str, Any],
    *,
    cache_read: bool,
    cache_write: bool,
    cache_read_only: bool,
    cache_path: Path | None,
    cache_read_override: bool | None,
    cache_write_override: bool | None,
    cache_disabled: bool = False,
) -> None:
    """Put cache policy on the ``llm_cache`` infra step, not a provider wrap.

    ``--cache-read`` / ``--cache-write`` / ``--cache-read-only`` overlay an
    existing step. If there is no step and those flags (or RuntimeEngine
    cache) ask for a cache, one is inserted. A step already in infra is left
    as written when the CLI did not override it, except that RuntimeEngine
    ``cache.enabled: false`` is a kill-switch and turns both flags off.
    """
    pipeline = [dict(step) if isinstance(step, dict) else step for step in (llm_proxy.get("pipeline") or [])]
    step = _llm_cache_step(pipeline)
    if step is None:
        if not (cache_read or cache_write or cache_read_only):
            return
        params: dict[str, Any] = {
            "allow_read": True if cache_read_only else cache_read,
            "allow_write": False if cache_read_only else cache_write,
            "raise_on_miss": bool(cache_read_only),
        }
        if cache_path is not None:
            params["cache_path"] = str(cache_path)
        pipeline.insert(
            0,
            {"middleware": "llm_cache", "for": ["llm"], "per": "all", "params": params},
        )
        llm_proxy["pipeline"] = pipeline
        return
    params = dict(step.get("params") or {})
    if cache_disabled:
        params["allow_read"] = False
        params["allow_write"] = False
    if cache_read_override is not None:
        params["allow_read"] = cache_read_override
    if cache_write_override is not None:
        params["allow_write"] = cache_write_override
    if cache_read_only:
        params["allow_read"] = True
        params["allow_write"] = False
        params["raise_on_miss"] = True
    step["params"] = params
    llm_proxy["pipeline"] = pipeline


def _bool_env(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_llm_base() -> str:
    """OpenAI SDK ``OPENAI_BASE_URL``, then the older ``OPENAI_API_BASE`` name."""
    for name in ("OPENAI_BASE_URL", "OPENAI_API_BASE"):
        raw = os.environ.get(name)
        if raw and raw.strip():
            return raw.strip()
    return ""


def _cache_settings(runtime_engine: dict[str, Any]) -> dict[str, Any]:
    cache = (runtime_engine or {}).get("cache") or {}
    return cache if isinstance(cache, dict) else {}


def _cache_read_enabled(
    runtime_engine: dict[str, Any],
    *,
    override: bool | None = None,
) -> bool:
    if override is not None:
        return override
    cache = _cache_settings(runtime_engine)
    if cache.get("enabled") is False:
        return False
    if isinstance(cache.get("read"), bool):
        return cache["read"]
    env = _bool_env("MAS_LLM_CACHE_READ")
    if env is not None:
        return env
    return False


def _cache_write_enabled(
    runtime_engine: dict[str, Any],
    *,
    override: bool | None = None,
) -> bool:
    if override is not None:
        return override
    cache = _cache_settings(runtime_engine)
    if cache.get("enabled") is False:
        return False
    if isinstance(cache.get("write"), bool):
        return cache["write"]
    env = _bool_env("MAS_LLM_CACHE_WRITE")
    if env is not None:
        return env
    return False


def _stream_enabled(
    runtime_engine: dict[str, Any],
    *,
    override: bool | None = None,
) -> bool:
    if override is not None:
        return override
    if isinstance((runtime_engine or {}).get("stream"), bool):
        return bool(runtime_engine["stream"])
    env = _bool_env("MAS_LLM_STREAM")
    if env is not None:
        return env
    return False
