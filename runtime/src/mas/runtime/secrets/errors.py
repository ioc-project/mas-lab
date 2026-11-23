#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Secret errors name the ref, never a value."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mas.runtime.secrets.ref import SecretRef


class SecretError(Exception):
    """A secret operation failed. ``str(self)`` is the ref, not a value."""

    def __init__(self, ref: SecretRef, message: str | None = None) -> None:
        self.ref = ref
        super().__init__(message or f"secret {ref}")


class SecretMissing(SecretError):
    """The store has no value for this ref. *hint* says what to do about it (no path, no value)."""

    def __init__(self, ref: SecretRef, *, reason: str = "missing", hint: str = "") -> None:
        self.reason = reason
        super().__init__(ref, f"secret {ref} is missing" + (f": {hint}" if hint else ""))


class SecretDenied(SecretError):
    """The store refused this ref (bad name, symlink escape, loose mode)."""

    def __init__(self, ref: SecretRef, reason: str = "denied") -> None:
        self.reason = reason
        super().__init__(ref, f"secret {ref} {reason}")


class SecretRefError(ValueError):
    """A manifest value is not a SecretRef object."""
