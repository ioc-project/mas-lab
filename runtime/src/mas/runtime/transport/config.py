#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Frozen transport-observability config. Built by the CLI, passed down.

Runtime and plugins never read the environment. Protocol names are keys the
caller supplies; this module has no built-in protocol vocabulary.

Capture lives on the native sink; projection and aliases live on the otel
sink. The observability list names those sinks. There is no transport plugin.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

# ``nested`` and ``flat`` (span nesting) are not exported yet, so they are not accepted.
OTEL_MODES: frozenset[str] = frozenset({"none", "contract"})
ALIAS_MODES: frozenset[str] = frozenset({"auto", "on", "off"})

NATIVE_SINK_NAMES: tuple[str, ...] = ("native", "mas.observability.native")
OTEL_SINK_NAMES: tuple[str, ...] = ("otel", "mas.observability.otel")

_CAPTURE_KEYS = frozenset({"enabled", "hooks", "internals", "sdk_spans", "propagate_metadata"})
_PROJECTION_KEYS = frozenset({"projection", "otel_projection", "aliases"})
_PROTOCOL_KEYS = _CAPTURE_KEYS | _PROJECTION_KEYS


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"expected true or false, got {value!r}")


def _as_mode(value: Any, default: str) -> str:
    """A mode name. YAML 1.1 reads unquoted ``on`` / ``off`` as booleans; they mean the modes of that name."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value).strip()


def sink_in(plugins: Sequence[str], names: tuple[str, ...]) -> bool:
    """True when *plugins* names one of *names* (short id or URN)."""
    return any(str(name).strip() in names for name in plugins)


def plugin_cfg(configs: Mapping[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    """The first non-empty config dict named in *names*, tried in that order (short id first)."""
    for name in names:
        raw = configs.get(name)
        if isinstance(raw, Mapping) and raw:
            return dict(raw)
    return {}


@dataclass(frozen=True)
class ProtocolTransportConfig:
    """Switches for one protocol. Same set for every protocol.

    Capture fields (enabled, hooks, internals, sdk_spans, propagate_metadata)
    come from ``native.transport``. Projection fields come from ``otel.transport``.
    """

    enabled: bool = True
    hooks: bool = True
    internals: bool = False
    sdk_spans: bool = False
    propagate_metadata: bool = False
    otel_projection: str = "contract"
    aliases: str = "auto"

    def __post_init__(self) -> None:
        if self.otel_projection not in OTEL_MODES:
            raise ValueError(
                f"projection must be one of {sorted(OTEL_MODES)}, got {self.otel_projection!r} "
                "(nested and flat are not exported yet)"
            )
        if self.aliases not in ALIAS_MODES:
            raise ValueError(f"aliases must be one of {sorted(ALIAS_MODES)}, got {self.aliases!r}")

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any] | None,
        *,
        base: ProtocolTransportConfig | None = None,
        keys: frozenset[str] | None = None,
    ) -> ProtocolTransportConfig:
        seed = base or cls()
        if not raw:
            return seed
        allowed = keys if keys is not None else _PROTOCOL_KEYS
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"unknown transport attributes {sorted(unknown)}")
        projection = raw.get("projection", raw.get("otel_projection"))
        return cls(
            enabled=_as_bool(raw.get("enabled"), seed.enabled),
            hooks=_as_bool(raw.get("hooks"), seed.hooks),
            internals=_as_bool(raw.get("internals"), seed.internals),
            sdk_spans=_as_bool(raw.get("sdk_spans"), seed.sdk_spans),
            propagate_metadata=_as_bool(raw.get("propagate_metadata"), seed.propagate_metadata),
            otel_projection=_as_mode(projection, seed.otel_projection),
            aliases=_as_mode(raw.get("aliases"), seed.aliases),
        )

    def overlay(self, raw: Mapping[str, Any] | None) -> ProtocolTransportConfig:
        return self.from_mapping(raw, base=self)

    def aliases_enabled(self, *, otlp_logs: bool = False) -> bool:
        """Whether to emit observe-sdk names SCOPE's norm reads.

        ``auto`` is on for ``contract`` (one alias per native event, not a
        second source) and off for ``none``. ``otlp_logs`` is accepted and
        ignored so callers keep passing it.
        """
        del otlp_logs
        if self.aliases == "on":
            return True
        if self.aliases == "off":
            return False
        return self.otel_projection == "contract"


DISABLED = ProtocolTransportConfig(
    enabled=False,
    hooks=False,
    internals=False,
    sdk_spans=False,
    propagate_metadata=False,
    otel_projection="none",
    aliases="off",
)

DEFAULTS = ProtocolTransportConfig()
NATIVE_ONLY = ProtocolTransportConfig(otel_projection="none", aliases="off")


def _pop_defaults(raw: Mapping[str, Any] | None) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    if not raw:
        return None, {}
    data = dict(raw)
    defaults = data.pop("defaults", None)
    if defaults is not None and not isinstance(defaults, Mapping):
        raise ValueError("transport defaults must be a mapping")
    return (defaults if isinstance(defaults, Mapping) else None), data


@dataclass(frozen=True)
class TransportObservabilityConfig:
    """Process-wide transport observability. ``present`` follows the sinks."""

    present: bool = False
    defaults: ProtocolTransportConfig = field(default_factory=ProtocolTransportConfig)
    protocols: Mapping[str, ProtocolTransportConfig] = field(default_factory=dict)
    plugin_defaults: Mapping[str, ProtocolTransportConfig] = field(default_factory=dict)

    def for_protocol(self, name: str) -> ProtocolTransportConfig:
        if not self.present:
            return DISABLED
        if name in self.protocols:
            return self.protocols[name]
        plugin = self.plugin_defaults.get(name)
        if plugin is None:
            return self.defaults
        return ProtocolTransportConfig(
            enabled=self.defaults.enabled,
            hooks=self.defaults.hooks,
            internals=self.defaults.internals,
            sdk_spans=self.defaults.sdk_spans or plugin.sdk_spans,
            propagate_metadata=self.defaults.propagate_metadata or plugin.propagate_metadata,
            otel_projection=self.defaults.otel_projection,
            aliases=self.defaults.aliases,
        )

    def any_enabled(self) -> bool:
        if not self.present:
            return False
        if self.defaults.enabled:
            return True
        return any(cfg.enabled for cfg in self.protocols.values())

    def any_sdk_spans(self) -> bool:
        if not self.present:
            return False
        if self.defaults.enabled and self.defaults.sdk_spans:
            return True
        return any(cfg.enabled and cfg.sdk_spans for cfg in self.protocols.values())

    def any_aliases(self) -> bool:
        if not self.present:
            return False
        if self.defaults.enabled and self.defaults.aliases_enabled():
            return True
        return any(cfg.enabled and cfg.aliases_enabled() for cfg in self.protocols.values())

    def overlay_cli(self, patches: Mapping[str, Mapping[str, Any]]) -> TransportObservabilityConfig:
        """Apply CLI ``protocol=attr:value`` patches. Highest precedence.

        Patches never turn transport observability on: with no native or otel sink it stays off
        (``present`` is False) and the caller says so.
        """
        if not patches or not self.present:
            return self
        protocols = dict(self.protocols)
        for name, raw in patches.items():
            protocols[name] = self.for_protocol(name).overlay(raw)
        return replace(self, protocols=protocols)

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any] | None,
        *,
        plugin_defaults: Mapping[str, ProtocolTransportConfig] | None = None,
    ) -> TransportObservabilityConfig:
        """Combined capture+projection mapping (CLI overlay and tests)."""
        extras = dict(plugin_defaults or {})
        if not raw:
            return cls(present=False, plugin_defaults=extras)
        defaults_raw, data = _pop_defaults(raw)
        defaults = ProtocolTransportConfig.from_mapping(defaults_raw)
        protocols: dict[str, ProtocolTransportConfig] = {}
        for name, cfg in data.items():
            if not isinstance(cfg, Mapping):
                raise ValueError(f"transport.{name} must be a mapping")
            base = extras.get(name, defaults)
            protocols[name] = ProtocolTransportConfig.from_mapping(cfg, base=base)
        return cls(
            present=True,
            defaults=defaults,
            protocols=protocols,
            plugin_defaults=extras,
        )

    @classmethod
    def from_plugins(
        cls,
        plugins: Sequence[str],
        configs: Mapping[str, Any],
        *,
        plugin_defaults: Mapping[str, ProtocolTransportConfig] | None = None,
    ) -> TransportObservabilityConfig:
        """Capture from native, projection from otel. Off when neither sink is listed."""
        extras = dict(plugin_defaults or {})
        native_on = sink_in(plugins, NATIVE_SINK_NAMES)
        otel_on = sink_in(plugins, OTEL_SINK_NAMES)
        if not native_on and not otel_on:
            return cls(present=False, plugin_defaults=extras)

        capture_raw = plugin_cfg(configs, NATIVE_SINK_NAMES).get("transport")
        if capture_raw is not None and not isinstance(capture_raw, Mapping):
            raise ValueError("native.transport must be a mapping")
        capture_defaults_raw, capture_protocols = _pop_defaults(
            capture_raw if isinstance(capture_raw, Mapping) else None
        )
        misplaced = set(capture_protocols) & _PROJECTION_KEYS
        if misplaced:
            raise ValueError(
                f"native.transport has {sorted(misplaced)}; projection and aliases belong on otel.transport"
            )
        capture_defaults = ProtocolTransportConfig.from_mapping(
            capture_defaults_raw,
            base=NATIVE_ONLY if not otel_on else DEFAULTS,
            keys=_CAPTURE_KEYS,
        )

        proj_protocols: dict[str, Any] = {}
        if otel_on:
            proj_raw = plugin_cfg(configs, OTEL_SINK_NAMES).get("transport")
            if proj_raw is not None and not isinstance(proj_raw, Mapping):
                raise ValueError("otel.transport must be a mapping")
            proj_data = dict(proj_raw) if isinstance(proj_raw, Mapping) else {}
            projection = proj_data.pop("projection", None)
            leftover = proj_data.pop("otel_projection", None)
            if projection is None:
                projection = leftover
            proj_defaults = ProtocolTransportConfig.from_mapping(
                {
                    "projection": projection,
                    "aliases": proj_data.pop("aliases", None),
                },
                base=capture_defaults,
                keys=_PROJECTION_KEYS,
            )
            defaults = ProtocolTransportConfig(
                enabled=capture_defaults.enabled,
                hooks=capture_defaults.hooks,
                internals=capture_defaults.internals,
                sdk_spans=capture_defaults.sdk_spans,
                propagate_metadata=capture_defaults.propagate_metadata,
                otel_projection=proj_defaults.otel_projection,
                aliases=proj_defaults.aliases,
            )
            proj_protocols = proj_data
        else:
            defaults = ProtocolTransportConfig.from_mapping(
                {"projection": "none", "aliases": "off"},
                base=capture_defaults,
                keys=_PROJECTION_KEYS,
            )

        protocols: dict[str, ProtocolTransportConfig] = {}
        names = set(capture_protocols) | set(proj_protocols)
        for name in names:
            cap = capture_protocols.get(name)
            if cap is not None and not isinstance(cap, Mapping):
                raise ValueError(f"native.transport.{name} must be a mapping")
            seed = extras.get(name, defaults)
            proto = ProtocolTransportConfig.from_mapping(
                cap if isinstance(cap, Mapping) else None,
                base=ProtocolTransportConfig(
                    enabled=defaults.enabled,
                    hooks=defaults.hooks,
                    internals=defaults.internals,
                    sdk_spans=defaults.sdk_spans,
                    propagate_metadata=seed.propagate_metadata,
                    otel_projection=defaults.otel_projection,
                    aliases=defaults.aliases,
                ),
                keys=_CAPTURE_KEYS,
            )
            proj = proj_protocols.get(name)
            if proj is not None:
                if not isinstance(proj, Mapping):
                    raise ValueError(f"otel.transport.{name} must be a mapping")
                proto = ProtocolTransportConfig.from_mapping(
                    proj,
                    base=proto,
                    keys=_PROJECTION_KEYS,
                )
            protocols[name] = proto
        return cls(
            present=True,
            defaults=defaults,
            protocols=protocols,
            plugin_defaults=extras,
        )


__all__ = [
    "ALIAS_MODES",
    "DEFAULTS",
    "DISABLED",
    "NATIVE_ONLY",
    "NATIVE_SINK_NAMES",
    "OTEL_MODES",
    "OTEL_SINK_NAMES",
    "ProtocolTransportConfig",
    "TransportObservabilityConfig",
    "plugin_cfg",
    "sink_in",
]
