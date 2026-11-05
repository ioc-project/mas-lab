#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Lazy slim-bindings import. Optional extra ``slim`` of the library-ioa package."""

from __future__ import annotations

from typing import Any

from mas._dist import extra_hint
from mas.runtime.boundary.agentcomm.protocol import AgentCommError

_PIN = "slim-bindings>=2.2,<3"
# Names checked against the slim-bindings==2.2.0 wheel (App, Session,
# SessionWithCompletion, SlimError.Timeout, Duration as timedelta).


def load_slim_bindings() -> Any:
    try:
        import slim_bindings
    except ImportError as exc:
        raise AgentCommError(
            f"SLIM link requires extra 'slim' ({_PIN}). Install with: {extra_hint('slim', file=__file__)}"
        ) from exc
    return slim_bindings
