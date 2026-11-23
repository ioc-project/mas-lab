#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The environment a host hands to library and runtime code.

Library and runtime code never touches ``os.environ``. The CLI entry points call :func:`install`
once (:func:`mas.runtime.entrypoint.install_host_environment`); code here reads :func:`get`. Values
other code exports for a run (a service's ``env:`` values) go through :func:`overlay`, never into the
process environment.

Only declared keys are readable: the ``MAS_*`` configuration namespace (but not ``MAS_SECRET_*``) and
:data:`mas.runtime.envkeys.DECLARED_KEYS`. Every other host variable, an API key among them, is dropped.
The env secret store's ``MAS_SECRET_*`` variables and the installer's index URLs (which may carry a
token) are kept apart, behind :func:`private` (ctl only).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from mas.runtime.envkeys import CREDENTIAL_FREE_URLS, DECLARED_KEYS
from mas.runtime.secrets.env_names import KNOWN_ENV_NAMES
from mas.runtime.secrets.errors import SecretRefError
from mas.runtime.secrets.url import check_url_has_no_credentials

SECRET_PREFIX = "MAS_SECRET_"
PRIVATE_KEYS = frozenset(
    {"UV_INDEX", "UV_DEFAULT_INDEX", "UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL"}
)


def declared(name: str) -> bool:
    """True for a key library code may read."""
    return not name.startswith(SECRET_PREFIX) and (name in DECLARED_KEYS or name.startswith("MAS_"))


def _is_private(name: str) -> bool:
    return name.startswith(SECRET_PREFIX) or name in PRIVATE_KEYS


def is_credential_name(name: str) -> bool:
    """A provider key the env secret import knows (``OPENAI_API_KEY``, ...), ``MAS_SECRET_*`` or an index URL."""
    return name in KNOWN_ENV_NAMES or _is_private(name)


class HostEnvironmentError(RuntimeError):
    """The host environment holds a value this process refuses to hand to its children."""


def refuse_credentialed_urls(env: Mapping[str, str]) -> None:
    """Raise when a proxy, OTLP endpoint or store address (:data:`~mas.runtime.envkeys.CREDENTIAL_FREE_URLS`)
    holds a user, a password or a credential query parameter, or does not parse."""
    for name in sorted(CREDENTIAL_FREE_URLS):
        raw = str(env.get(name) or "").strip()
        # A value with no ``://`` is read both as written (``http:/u:p@h``) and as a bare authority.
        try:
            for candidate in (raw,) if "://" in raw else (raw, f"//{raw}") if raw else ():
                check_url_has_no_credentials(candidate, field=name)
        except SecretRefError as exc:
            raise HostEnvironmentError(
                f"{exc} MAS Lab hands {name} to its processes, so it must not hold a secret: point it at a "
                "proxy, collector or store that needs no credentials in the URL (for OTLP, put the token in "
                "the otel plugin's headers as a secret ref)."
            ) from None


_LOCK = threading.Lock()
_HOST: Mapping[str, str] = {}
_OVERLAY: dict[str, str] = {}
_INSTALLED = False


def install(env: Mapping[str, str] | None) -> None:
    """Install the host's environment. A dict is copied without its undeclared keys; another mapping (the
    test harness's ``os.environ``) is read through, filtered at every read."""
    global _HOST, _INSTALLED
    if env is None or isinstance(env, dict):
        _HOST = {k: v for k, v in (env or {}).items() if declared(k) or _is_private(k)}
    else:
        _HOST = env
    _INSTALLED = True


def installed() -> bool:
    """True once a host (an entry point, a spawn worker, the test harness) has installed its environment."""
    return _INSTALLED


def get(name: str, default: str | None = None) -> str | None:
    with _LOCK:
        if name in _OVERLAY:
            return _OVERLAY[name]
    value = _HOST.get(name) if declared(name) else None
    return default if value is None else value


def exported(name: str) -> str | None:
    """A value exported for this run through the overlay (a running service's ``env:``); never a host value."""
    with _LOCK:
        return _OVERLAY.get(name)


def private(name: str) -> str | None:
    """A ``MAS_SECRET_*`` variable or an installer index URL. Ctl only: the env store and ``plugin install``."""
    if not _is_private(name):
        raise KeyError(f"{name} is not a private host key")
    return _HOST.get(name)


def snapshot() -> dict[str, str]:
    """Declared base environment plus overlay, for a child process's allowlist."""
    with _LOCK:
        return {**{k: v for k, v in list(_HOST.items()) if declared(k)}, **_OVERLAY}


def overlay_snapshot() -> dict[str, str]:
    """Only the values exported for a run (no base): what a spawned worker cannot inherit."""
    with _LOCK:
        return dict(_OVERLAY)


class OverlayRefused(ValueError):
    """A value may not be exported to the whole process: it is, or may carry, a secret."""


def _plain(values: Mapping[str, str]) -> dict[str, str]:
    """Only plain strings enter the overlay; a secret object, a credential name and a credentialed URL are refused."""
    out: dict[str, str] = {}
    for key, value in values.items():
        name = str(key)
        if not isinstance(value, str):
            raise TypeError(
                f"hostenv overlay {name}: expected a plain string, got {type(value).__name__}; "
                "a secret is revealed into a child's own environment, never into the overlay"
            )
        if is_credential_name(name):
            raise OverlayRefused(f"hostenv overlay {name}: a credential name is never exported to the process")
        out[name] = value
    try:
        refuse_credentialed_urls(out)
    except HostEnvironmentError as exc:
        raise OverlayRefused(f"hostenv overlay: {exc}") from None
    return out


def set_overlay(values: Mapping[str, str]) -> None:
    plain = _plain(values)
    with _LOCK:
        _OVERLAY.update(plain)


def clear_overlay(names: list[str] | tuple[str, ...]) -> None:
    with _LOCK:
        for name in names:
            _OVERLAY.pop(name, None)


@contextmanager
def overlay(values: Mapping[str, str]) -> Iterator[None]:
    plain = _plain(values)
    with _LOCK:
        previous = {k: _OVERLAY.get(k) for k in plain}
        _OVERLAY.update(plain)
    try:
        yield
    finally:
        with _LOCK:
            for k, v in previous.items():
                if v is None:
                    _OVERLAY.pop(k, None)
                else:
                    _OVERLAY[k] = v
