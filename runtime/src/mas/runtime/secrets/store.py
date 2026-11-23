#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""SecretStore protocol. Implementations are plugins; the runtime has none."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, Protocol, runtime_checkable

from mas.runtime.secrets.ref import SecretRef
from mas.runtime.secrets.value import SecretValue


@dataclass(frozen=True)
class SecretInfo:
    """Inspection record. Never a value."""

    present: bool
    size: int = 0
    reason: str = ""


@runtime_checkable
class SecretStore(Protocol):
    """Fetch a secret by ref. Messages carry the ref, never a value."""

    def get(self, ref: SecretRef) -> SecretValue:
        """Return the value, or raise SecretMissing / SecretDenied."""

    def describe(self, ref: SecretRef) -> SecretInfo:
        """Presence and byte size only. Runs the checks of ``get``; a refusal is ``present=False`` with a reason."""

    def change_token(self, ref: SecretRef) -> Hashable:
        """Opaque token that changes when the stored bytes change."""
