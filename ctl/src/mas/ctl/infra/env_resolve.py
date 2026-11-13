#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Resolve ``env:VAR`` indirections in infra/v1 manifests (k8s secretKeyRef style).

Infra manifests are committed without secrets. Deploy-time values (``api_base``,
URLs, feature flags) may reference process environment variables injected by
Kubernetes ``valueFrom``, Vault agents, or local ``.env`` files.

Syntax (same as ``mas.lab.connections``)::

    api_base: env:LLM_PROXY_API_BASE|https://llm-proxy.example/v1

    # secret — empty string when unset (caller omits the header / field)
    Authorization: env:MCP_AUTH_HEADER

Fields whose key ends in ``_env`` (``api_key_env``, ``password_env``, …) are
**secretKeyRef names** and are never resolved — the value is the env var name
that holds the secret, not an ``env:`` lookup.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_ENV_PREFIX = "env:"
_SECRET_KEY_SUFFIX = "_env"


_ENDPOINT_SUFFIXES = ("_BASE_URL", "_API_BASE")
_warned_endpoints: set[tuple[str, str]] = set()


def warn_endpoint_from_env(var: str, value: str) -> None:
    """Warn once that an address came from the shell, so the API key follows it to that host."""
    from urllib.parse import urlparse

    host = urlparse(value).netloc.rsplit("@", 1)[-1] or "(unparsed)"
    key = (var, host)
    if key in _warned_endpoints:
        return
    _warned_endpoints.add(key)
    logger.warning(
        "LLM address comes from the environment variable %s (host %s): the API key for this endpoint is "
        "sent to that host. Unset %s to use the address in the file.",
        var,
        host,
        var,
    )


def resolve_env_string(value: str) -> str:
    """Expand ``env:VAR`` or ``env:VAR|default`` to a string."""
    if not value.startswith(_ENV_PREFIX):
        return value
    rest = value[len(_ENV_PREFIX) :].strip()
    if not rest:
        return ""
    if "|" in rest:
        var, default = rest.split("|", 1)
        var, default = var.strip(), default.strip()
    else:
        var, default = rest, ""
    if not var:
        return default
    resolved = os.environ.get(var)
    if resolved:
        logger.debug("env_resolve: %s resolved from env", var)
        if var.upper().endswith(_ENDPOINT_SUFFIXES):
            warn_endpoint_from_env(var, resolved)
        return resolved
    if default:
        logger.debug("env_resolve: %s is not set; using manifest default", var)
        return default
    logger.debug("env_resolve: %s is not set and has no default", var)
    return default


def resolve_manifest_values(data: Any) -> Any:
    """Recursively resolve ``env:`` strings in a parsed manifest mapping."""
    if isinstance(data, dict):
        return {k: _resolve_field(k, v) for k, v in data.items()}
    if isinstance(data, list):
        return [resolve_manifest_values(item) for item in data]
    if isinstance(data, str):
        return resolve_env_string(data)
    return data


def _resolve_field(key: str, value: Any) -> Any:
    if isinstance(key, str) and key.endswith(_SECRET_KEY_SUFFIX):  # a bare YAML `on:` key is a bool
        # _env fields hold an env-var NAME (secretKeyRef style), not the secret
        # value itself.  Allow env: indirection so the key name is itself
        # configurable at deploy time:
        #   api_key_env: env:LLM_PROXY_API_KEY_ENV|OPENAI_API_KEY
        # resolves to the value of LLM_PROXY_API_KEY_ENV (e.g. "MY_TOKEN"),
        # which the caller then reads to obtain the actual secret.
        if isinstance(value, str) and value.startswith(_ENV_PREFIX):
            return resolve_env_string(value)
        return value if not isinstance(value, str) else str(value)
    return resolve_manifest_values(value)
