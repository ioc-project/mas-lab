#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Env secret store: name → MAS_SECRET_<NAME_UPPER_SNAKE>. Dev only.

Ctl constructs this store and hands it a getter. The plugin never reads ``os.environ`` itself.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Hashable

from mas.runtime import hostenv
from mas.runtime.secrets.errors import SecretDenied, SecretMissing
from mas.runtime.secrets.ref import SecretRef
from mas.runtime.secrets.store import SecretInfo
from mas.runtime.secrets.value import SecretValue, clean_secret_bytes

# Names this store can map one to one: letters, digits and single inner hyphens.
_ENV_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def env_var_for(ref: SecretRef) -> str:
    """Fixed, reversible mapping. ``-`` becomes ``_``; ``__`` separates name and key.

    Raises SecretDenied for a name that cannot be mapped one to one (``.``, ``_``, ``--``).
    """
    if not (_ENV_NAME_RE.match(ref.name) and _ENV_NAME_RE.match(ref.key)):
        raise SecretDenied(ref, "name is not valid for the env store (letters, digits, single hyphens)")
    parts = [ref.name] if ref.key == "value" else [ref.name, ref.key]
    return hostenv.SECRET_PREFIX + "__".join(part.replace("-", "_").upper() for part in parts)


def ref_for_env_var(variable: str) -> SecretRef | None:
    """The ref ``env_var_for`` maps to *variable*, or None when it is not one of its names."""
    name, _, key = variable.removeprefix(hostenv.SECRET_PREFIX).lower().replace("_", "-").partition("--")
    try:
        ref = SecretRef(name=name, key=key or "value")
        return ref if env_var_for(ref) == variable else None
    except (SecretDenied, ValueError):
        return None


class EnvSecretStore:
    """Read ``MAS_SECRET_*`` through a getter ctl supplies."""

    def __init__(self, getenv: Callable[[str], str | None] | Mapping[str, str]) -> None:
        self._getenv = getenv.get if isinstance(getenv, Mapping) else getenv

    def get(self, ref: SecretRef) -> SecretValue:
        raw = self._getenv(env_var_for(ref))
        if not raw:
            raise SecretMissing(ref)
        return SecretValue(clean_secret_bytes(ref, raw.encode("utf-8")), name=ref.name)

    def describe(self, ref: SecretRef) -> SecretInfo:
        try:
            return SecretInfo(present=True, size=len(self.get(ref)))
        except (SecretMissing, SecretDenied) as exc:
            return SecretInfo(present=False, size=0, reason=exc.reason)

    def change_token(self, ref: SecretRef) -> Hashable:
        """Never a hash of the value. A new object each call so the resolver re-reads."""
        try:
            return None if self._getenv(env_var_for(ref)) is None else object()
        except SecretDenied:
            return None
