#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""pay_fare: POST /pay at the loopback payments fixture."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from mas.library.standard.lib.environment_port import get_environment_port
from mas.runtime.contracts import ToolContract


class PayFareTool(ToolContract):
    def __init__(self, url: str = "", **_: Any) -> None:
        self._url = str(url or "").rstrip("/")

    def get_name(self) -> str:
        return "pay_fare"

    def get_description(self) -> str:
        return "Pay a fare against the issuer-backed payments service."

    def get_parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        url = self._url
        if not url:
            port = get_environment_port()
            if port is None:
                return {"allowed": 0, "error": "payments fixture is not bound"}
            try:
                url = str(port.fixture_url("payments") or "").rstrip("/")
            except KeyError:
                return {"allowed": 0, "error": "payments fixture is not running"}
        if not url:
            return {"allowed": 0, "error": "payments fixture has no url"}
        try:
            raw = urlopen(
                Request(
                    url + "/pay",
                    data=b"{}",
                    method="POST",
                    headers={"Content-Type": "application/json"},
                ),
                timeout=2,
            ).read()
            return json.loads(raw)
        except (OSError, URLError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            return {"allowed": 0, "error": type(exc).__name__}
