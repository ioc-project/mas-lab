#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Where a secret may travel: over https, or to a loopback host. Nothing else, no escape hatch.

Every client that sends a revealed secret asks :func:`check_plain_http` first and builds its HTTP client
with :func:`httpx_client_kwargs_for`, :func:`requests_session_for` or :func:`opener_for`: no redirect is
followed and a loopback host is reached without the environment's proxy.
"""

from __future__ import annotations

import urllib.request
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from mas.runtime.secrets.errors import SecretRefError
from mas.runtime.secrets.loopback import is_loopback_host
from mas.runtime.secrets.ref import is_ref
from mas.runtime.secrets.resolver import SecretResolver, reveal_mapping
from mas.runtime.secrets.url import has_unsafe_chars, origin, origin_text


class PlainHttpRefused(SecretRefError):
    """A secret was not sent to a URL that is neither https nor loopback. Names the origin, never a value."""


def check_plain_http(url: Any, *, what: str = "secret") -> None:
    """Refuse to send ``what`` to ``url`` unless it is https (or wss) or its host is loopback, and its text
    holds no control character, white space, backslash or ``@`` in the authority."""
    scheme, host, _port = origin(url)
    shown = origin_text(url)
    if scheme not in ("https", "wss") and not is_loopback_host(host):
        raise PlainHttpRefused(f"refusing to send the {what} over plain http to {shown}; use https or a loopback host")
    try:
        netloc = urlsplit(str(url or "").strip()).netloc
    except ValueError:
        netloc = "@"
    if has_unsafe_chars(url) or "@" in netloc:
        raise PlainHttpRefused(f"refusing to send the {what} to {shown}: write the plain host and port")


def reveal_headers_for(
    url: Any, raw: Mapping[str, Any] | None, *, what: str, resolver: SecretResolver | None = None
) -> dict[str, str]:
    """*raw* revealed for a request to *url*; a map holding a SecretRef must pass :func:`check_plain_http`."""
    if not raw:
        return {}
    if any(is_ref(value) for value in raw.values()):
        check_plain_http(url, what=what)
    return reveal_mapping(raw, resolver=resolver)


def _direct(url: Any) -> bool:
    return is_loopback_host(origin(url)[1])


def httpx_client_kwargs_for(url: Any) -> dict[str, Any]:
    """Keyword arguments an ``httpx`` client for ``url`` adds: a loopback host bypasses the environment's proxy."""
    return {"mounts": dict.fromkeys(("all://", "http://", "https://"))} if _direct(url) else {}


def requests_session_for(url: Any) -> Any:
    """A ``requests`` session that follows no redirect and reaches a loopback host without any proxy."""
    import requests

    session = requests.Session()
    session.get_redirect_target = lambda _resp: None
    if _direct(url):
        send = session.send
        session.send = lambda request, **kw: send(request, **{**kw, "proxies": {}})
    return session


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def opener_for(url: Any) -> urllib.request.OpenerDirector:
    """A urllib opener that follows no redirect (urllib would resend the headers) and reaches a loopback host
    without the environment's proxy."""
    handlers: list[Any] = [_NoRedirect()]
    if _direct(url):
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)
