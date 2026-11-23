#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
from __future__ import annotations

"""http_call — POST/GET a loopback (or allow-listed) fixture. Records status only."""

import asyncio
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request

from mas.lab.benchmark.environment.limits import assert_url_allowed
from mas.lab.benchmark.environment.step import ActionResult, EnvironmentContext, EnvironmentStep
from mas.runtime.secrets.transport import opener_for


def _send(req: Request, timeout: float) -> int:
    """The status of *req*. A redirect is not followed (it would leave the allow-list unchecked) and a
    loopback fixture is reached without a proxy (:func:`mas.runtime.secrets.transport.opener_for`)."""
    try:
        with opener_for(req.full_url).open(req, timeout=timeout) as resp:
            return int(resp.status)
    except HTTPError as exc:
        return int(exc.code)


class HttpCallStep(EnvironmentStep):
    type = "http_call"

    async def act(self, ctx: EnvironmentContext) -> ActionResult:
        cfg = ctx.interpolate(self.config)
        method = str(cfg.get("method") or "POST").upper()
        url = str(cfg.get("url") or "")
        if not url:
            fixture = str(cfg.get("fixture") or "")
            path = str(cfg.get("path") or "/")
            if not fixture:
                raise ValueError("http_call requires fixture+path or url")
            url = ctx.fixtures.url(fixture).rstrip("/") + "/" + path.lstrip("/")
        assert_url_allowed(url, ctx.allow_network)
        body = cfg.get("json")
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            if len(raw) > ctx.limits.max_http_body_bytes:
                raise ValueError("http_call json exceeds limits.max_http_body_bytes")
            data = raw
            headers["Content-Type"] = "application/json"
        started = time.monotonic()
        status = 0
        try:
            req = Request(url, data=data, method=method, headers=headers)
            status = await asyncio.to_thread(_send, req, ctx.limits.action_timeout_s)
        except (URLError, OSError) as exc:
            return ActionResult(outcome="failed", payload={"status": 0}, error=type(exc).__name__)
        duration_ms = round((time.monotonic() - started) * 1000.0, 1)
        expect = cfg.get("expect_status")
        outcome = "ok"
        if expect is not None and int(status) != int(expect):
            outcome = "failed"
        return ActionResult(outcome=outcome, payload={"status": status, "duration_ms": duration_ms})
