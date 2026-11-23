#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""URLs never carry a credential: no userinfo, no credential query or fragment parameter.

Also the one origin rule (:func:`origin`, :func:`same_origin`, :func:`origin_text`) and the one
unsafe-character check (:func:`has_unsafe_chars`) every client that sends a secret uses.
"""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Any
from urllib.parse import unquote, unquote_plus, urlsplit

from mas.runtime.secrets.errors import SecretRefError

# Query and fragment parameter names that carry a credential, compared without case.
URL_CREDENTIAL_PARAMS = frozenset(
    {"api_key", "apikey", "key", "token", "access_token", "password", "secret", "sig", "signature"}
)
# ``scheme://authority`` or ``//authority``: what reads as a URL (a WHATWG parser also reads ``http:/h``).
_URL_RE = re.compile(r"\A(?:[A-Za-z][A-Za-z0-9+.-]*:[/\\]+|//)([^/?#]*)")
# ``user:password@host[:port][/...]`` with no scheme (a DSN). The port is digits, so an image
# reference such as ``repo:tag@sha256:digest`` is not one.
_DSN_RE = re.compile(r"\A[^\s:/@?#]+:[^\s/@?#]*@[^\s:/@?#]+(?::\d+)?(?:[/?#]\S*)?\Z")
# URL parsers disagree on where an authority holding one of these ends.
_DROPPED = str.maketrans("", "", "\t\r\n")
_AUTHORITY_UNSAFE_RE = re.compile(r"[\\\x00-\x1f\x7f]")


def check_url_has_no_credentials(url: str, *, field: str = "url") -> None:
    """Refuse userinfo (``user:password@``), a backslash or control character in the authority, a
    credential query or fragment parameter (:data:`URL_CREDENTIAL_PARAMS`, split on ``&`` and ``;``), and
    a scheme-less ``user:password@host`` DSN. The error names *field*, never the value. Not a URL: passes.
    """
    if not isinstance(url, str):
        return
    text = url.strip().translate(_DROPPED)  # URL parsers drop tab and line ends
    match = _URL_RE.match(text)
    if match is None:
        if _DSN_RE.match(text):
            raise SecretRefError(f"{field}: a connection string may not hold credentials (user:password@host)")
        return
    authority = unquote(match.group(1))
    if _AUTHORITY_UNSAFE_RE.search(authority):
        raise SecretRefError(f"{field}: a URL authority may not hold a backslash or control character")
    if "@" in authority:
        raise SecretRefError(
            f"{field}: a URL may not hold credentials (user:password@host); "
            "put them in a SecretRef ({secret: NAME}) on the field or header that carries them."
        )
    rest, _, fragment = text.partition("#")
    for item in re.split(r"[&;]", f"{rest.partition('?')[2]}&{fragment}"):
        name = unquote_plus(item.split("=", 1)[0]).strip().lower()
        if name in URL_CREDENTIAL_PARAMS:
            raise SecretRefError(
                f"{field}: URL parameter {name!r} carries a credential; send it in a header that holds a SecretRef."
            )


# The default port of each scheme an origin is compared on. Fixed: the host's services database is never read.
DEFAULT_PORTS = MappingProxyType({"http": 80, "https": 443, "ws": 80, "wss": 443})

# A backslash, white space or a control character is read differently by different URL parsers
# (a browser or a Rust parser takes ``\`` as ``/``), so the host could differ from the one checked.
_UNSAFE_CHARS_RE = re.compile(r"[\\\s\x00-\x1f\x7f]")


def has_unsafe_chars(url: Any) -> bool:
    """True when *url* holds a backslash, white space or a control character."""
    return bool(_UNSAFE_CHARS_RE.search(str(url or "")))


def origin(url: Any) -> tuple[str, str, int]:
    """``(scheme, host, port)`` of *url*, lowercased, the scheme's default port (:data:`DEFAULT_PORTS`)
    filled in; ``0`` for an unknown scheme with no port. ``("", "", 0)`` for a URL that does not parse."""
    try:
        parts = urlsplit(str(url or "").strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return "", "", 0
    scheme = (parts.scheme or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    return scheme, host, int(port or DEFAULT_PORTS.get(scheme, 0))


def origin_text(url: Any) -> str:
    """``scheme://host[:port]`` of *url* for an error or a log: the port only when the URL names one,
    never its userinfo, path, query or fragment. ``(no url)`` when it has neither scheme nor host."""
    try:
        parts = urlsplit(str(url or "").strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return "(no url)"
    scheme = (parts.scheme or "").lower()
    if not scheme and not host:
        return "(no url)"
    try:
        port = parts.port
    except ValueError:
        port = None
    shown = f"[{host}]" if ":" in host else host
    if port is not None:
        shown = f"{shown}:{port}"
    return f"{scheme or '(no scheme)'}://{shown}"


def same_origin(a: Any, b: Any, *, upgrade: bool = False) -> bool:
    """True when *b* has the origin of *a* (:func:`origin`), both with a scheme and a host.

    With *upgrade*, *b* may also be the https upgrade of an http *a* on the same host with default
    ports (``http://h`` to ``https://h``); never a downgrade.
    """
    first, second = origin(a), origin(b)
    if not (first[0] and first[1] and second[0] and second[1]):
        return False
    if first == second:
        return True
    return upgrade and first == ("http", second[1], 80) and second == ("https", first[1], 443)
