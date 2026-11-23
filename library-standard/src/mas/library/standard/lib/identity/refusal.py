#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""How an identity failure is told to a caller: one closed code, nothing else."""

from __future__ import annotations

from mas.library.standard.lib.identity.contract import IdentityError
from mas.runtime.boundary.agentcomm.protocol import AgentCommError, LayerRefusal


def code_of(exc: BaseException) -> str:
    """The value-free code of a failure: an identity code, else ``unavailable``."""
    return str(exc) if isinstance(exc, IdentityError) else "unavailable"


def denial(exc: BaseException) -> AgentCommError:
    """A ``LayerRefusal`` for any failure, as ``identity: <code>``. Never a message, a cause or a value."""
    return LayerRefusal(f"identity: {code_of(exc)}")
