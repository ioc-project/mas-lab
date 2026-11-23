#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Send a row's SecretRef headers only to its own origin, revealed per request."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mas.runtime.secrets.ref import is_ref
from mas.runtime.secrets.transport import PlainHttpRefused, reveal_headers_for
from mas.runtime.secrets.url import origin_text, same_origin


class SecretHeaderHook:
    """An httpx request hook that sets a row's headers on each request, revealing SecretRef values then.

    A secret header goes only to the origin of ``owner_url`` (or one of ``allowed_origins``: an agent card may
    name another host), over https unless that host is loopback. A request to another origin has the secret
    header names removed and is refused with ``error`` naming the two origins, never the value.
    ``check_redirect`` refuses a redirect leaving the owner's origin, for a client that follows redirects.
    """

    def __init__(
        self,
        headers: Mapping[str, Any] | None,
        *,
        owner_url: str,
        allowed_origins: Any = (),
        error: type[Exception] = RuntimeError,
        what: str = "request",
        prepare: Any = None,
    ) -> None:
        self._raw = dict(headers or {})
        self._secret = [str(key) for key, value in self._raw.items() if is_ref(value)]
        self._owner = str(owner_url or "")
        extra = [allowed_origins] if isinstance(allowed_origins, str) else list(allowed_origins or ())
        self._allowed = [self._owner] + [str(item) for item in extra]
        self._error = error
        self._what = what
        self._prepare = prepare

    def apply(self, request: Any) -> None:
        if not self._raw:
            return
        target = str(request.url)
        for name in self._secret:
            request.headers.pop(name, None)
        if self._secret and not any(same_origin(base, target, upgrade=True) for base in self._allowed):
            raise self._error(
                f"{self._what}: refusing to send the secret headers configured for "
                f"{origin_text(self._owner)} to {origin_text(target)}"
            )
        try:
            values = reveal_headers_for(target, self._raw, what="secret headers")
        except PlainHttpRefused as exc:
            raise self._error(f"{self._what}: {exc}") from None
        if self._prepare is not None:
            values = self._prepare(values)
        request.headers.update(values)

    async def arequest(self, request: Any) -> None:
        self.apply(request)

    def check_redirect(self, response: Any) -> None:
        if not response.has_redirect_location:
            return
        source = str(response.request.url)
        target = str(response.request.url.join(response.headers["location"]))
        if not same_origin(source, target, upgrade=True):
            raise self._error(f"{self._what}: refusing a redirect from {origin_text(source)} to {origin_text(target)}")

    async def acheck_redirect(self, response: Any) -> None:
        self.check_redirect(response)
