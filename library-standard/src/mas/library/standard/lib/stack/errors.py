#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Errors a layer or a stack raises."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mas.runtime.boundary.agentcomm.protocol import AgentCommError


class StackError(RuntimeError):
    """A layer could not complete a send, or a stack could not be built."""


class StackSendError(AgentCommError, StackError):
    """A ``StackError`` as the agent-call contract reports it.

    ``agent_comm/stack`` raises it from ``send`` / ``asend`` so a caller that
    handles ``AgentCommError`` (the send tool) handles a layer failure too.
    The layer's own error is the ``__cause__``.
    """


class PeerChallenge(Exception):
    """An inbound request is not authenticated: the peer is told what would satisfy this side.

    ``challenge`` is meant for the peer, so it holds no secret (a single-use nonce, the audience it is bound to,
    what is asked for). It may be empty: the request is refused and there is nothing to offer. ``code`` is a
    short value-free slug. A layer raises this from its inbound handler *instead of* calling the handler; the
    transport that received the request turns it into its own answer (HTTP 401 with ``WWW-Authenticate``, an
    A2A auth-required task, the RPC's unauthenticated status). ``str`` is the code and nothing else.

    ``carry`` names how the transport should carry the challenge, as the scheme asked: ``a2a.metadata_key`` (the key
    of the auth-required status message's metadata), ``a2a.data_key`` (the key of its data part) and
    ``http.auth_scheme`` (the ``WWW-Authenticate`` scheme). Values are short tokens. ``wire`` is the serialized
    challenge the scheme built for a peer with its own shape; the transport carries it verbatim under the
    ``carry`` key (the metadata value, the data part value, the ``WWW-Authenticate`` parameters).
    """

    def __init__(
        self,
        challenge: Mapping[str, Any] | None = None,
        *,
        code: str = "unauthenticated",
        carry: Mapping[str, str] | None = None,
        wire: str | None = None,
    ) -> None:
        super().__init__(code)
        self.challenge: dict[str, Any] = dict(challenge or {})
        self.code = code
        self.carry: dict[str, str] = dict(carry or {})
        self.wire = wire

    def __reduce__(self) -> tuple[Any, ...]:
        return (_rebuild_challenge, (self.challenge, self.code, self.carry, self.wire))


class Unauthenticated(AgentCommError):
    """The peer answered a send as unauthenticated (HTTP 401, the RPC's UNAUTHENTICATED) and said what it wants.

    ``challenge`` is what the peer put in its answer for the caller to satisfy: reserved challenge keys, no secret,
    possibly empty. ``wire`` is the raw serialized value the peer carried it in, when it sent one, as received.
    ``carry`` is what the peer said about how it carried the challenge (the keys of ``PeerChallenge.carry``), when it
    said. A layer above that can answer it sends once more; the text is the code and nothing else.
    """

    def __init__(
        self,
        challenge: Mapping[str, Any] | None = None,
        wire: str | None = None,
        carry: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__("unauthenticated")
        self.challenge: dict[str, Any] = dict(challenge or {})
        self.wire = wire
        self.carry: dict[str, str] = dict(carry or {})

    def __reduce__(self) -> tuple[Any, ...]:
        return (Unauthenticated, (self.challenge, self.wire, self.carry))


def _rebuild_challenge(
    challenge: Mapping[str, Any], code: str, carry: Mapping[str, str], wire: str | None = None
) -> PeerChallenge:
    return PeerChallenge(challenge, code=code, carry=carry, wire=wire)
