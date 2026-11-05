#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The SLIM layer's shared service: one node connection actor for every agent on a stack."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mas.library.standard.lib.comm.names import parse_name
from mas.runtime.boundary.agentcomm.protocol import AgentCommError

from library_ioa.plugins.slim.actor import ConnectionActor
from library_ioa.plugins.slim.endpoint import require_secret

PARAMS = frozenset(
    {"url", "identity", "secret", "dev", "timeout_s", "reply_timeout_s", "auth_mode", "max_message_size"}
)


class SlimService:
    """One ``ConnectionActor`` that every agent's ``SlimAgentComm`` on a stack uses.

    A stack creates it once (``SlimAgentComm.shared_service``), starts it
    before the first agent and closes it after the last. The secret is a
    value: whoever reads the environment passes it in.
    """

    def __init__(
        self,
        *,
        identity: str = "",
        url: str = "",
        secret: str = "",
        dev: bool = False,
        timeout_s: float = 2.0,
        reply_timeout_s: float = 120.0,
        auth_mode: str = "shared_secret",
        max_message_size: int | None = None,
    ) -> None:
        address = str(url or "").strip()
        if not address:
            raise AgentCommError("SLIM layer requires url")
        require_secret(secret)
        if str(auth_mode or "shared_secret") != "shared_secret":
            raise AgentCommError("SLIM layer auth_mode must be shared_secret, the only mode there is")
        for label, seconds in (("timeout_s", timeout_s), ("reply_timeout_s", reply_timeout_s)):
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0:
                raise AgentCommError(f"SLIM layer {label} must be a positive number of seconds")
        if max_message_size is not None and (
            isinstance(max_message_size, bool) or not isinstance(max_message_size, int) or max_message_size <= 0
        ):
            raise AgentCommError("SLIM layer max_message_size must be a positive number of bytes")
        parts = parse_name(str(identity or "").strip().strip("/")) if str(identity or "").strip() else ()
        if len(parts) != 3:
            raise AgentCommError(f"SLIM layer identity must be a three-part org/ns/service name, got {identity!r}")
        if "." in parts[2]:
            raise AgentCommError(f"SLIM service cannot contain '.': {parts[2]!r}")
        self.identity = "/".join(parts)
        self.secret = str(secret)
        self.max_message_size = max_message_size
        self.actor = ConnectionActor(
            endpoint=address,
            dev=bool(dev),
            timeout_s=float(timeout_s),
            reply_timeout_s=float(reply_timeout_s),
            name=self.identity,
        )
        self._started = False

    @classmethod
    def from_params(cls, params: Mapping[str, Any]) -> SlimService:
        unknown = set(params) - PARAMS
        if unknown:
            raise AgentCommError(f"SLIM layer: unknown params {sorted(unknown)}")
        return cls(**dict(params))

    def __repr__(self) -> str:
        return f"SlimService(identity={self.identity!r})"

    def start(self) -> None:
        if not self._started:
            self.actor.start()
            self._started = True

    def close(self) -> None:
        self.actor.stop()
        self._started = False
