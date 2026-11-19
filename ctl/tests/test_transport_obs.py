#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from mas.ctl.session.transport_obs import parse_cli_patches
from mas.runtime.transport.config import TransportObservabilityConfig


def test_parse_cli_patches_off_and_dotted() -> None:
    patches = parse_cli_patches(("a2a=off", "mcp.sdk_spans=true", "slim"))
    assert patches["a2a"]["enabled"] is False
    assert patches["mcp"]["sdk_spans"] is True
    assert patches["slim"]["enabled"] is True


def test_cli_overlay_turns_protocol_off() -> None:
    cfg = TransportObservabilityConfig.from_mapping({"defaults": {"enabled": True}})
    patched = cfg.overlay_cli(parse_cli_patches(("a2a=off",)))
    assert patched.for_protocol("a2a").enabled is False
    assert patched.for_protocol("mcp").enabled is True


def test_malformed_cli_values_name_the_flag() -> None:
    """Mutation: parse_cli_patches accepts '=x', 'a2a.=1', unknown attributes or non-boolean text."""
    import click
    import pytest

    for bad in ("=x", "a2a.=1", ".x=1", "a2a.bogus=1", "a2a.sdk_spans=banana", "a2a=maybe"):
        with pytest.raises(click.BadParameter, match="[=']"):
            parse_cli_patches((bad,))


def test_aliases_off_from_the_cli() -> None:
    """Mutation: 'off' is parsed to False and then falls back to the default."""
    cfg = TransportObservabilityConfig.from_plugins(["native", "otel"], {})
    patched = cfg.overlay_cli(parse_cli_patches(("a2a.aliases=off",)))
    assert patched.for_protocol("a2a").aliases == "off"


def test_explicit_env_opt_in_survives_a_default_config(monkeypatch) -> None:
    """Mutation: apply_sdk_span_env writes 'false' whenever sdk_spans is not set."""
    import os

    from mas.ctl.session import transport_obs
    from mas.ctl.session.transport_obs import apply_sdk_span_env
    from mas.third_party_otel import A2A_SDK_OTEL_FLAG

    transport_obs._we_set_a2a_flag = False
    monkeypatch.setenv(A2A_SDK_OTEL_FLAG, "true")
    apply_sdk_span_env(TransportObservabilityConfig.from_plugins(["native"], {}))
    assert os.environ[A2A_SDK_OTEL_FLAG] == "true"

    monkeypatch.delenv(A2A_SDK_OTEL_FLAG)
    apply_sdk_span_env(TransportObservabilityConfig.from_plugins(["native"], {}))
    assert os.environ[A2A_SDK_OTEL_FLAG] == "false"
    monkeypatch.delenv(A2A_SDK_OTEL_FLAG)
    on = TransportObservabilityConfig.from_plugins(["native"], {"native": {"transport": {"a2a": {"sdk_spans": True}}}})
    apply_sdk_span_env(on)
    assert os.environ[A2A_SDK_OTEL_FLAG] == "true"
    apply_sdk_span_env(TransportObservabilityConfig.from_plugins(["native"], {}))
    assert os.environ[A2A_SDK_OTEL_FLAG] == "false"
    monkeypatch.delenv(A2A_SDK_OTEL_FLAG)


def test_sdk_imported_first_is_reported_when_spans_are_wanted(monkeypatch) -> None:
    """Mutation: the 'imported first' check runs after the env is written, so it can never fire."""
    import sys
    import types

    from mas.ctl.session.transport_obs import apply_sdk_span_env
    from mas.third_party_otel import A2A_SDK_OTEL_FLAG

    monkeypatch.delenv(A2A_SDK_OTEL_FLAG, raising=False)
    monkeypatch.setitem(sys.modules, "a2a.utils.telemetry", types.ModuleType("a2a.utils.telemetry"))
    wanted = TransportObservabilityConfig.from_plugins(
        ["native"], {"native": {"transport": {"a2a": {"sdk_spans": True}}}}
    )
    assert apply_sdk_span_env(wanted) is True
    monkeypatch.delenv(A2A_SDK_OTEL_FLAG, raising=False)
    assert apply_sdk_span_env(TransportObservabilityConfig.from_plugins(["native"], {})) is False
    monkeypatch.delenv(A2A_SDK_OTEL_FLAG, raising=False)


def test_no_events_clears_the_a2a_flag_this_process_set(monkeypatch) -> None:
    """Mutation: _apply_transport(None) leaves OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=true."""
    import os

    from mas.ctl.session import transport_obs
    from mas.ctl.session.observability import _apply_transport
    from mas.ctl.session.transport_obs import apply_sdk_span_env
    from mas.third_party_otel import A2A_SDK_OTEL_FLAG

    transport_obs._we_set_a2a_flag = False
    monkeypatch.delenv(A2A_SDK_OTEL_FLAG, raising=False)
    on = TransportObservabilityConfig.from_plugins(["native"], {"native": {"transport": {"a2a": {"sdk_spans": True}}}})
    apply_sdk_span_env(on)
    assert os.environ[A2A_SDK_OTEL_FLAG] == "true"
    _apply_transport(None)
    assert os.environ[A2A_SDK_OTEL_FLAG] == "false"
