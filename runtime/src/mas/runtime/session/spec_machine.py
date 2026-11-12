#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""M_spec — the Mealy δ that decides whether a ``spec_delta`` applies. It is the only place that decides.

The running spec belongs to the session. Whoever proposes a change (an operator, the model, a layer) proposes
it against a state it has seen; this machine looks at the session's lifecycle state and at the state the
change was computed from, and outputs a verdict. Nothing else orders spec writes: no lock, and no plugin
carries its own guard. A plugin only re-reads and proposes again after a ``CONFLICT``.

* ``APPLY``: the session is live and, when the change names the state it expects, that is still the state.
* ``BUSY``: the session is being checkpointed or is over; a snapshot never sees a half-revised spec.
* ``CONFLICT``: someone changed the state since the proposer read it.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any

from mas.runtime.session.state import SessionStatus


class SpecConflict(ValueError):
    """A delta was computed from a state the running spec no longer has. Re-read and propose it again."""


class SpecVerdict(str, Enum):
    APPLY = "apply"
    BUSY = "busy"
    CONFLICT = "conflict"


_CLOSED = frozenset({SessionStatus.CHECKPOINTING, SessionStatus.TERMINATED})


def spec_on_delta(
    lifecycle: SessionStatus,
    current: Mapping[str, Any] | str | None,
    expected: Mapping[str, Any] | str | None = None,
    *,
    guarded: bool = False,
) -> SpecVerdict:
    """The verdict on one delta. *guarded* deltas apply only to the state they expected (``None`` is ``{}``)."""
    if lifecycle in _CLOSED:
        return SpecVerdict.BUSY
    if guarded and (current or {}) != (expected or {}):
        return SpecVerdict.CONFLICT
    return SpecVerdict.APPLY
