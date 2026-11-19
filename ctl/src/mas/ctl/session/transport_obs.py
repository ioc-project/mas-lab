#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""CLI-layer transport observability: env pin, intake install, flag parsing.

Runtime and plugins never read the environment. This module is the one place
that does, before protocol SDKs are imported for the run.
"""

from __future__ import annotations

import logging
import os
import sys

import click
from mas.runtime.registry import get_registry
from mas.runtime.transport.config import _PROTOCOL_KEYS, TransportObservabilityConfig
from mas.third_party_otel import A2A_SDK_OTEL_FLAG

_logger = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_BOOL_ATTRS = {"enabled", "hooks", "internals", "sdk_spans", "propagate_metadata"}
_we_set_a2a_flag = False


def apply_sdk_span_env(config: TransportObservabilityConfig) -> bool:
    """Set the A2A SDK telemetry flag before the SDK is imported. True when it is too late to take effect.

    The SDK reads the flag once, when ``a2a.utils.telemetry`` is imported. An explicit
    ``OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=true`` in the process environment is an opt-in that is left
    alone (see ``mas.third_party_otel``); a resolved ``sdk_spans: true`` turns the flag on; otherwise
    the flag keeps whatever the pin set (off).
    """
    global _we_set_a2a_flag
    a2a = config.for_protocol("a2a")
    wanted = a2a.enabled and a2a.sdk_spans
    previous = os.environ.get(A2A_SDK_OTEL_FLAG, "").strip().lower()
    imported = "a2a.utils.telemetry" in sys.modules
    if wanted:
        os.environ[A2A_SDK_OTEL_FLAG] = "true"
        _we_set_a2a_flag = True
    elif _we_set_a2a_flag:
        os.environ[A2A_SDK_OTEL_FLAG] = "false"
        _we_set_a2a_flag = False
    else:
        os.environ.setdefault(A2A_SDK_OTEL_FLAG, "false")
    return bool(wanted and imported and previous not in _TRUE)


def install_configured_intake(config: TransportObservabilityConfig, *, sdk_imported_first: bool = False) -> None:
    """Load classifiers for protocols with sdk_spans and attach intake (idempotent per provider)."""
    if not config.any_sdk_spans():
        return
    registry = get_registry()
    classifiers = []
    for entry in registry.get_by_category("transport_classifier"):
        name = entry.urn.rsplit(".", 1)[-1]
        cfg = config.for_protocol(name)
        if not (cfg.enabled and cfg.sdk_spans):
            continue
        variant = entry.resolve()
        classifiers.append(variant.load_class()())
    if not classifiers:
        return
    from mas.library.telemetry.transport.intake import IntakeConfig, install_intake

    report = install_intake(
        tuple(classifiers),
        config=IntakeConfig(sdk_spans_requested=True, sdk_imported_first=sdk_imported_first),
    )
    _logger.info(
        "transport intake active=%s provider=%s classifiers=%s sdk_spans=%s",
        report.active,
        report.provider,
        ",".join(report.classifiers) or "-",
        report.sdk_spans,
    )


def parse_cli_patches(values: tuple[str, ...]) -> dict[str, dict]:
    """Parse ``--transport-observability proto=off`` / ``proto.sdk_spans=true``.

    Raises ``click.BadParameter`` naming the flag value when it is malformed.
    """
    patches: dict[str, dict] = {}
    for raw in values:
        text = str(raw).strip()
        if not text:
            continue
        if "=" not in text:
            key, value = text, "true"
        else:
            key, value = (part.strip() for part in text.split("=", 1))
        protocol, dot, attr = key.partition(".")
        if not dot:
            attr = "enabled"
        if not protocol or not attr:
            raise click.BadParameter(f"{raw!r}: expected PROTOCOL=off or PROTOCOL.ATTRIBUTE=VALUE")
        if attr not in _PROTOCOL_KEYS:
            raise click.BadParameter(f"{raw!r}: unknown attribute {attr!r}; one of {sorted(_PROTOCOL_KEYS)}")
        low = value.lower()
        parsed: bool | str
        if attr in _BOOL_ATTRS:
            if low in _TRUE:
                parsed = True
            elif low in _FALSE:
                parsed = False
            else:
                raise click.BadParameter(f"{raw!r}: {attr} takes true or false, got {value!r}")
        else:
            parsed = value
        patches.setdefault(protocol, {})[attr] = parsed
    return patches


__all__ = [
    "apply_sdk_span_env",
    "install_configured_intake",
    "parse_cli_patches",
]
