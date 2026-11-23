#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""A declared ``env:`` map for a child process: plain strings, or SecretRefs revealed at spawn.

A service, a fixture or a shell step names the variables its own child gets. A value is a
plain string or ``{secret: NAME}``. A SecretRef is revealed only into that child's environment
when it starts; it never enters hostenv, a log line, or another process.

A plain value is refused where it would carry a secret: under a provider key name the env
secret import knows (``OPENAI_API_KEY``, ...) or a ``MAS_SECRET_*`` name, and a URL with
credentials in it (``http://user:password@proxy``). Those take ``{secret: NAME}``. So is a plain
installer index URL (``PIP_INDEX_URL``, ``UV_INDEX``, ...), which may carry a token, and a proxy,
OTLP endpoint or store address with a user in it (``HTTPS_PROXY: user@proxy:3128``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Union

from mas.runtime import hostenv
from mas.runtime.secrets import SecretRef, SecretRefError, prepare_manifest

DeclaredEnv = dict[str, Union[str, SecretRef]]


def compile_env(raw: Any, *, where: str) -> DeclaredEnv:
    """Validate a declared ``env`` map. ``env:`` strings, removed ``*_env`` keys and literal secrets are refused."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{where}.env must be a mapping of name to value")
    hydrated = prepare_manifest(dict(raw), source=f"{where}.env")
    out: DeclaredEnv = {}
    for key, value in hydrated.items():
        name = str(key)
        if isinstance(value, SecretRef):
            out[name] = value
            continue
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"{where}.env.{name}: expected a string or {{secret: NAME}}, got {type(value).__name__}")
        if hostenv.is_credential_name(name):
            raise SecretRefError(f"{where}.env.{name}: holds a credential; write {{secret: NAME}}, not a value")
        try:
            hostenv.refuse_credentialed_urls({name: str(value)})
        except hostenv.HostEnvironmentError:
            raise SecretRefError(
                f"{where}.env.{name}: a proxy, OTLP endpoint or store address may not hold credentials; "
                "point it at one that needs none in the URL"
            ) from None
        out[name] = str(value)
    return out


def plain_env(env: Mapping[str, Any]) -> dict[str, str]:
    """Only the non-secret entries: what may be exported to the run or written in a log."""
    return {key: value for key, value in env.items() if isinstance(value, str)}
