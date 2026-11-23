#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Keep third-party OpenTelemetry instrumentation off unless the user opts in.

MAS Lab emits OTel only through the ``otel`` observability plugin. Recent
a2a-sdk releases turn tracing on whenever ``opentelemetry`` is importable
(default ``OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=true``), wrapping async
work in ``start_as_current_span``. Those spans attach/detach contextvars
tokens. When the work finishes on another thread or asyncio task — the A2A
client's thread-pool ``asyncio.run`` bridge does exactly that —
OpenTelemetry logs ``ValueError: Token was created in a different Context``
at ERROR, one full traceback per call (GitHub issue #127).

The flag is read once at import of ``a2a.utils.telemetry``. This module
must run before any ``a2a`` import. Importing it has the pin as a side
effect; ``pin_third_party_otel_off`` is also safe to call explicitly.
The CLI overwrites the flag from ``transport.<protocol>.sdk_spans`` after
it parses the manifest, still before the SDK is imported. Default remains
off.

This does **not** set ``OTEL_SDK_DISABLED``. That would disable the
first-party ``otel`` plugin. Explicit ``OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=true``
in the process environment is left alone by ``setdefault``; a manifest
``sdk_spans`` value then overrides it.

Same reasoning, same mechanism, for ``OBSERVE_REALTIME_OBSERVABILITY_ENABLED``:
the ``ioa-observe-sdk`` dependency (see the telemetry library) defaults
this to ``"true"`` when unset upstream. MAS Lab pins it off by default too —
real-time span emission is opt-in, not implied by installing the SDK.

Why this module writes ``os.environ``: these SDKs read their flags only from
the process environment, with no API to pass them. This is the one runtime
exception to "no environment access": it writes two fixed, non-secret
constants (``"false"``) with ``setdefault``, and never reads a host value
into MAS Lab. The host-read guard tests allowlist this file for that write.
"""

from __future__ import annotations

import os

A2A_SDK_OTEL_FLAG = "OTEL_INSTRUMENTATION_A2A_SDK_ENABLED"
OBSERVE_REALTIME_FLAG = "OBSERVE_REALTIME_OBSERVABILITY_ENABLED"


def pin_third_party_otel_off() -> None:
    """Default-disable third-party OTel auto-instrumentation the user did not opt into."""
    os.environ.setdefault(A2A_SDK_OTEL_FLAG, "false")
    os.environ.setdefault(OBSERVE_REALTIME_FLAG, "false")


pin_third_party_otel_off()
