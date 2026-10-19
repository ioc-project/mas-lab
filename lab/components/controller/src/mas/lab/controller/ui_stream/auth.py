#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Token + TLS gate for the demo-ui stream plane."""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import HTTPException, Request, WebSocket
from starlette.datastructures import Headers

_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1", "testclient", "testserver"})
_PROTECTED_PREFIXES = (
    "/api/ingest",
    "/api/commands",
    "/api/stream",
    "/api/plots",
    "/ws",
)
CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "object-src 'none'; "
    "frame-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)


def ui_token() -> str:
    return str(os.environ.get("MAS_DEMO_UI_TOKEN") or "").strip()


def is_loopback(host: str) -> bool:
    name = str(host or "").split("%", 1)[0].strip().lower().strip("[]")
    if name.startswith("127."):
        return True
    return name in _LOOPBACK


def ssl_enabled() -> bool:
    cert = str(os.environ.get("MAS_DEMO_UI_SSL_CERTFILE") or "").strip()
    key = str(os.environ.get("MAS_DEMO_UI_SSL_KEYFILE") or "").strip()
    return bool(cert and key)


def ssl_uvicorn_kwargs() -> dict[str, str]:
    if not ssl_enabled():
        return {}
    return {
        "ssl_certfile": str(os.environ.get("MAS_DEMO_UI_SSL_CERTFILE") or "").strip(),
        "ssl_keyfile": str(os.environ.get("MAS_DEMO_UI_SSL_KEYFILE") or "").strip(),
    }


def assert_ui_bind(host: str) -> None:
    """Refuse a non-loopback bind that has no TLS or no token."""
    if is_loopback(host):
        return
    if not ssl_enabled():
        raise RuntimeError("TLS is required for a non-loopback mas-lab serve bind")
    if not ui_token():
        raise RuntimeError("MAS_DEMO_UI_TOKEN is required for a non-loopback mas-lab serve bind")


def token_from_headers(headers: Headers | dict[str, str] | None, extra: str | None = None) -> str:
    if extra:
        return str(extra)
    if headers is None:
        return ""
    raw = str(headers.get("authorization") or headers.get("Authorization") or "")
    if raw.lower().startswith("bearer "):
        raw = raw[7:]
    raw = raw.strip()
    if raw:
        return raw
    return str(headers.get("x-mas-token") or headers.get("X-Mas-Token") or "").strip()


def token_from_request(request: Request) -> str:
    query = str(request.query_params.get("token") or "").strip()
    return token_from_headers(request.headers, query or None)


def check_token(provided: str, expected: str) -> bool:
    if not expected:
        return False
    return hmac.compare_digest(str(provided or ""), str(expected))


def authorize_http(request: Request) -> None:
    expected = ui_token()
    if not expected:
        return
    if not check_token(token_from_request(request), expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def authorize_ws(ws: WebSocket, hello: dict[str, Any] | None = None) -> None:
    expected = ui_token()
    if not expected:
        return
    query = str(ws.query_params.get("token") or "").strip()
    provided = token_from_headers(ws.headers, query or None)
    if hello and not provided:
        provided = str(hello.get("token") or hello.get("authorization") or "").strip()
        if provided.lower().startswith("bearer "):
            provided = provided[7:].strip()
    if not check_token(provided, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def path_is_protected(path: str) -> bool:
    raw = str(path or "")
    return any(raw == prefix or raw.startswith(prefix + "/") or raw.startswith(prefix + "?") for prefix in _PROTECTED_PREFIXES)


def ingest_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = ui_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def security_headers() -> dict[str, str]:
    return {
        "Content-Security-Policy": CSP,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
