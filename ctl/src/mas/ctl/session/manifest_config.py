#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Resolve kernel + observability settings from merged manifests (spec-first)."""

from __future__ import annotations

from typing import Any

from mas.ctl.adapters.obs.config import ObservabilityConfig
from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.manifest.spec_bindings import (
    normalize_obs_plugin,
    parse_governance,
    parse_observability,
    parse_sink_from_deployment,
)
from mas.ctl.session.flavour_control import (
    control_lists_for_agent,
    matching_controls,
    resolve_controls,
)
from mas.ctl.session.flavour_control.spec import (
    GOVERNANCE,
    OBSERVABILITY,
    empty_list_silences_warning,
)
from mas.ctl.session.notices import warn_once
from mas.runtime.kernel.config import KernelConfig


# DEPRECATED: kernel_config_from_manifest is superseded by RuntimeInstance.from_spec()
# (runtime/src/mas/runtime/driver/instance.py). New callers should use from_spec() directly.
# This function is retained for backward compatibility and will be removed in a future release.
def spec_with_control_lists(
    spec: dict | None,
    flavour_spec: dict | None,
    *,
    agent_id: str = "",
    entry_id: str = "",
    delegate_ids: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Session view for parsers that read ``spec.governance``, and ``spec.observability``.

    Each list is the matching Controls' set when one names it; otherwise the Agent's leftover
    list stays in force. The agent document is not rewritten.
    """
    out = dict(spec or {})
    if not flavour_spec:
        return out
    rows = resolve_controls(flavour_spec)
    if not matching_controls(rows, agent_id=agent_id, entry_id=entry_id, delegate_ids=delegate_ids):
        return out
    lists = control_lists_for_agent(rows, agent_id=agent_id, entry_id=entry_id, delegate_ids=delegate_ids)
    for key in (OBSERVABILITY, GOVERNANCE):
        if key in lists:
            if (spec or {}).get(key) and not lists[key]:
                warn_once(empty_list_silences_warning(agent_id, key))
            out[key] = lists[key]
    return out


def kernel_config_from_manifest(
    manifest: dict | None,
    *,
    pattern_plugin_id: str | None = None,
    flavour_spec: dict | None = None,
    agent_id: str = "",
    entry_id: str = "",
    delegate_ids: list[str] | tuple[str, ...] = (),
) -> KernelConfig:
    from mas.runtime.spec.gov import build_kernel_config

    spec = spec_with_control_lists(
        (manifest or {}).get("spec") or {},
        flavour_spec,
        agent_id=agent_id,
        entry_id=entry_id,
        delegate_ids=delegate_ids,
    )
    gov = parse_governance(spec.get("governance"))

    # Ingress plugins are wired inside build_kernel_config from
    # binding.ingress_plugins. Do not pass a second spec list here — that
    # parameter was dead and looked like a double-wiring path.
    return build_kernel_config(
        gov,
        pattern_plugin_id=pattern_plugin_id or "",
        agent_spec=spec,
        agent_id=agent_id,
    )


def agent_id_from_manifest(manifest: dict | None) -> str:
    """``metadata.name`` on the loaded Agent or MAS document (schema field)."""
    meta = (manifest or {}).get("metadata") or {}
    return str(meta.get("name") or "").strip()


def require_agent_id_from_manifest(manifest: dict | None) -> str:
    """``metadata.name``, required when the loaded document is an Agent or MAS."""
    name = agent_id_from_manifest(manifest)
    kind = str((manifest or {}).get("kind") or "").strip().lower()
    if kind in {"agent", "mas"}:
        if not name:
            raise ValueError("metadata.name is required on the loaded manifest")
        if "/" in name or "\\" in name:
            raise ValueError(f"metadata.name must be a declared identity, not a filesystem path: {name!r}")
    return name


def mas_id_from_manifest(manifest: dict | None) -> str:
    """MAS identity is this document's ``metadata.name``.

    Both ``kind: MAS`` and ``kind: Agent`` declare identity only as
    ``metadata.name``. There is no ``metadata.mas_id`` or nested ``mas.name``
    in the schemas.
    """
    return agent_id_from_manifest(manifest)


def resolve_mas_id(manifest: dict | None, *, mas_id: str = "") -> str:
    """Explicit override, else ``metadata.name`` on *manifest*."""
    return mas_id or mas_id_from_manifest(manifest)


def derive_observability_format(
    plugins: list[str],
    *,
    cli_override: str | None = None,
) -> str:
    """Derive export format from manifest plugin list and optional CLI override."""
    if cli_override:
        return cli_override
    has_native = "native" in plugins
    has_otel = "otel" in plugins
    if has_native and has_otel:
        return "both"
    if has_otel:
        return "otel"
    return "native"


# What an Application row says about the endpoint itself. The rest of the row configures the sink's plugin.
_ROW_STRUCTURE = frozenset(
    {"protocol", "url", "identity", "secret_env", "usage", "expose", "headers", "timeout", "a2a"}
)


def _bind_events_raw(flavour_spec: dict | None, resolved_infra: ResolvedInfra | None) -> list[Any] | None:
    """``bind.events`` as an observability plugin list, or ``None`` when unset.

    Each name is an Application row and its ``protocol`` names the plugin. The row's keys that describe the
    endpoint (``url``, ``secret_env``, …) stay on infra; any other key is the plugin's own setting.
    """
    bind = (flavour_spec or {}).get("bind") if isinstance(flavour_spec, dict) else None
    names = bind.get("events") if isinstance(bind, dict) else None
    if not names:
        return None
    from mas.ctl.infra.bind import SECRET_KEY, BindError

    rows = (resolved_infra.applications or {}) if isinstance(resolved_infra, ResolvedInfra) else {}
    entries: list[Any] = []
    for name in names:
        row = rows.get(name)
        if not isinstance(row, dict):
            raise BindError(f"Flavour bind.events names {name!r}, which is no Application row")
        protocol = str(row.get("protocol") or "").strip()
        if not protocol:
            raise BindError(f"Application {name!r}: a row in bind.events needs protocol")
        config = {key: value for key, value in row.items() if key not in _ROW_STRUCTURE and not SECRET_KEY.match(key)}
        entries.append({protocol: config} if config else protocol)
    return entries


def _obs_plugin_choice(cli_events_format: str | None) -> list[str] | None:
    if cli_events_format == "both":
        return ["native", "otel"]
    if cli_events_format in {"native", "otel"}:
        return [cli_events_format]
    return None


def _warn_obs_precedence(winner: str, *replaced: str) -> None:
    named = [name for name in replaced if name]
    if named:
        warn_once(f"{winner} replaces {', '.join(named)}")


def _add_bind_sinks(
    plugins: list[str],
    plugin_configs: dict[str, dict],
    flavour_spec: dict | None,
    resolved_infra: ResolvedInfra | None,
    *,
    add: bool,
) -> tuple[list[str], dict[str, dict]]:
    """Add the plugins ``bind.events`` rows name, and give each plugin its row's settings.

    Naming a row is the declaration to send events there, so its plugin joins the selected set. With ``add``
    false (``--no-events`` or an explicit ``--events-format``) the set is final and a row only configures a
    plugin already in it.
    """
    raw = _bind_events_raw(flavour_spec, resolved_infra)
    if not raw:
        return plugins, plugin_configs
    sinks = parse_observability(raw)
    selected = list(plugins)
    configs = {key: dict(value) for key, value in plugin_configs.items()}
    for name in sinks.plugins:
        if name not in selected:
            if not add:
                continue
            selected.append(name)
        configs[name] = {**configs.get(name, {}), **sinks.plugin_configs.get(name, {})}
    return selected, configs


def observability_config_from_manifest(
    manifest: dict | None,
    *,
    deployment: dict | None = None,
    agent_id: str = "",
    mas_id: str = "",
    cli_events: bool | None = None,
    cli_events_file: str | None = None,
    cli_events_stdout: bool = False,
    cli_events_format: str | None = None,
    flavour_spec: dict | None = None,
    resolved_infra: ResolvedInfra | None = None,
    entry_id: str = "",
    delegate_ids: list[str] | tuple[str, ...] = (),
) -> ObservabilityConfig:
    """CLI replace-flags, then Control observability, then bind.events, then native.

    Matching Controls contribute an observability set. Leftover Agent
    ``spec.observability`` still applies when no matching Control names that
    list. Leftover Flavour ``observability`` is unread. ``bind.events`` rows add
    the plugins they name; they do not replace native. ``--no-events`` and
    ``--events-format`` ``native`` / ``otel`` / ``both`` replace the plugin set.
    ``--events-stdout`` and ``--events-file`` add native when it is not
    selected; they do not replace a plugin. Native records when no Control
    named this list and no leftover agent named plugins (including when
    ``bind.events`` added a row). A Control that names ``observability: []``
    named the list: native does not fill.
    """
    who = agent_id or entry_id
    spec = spec_with_control_lists(
        (manifest or {}).get("spec") or {},
        flavour_spec,
        agent_id=who,
        entry_id=entry_id,
        delegate_ids=delegate_ids,
    )
    named_obs = False
    if flavour_spec:
        lists = control_lists_for_agent(
            resolve_controls(flavour_spec),
            agent_id=who,
            entry_id=entry_id,
            delegate_ids=delegate_ids,
        )
        named_obs = OBSERVABILITY in lists

    def _short_obs(raw: Any) -> Any:
        if not isinstance(raw, list):
            return raw
        out: list[Any] = []
        for item in raw:
            if isinstance(item, str):
                out.append(item.rsplit(".", 1)[-1])
            elif isinstance(item, dict) and len(item) == 1:
                name, cfg = next(iter(item.items()))
                out.append({str(name).rsplit(".", 1)[-1]: cfg})
            else:
                out.append(item)
        return out

    agent_binding = parse_observability(_short_obs(spec.get("observability")))
    sink_ref = parse_sink_from_deployment(deployment)
    choice = _obs_plugin_choice(cli_events_format)
    sources = [("spec.observability", agent_binding)] if agent_binding.plugins else []
    names = [name for name, _ in sources]
    if sources:
        binding = sources[0][1]
        plugins: list[str] = list(binding.plugins)
        enabled = True
    else:
        binding = parse_observability(None)
        plugins = []
        enabled = bool(sink_ref)

    if cli_events is False:
        _warn_obs_precedence("CLI --no-events", *names)
        binding = parse_observability(None)
        plugins = []
        enabled = False
    elif choice is not None:
        if plugins != choice:
            _warn_obs_precedence("CLI --events-format", *names)
        else:
            _warn_obs_precedence(names[0], *names[1:])
        binding = parse_observability(choice)
        plugins = list(binding.plugins)
        enabled = True
    else:
        if sources:
            _warn_obs_precedence(names[0], *names[1:])
        if cli_events is True:
            enabled = True

    plugins, plugin_configs = _add_bind_sinks(
        plugins,
        dict(binding.plugin_configs),
        flavour_spec,
        resolved_infra,
        add=cli_events is not False and choice is None,
    )
    if plugins and cli_events is not False:
        enabled = True

    if sink_ref and not plugins and enabled:
        plugins = [normalize_obs_plugin(sink_ref)]
        enabled = True

    # The events file and the stdout stream are native's JSONL: asking for one means native records.
    if (cli_events_file or cli_events_stdout) and cli_events is not False and choice is None:
        if plugins and "native" not in plugins:
            plugins = [*plugins, "native"]

    # bind.events adds a sink; it does not count as choosing the plugin set. A Control
    # that names observability (including []) or leftover agent observability does.
    # Native fills when neither named the list.
    if cli_events is not False and choice is None and not sources and not named_obs:
        if "native" not in plugins:
            native_binding = parse_observability(["native"])
            plugins = [*plugins, "native"] if plugins else ["native"]
            plugin_configs = {**dict(native_binding.plugin_configs), **plugin_configs}
        enabled = True
    fmt = derive_observability_format(plugins, cli_override=cli_events_format)

    otel_cfg = plugin_configs.get("otel") or {}
    otel_file = otel_cfg.get("otel_file")
    if not otel_file and otel_cfg.get("output_path"):
        from pathlib import Path

        otel_file = str(Path(str(otel_cfg["output_path"])) / "otel_sdk_spans.jsonl")

    native_cfg = plugin_configs.get("native") or {}
    events_file = cli_events_file or binding.events_file
    if not events_file:
        events_file = native_cfg.get("path") or native_cfg.get("events_file")

    events_stdout = cli_events_stdout or binding.stdout or bool(native_cfg.get("stdout"))

    return ObservabilityConfig(
        enabled=bool(enabled),
        format=str(fmt),
        events_file=str(events_file) if events_file else None,
        events_stdout=events_stdout,
        otel_file=str(otel_file) if otel_file else None,
        sink_ref=str(sink_ref) if sink_ref else None,
        agent_id=agent_id,
        mas_id=resolve_mas_id(manifest, mas_id=mas_id),
        plugins=plugins,
        plugin_configs=plugin_configs,
    )


def engine_use_tool_loop(manifest: dict | None, kernel: KernelConfig) -> bool:
    """Whether the LLM engine exposes tools for native function-calling.

    Structural only — not a manifest flag. ReAct with declared tools or skills
    ⇒ tool egress in the Mealy product (``activate_skill`` is a system tool
    when ``spec.skills`` is non-empty). Plan-execute schedules tools via the
    DP plugin instead.
    """
    if kernel.pattern_plugin_id == "plan_execute@v1":
        return False
    spec = (manifest or {}).get("spec") or {}
    return bool(spec.get("tools") or spec.get("skills"))
