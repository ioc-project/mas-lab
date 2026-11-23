#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mas.runtime.spec.obs import SpecBindingError, parse_obs_spec
from mas.runtime.transport.config import (
    DEFAULTS,
    NATIVE_ONLY,
    ProtocolTransportConfig,
    TransportObservabilityConfig,
)

CAPTURE_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "schemas"
    / "runtime"
    / "fragments"
    / "transport-protocol-config.schema.yaml"
)
OTEL_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "schemas"
    / "runtime"
    / "fragments"
    / "otel-transport-config.schema.yaml"
)


def test_dataclass_defaults_match_schema() -> None:
    capture = yaml.safe_load(CAPTURE_SCHEMA.read_text(encoding="utf-8"))["properties"]
    otel = yaml.safe_load(OTEL_SCHEMA.read_text(encoding="utf-8"))["properties"]
    assert capture["enabled"]["default"] == DEFAULTS.enabled
    assert capture["hooks"]["default"] == DEFAULTS.hooks
    assert capture["internals"]["default"] == DEFAULTS.internals
    assert capture["sdk_spans"]["default"] == DEFAULTS.sdk_spans
    assert capture["propagate_metadata"]["default"] == DEFAULTS.propagate_metadata
    assert otel["projection"]["default"] == DEFAULTS.otel_projection
    assert otel["aliases"]["default"] == DEFAULTS.aliases
    assert set(otel["projection"]["enum"]) == {"none", "contract"}
    assert DEFAULTS.internals is False
    assert DEFAULTS.aliases_enabled() is True
    assert ProtocolTransportConfig(otel_projection="none").aliases_enabled() is False
    assert NATIVE_ONLY.otel_projection == "none"
    assert NATIVE_ONLY.aliases_enabled() is False


def test_schema_examples_parse() -> None:
    root = Path(__file__).resolve().parents[2] / "docs" / "schemas" / "examples" / "controls"
    off = parse_obs_spec(
        yaml.safe_load((root / "observability-transport-a2a-off.yaml").read_text())["spec"]["observability"]
    )
    assert off.transport.present is True
    assert off.transport.for_protocol("a2a").enabled is False
    assert off.transport.defaults.otel_projection == "none"
    native_only = parse_obs_spec(
        yaml.safe_load((root / "observability-transport-native-only.yaml").read_text())["spec"]["observability"]
    )
    assert native_only.transport.present is True
    assert native_only.transport.defaults.otel_projection == "none"
    assert "native" in native_only.plugins or "mas.observability.native" in native_only.plugins


def test_urn_native_sink_turns_capture_on() -> None:
    binding = parse_obs_spec(["mas.observability.native"])
    assert binding.transport.present is True
    assert binding.transport.defaults.otel_projection == "none"


def test_native_sink_turns_capture_on() -> None:
    binding = parse_obs_spec(["native"])
    assert "transport" not in binding.plugins
    assert binding.transport.present is True
    assert binding.transport.for_protocol("example").enabled is True
    assert binding.transport.for_protocol("example").hooks is True
    assert binding.transport.for_protocol("example").otel_projection == "none"
    assert binding.transport.for_protocol("example").aliases_enabled() is False


def test_otel_sink_turns_projection_and_aliases_on() -> None:
    binding = parse_obs_spec(["otel"])
    assert binding.transport.present is True
    cfg = binding.transport.for_protocol("example")
    assert cfg.enabled is True
    assert cfg.hooks is True
    assert cfg.otel_projection == "contract"
    assert cfg.aliases_enabled() is True


def test_parse_obs_spec_reads_capture_on_native() -> None:
    binding = parse_obs_spec(
        [
            {
                "native": {
                    "transport": {
                        "defaults": {"enabled": True},
                        "example": {"enabled": False, "sdk_spans": True},
                    }
                }
            }
        ]
    )
    assert "transport" not in binding.plugins
    assert "native" in binding.plugins
    assert binding.transport.present is True
    assert binding.transport.for_protocol("example").enabled is False
    assert binding.transport.for_protocol("example").sdk_spans is True
    assert binding.transport.for_protocol("other").enabled is True
    assert binding.transport.for_protocol("example").otel_projection == "none"


def test_parse_obs_spec_reads_projection_on_otel() -> None:
    binding = parse_obs_spec(
        [
            "native",
            {"otel": {"transport": {"projection": "contract", "example": {"projection": "none"}}}},
        ]
    )
    assert binding.transport.defaults.otel_projection == "contract"
    assert binding.transport.for_protocol("example").otel_projection == "none"
    assert binding.transport.for_protocol("other").otel_projection == "contract"
    assert binding.transport.for_protocol("other").aliases_enabled() is True


def test_native_transport_rejects_projection_key() -> None:
    with pytest.raises(ValueError, match="belong on otel.transport"):
        parse_obs_spec([{"native": {"transport": {"projection": "contract"}}}])


def test_transport_list_entry_is_rejected() -> None:
    with pytest.raises(SpecBindingError, match="not an observability plugin"):
        parse_obs_spec(["native", {"transport": {"defaults": {"enabled": True}}}])


def test_empty_observability_is_off() -> None:
    binding = parse_obs_spec([])
    assert binding.transport.present is False


def test_unknown_protocol_rejected_at_bind() -> None:
    """Mutation: unknown protocol names are stored without a load-time error."""
    from mas.runtime.boundary.obs.plugins import _bind_transport, _transport_plugin_defaults

    if not _transport_plugin_defaults():
        pytest.skip("transport classifiers not registered")
    with pytest.raises(ValueError, match="unknown transport protocol"):
        _bind_transport(
            TransportObservabilityConfig(
                present=True,
                protocols={"not-a-protocol": ProtocolTransportConfig()},
            )
        )


def test_aliases_off_is_honoured_from_yaml_and_cli() -> None:
    """Mutation: ``str(raw.get('aliases') or seed.aliases)`` again, so a YAML ``off`` (False) falls back to auto."""
    import yaml

    loaded = yaml.safe_load("aliases: off\n")  # YAML 1.1 reads the bare word as False
    assert loaded["aliases"] is False
    cfg = ProtocolTransportConfig.from_mapping(loaded, keys=frozenset({"aliases"}))
    assert cfg.aliases == "off"
    assert cfg.aliases_enabled() is False
    assert ProtocolTransportConfig.from_mapping({"aliases": True}, keys=frozenset({"aliases"})).aliases == "on"


def test_bad_values_are_errors_not_defaults() -> None:
    """Mutation: _as_bool returns the default for text it does not know."""
    with pytest.raises(ValueError, match="true or false"):
        ProtocolTransportConfig.from_mapping({"enabled": "maybe"})
    with pytest.raises(ValueError, match="nested and flat are not exported"):
        ProtocolTransportConfig.from_mapping({"projection": "nested"})


def test_cli_patches_do_not_turn_a_sinkless_run_on() -> None:
    """Mutation: overlay_cli sets present=True, so a flag alone enables the bus."""
    off = TransportObservabilityConfig.from_plugins([], {})
    assert off.overlay_cli({"a2a": {"enabled": False}}).present is False


def test_short_sink_name_wins_over_the_long_one() -> None:
    """Mutation: plugin_cfg iterates a set, so the winner depends on the hash seed."""
    from mas.runtime.transport.config import NATIVE_SINK_NAMES, plugin_cfg

    configs = {"mas.observability.native": {"a": 2}, "native": {"a": 1}}
    assert plugin_cfg(configs, NATIVE_SINK_NAMES) == {"a": 1}
    assert plugin_cfg({"native": {}, "mas.observability.native": {"a": 2}}, NATIVE_SINK_NAMES) == {"a": 2}


def test_metadata_propagation_is_off_by_default() -> None:
    """Mutation: propagate_metadata defaults to True, so every peer is sent the session id."""
    assert ProtocolTransportConfig().propagate_metadata is False
    assert TransportObservabilityConfig.from_plugins(["native"], {}).for_protocol("a2a").propagate_metadata is False


def test_defaults_propagate_metadata_reaches_a_registered_protocol() -> None:
    """Mutation: for_protocol ignores defaults.propagate_metadata when a classifier default exists."""
    cfg = TransportObservabilityConfig.from_plugins(
        ["native"],
        {"native": {"transport": {"defaults": {"propagate_metadata": True}}}},
        plugin_defaults={"a2a": ProtocolTransportConfig(propagate_metadata=False)},
    )
    assert cfg.for_protocol("a2a").propagate_metadata is True
