#!/usr/bin/env python3
#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Smoke test of the running container stack (``task docker-smoke`` starts it).

Checks that the controller API answers and the UI is served and points at the API. It needs no model
key and no network: it fails on an image that builds but does not start.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable

# What compose.yaml gives the UI as the API address when VITE_API_BASE_URL is not set.
DEFAULT_UI_API = "http://localhost:8090"


def fetch(url: str, *, timeout: float = 5.0) -> tuple[int, str, str]:
    """Status, content type and body of a GET."""
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - loopback test URL
        return response.status, response.headers.get("Content-Type", ""), response.read().decode("utf-8", "replace")


def wait_until(check: Callable[[], None], *, timeout: float, interval: float = 2.0) -> None:
    """Run *check* until it stops raising; re-raise its last error once *timeout* has passed."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            check()
            return
        except (AssertionError, OSError, urllib.error.URLError, ValueError) as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(str(exc) or type(exc).__name__) from exc
            time.sleep(min(interval, remaining))


def check_api_health(api: str) -> None:
    status, _, body = fetch(f"{api}/api/health")
    assert status == 200, f"/api/health answered {status}"
    assert json.loads(body).get("status") == "ok", f"/api/health body: {body[:200]}"


def check_api_info(api: str) -> None:
    status, _, body = fetch(f"{api}/api/info")
    assert status == 200, f"/api/info answered {status}"
    info = json.loads(body)
    assert info.get("api_version"), "/api/info has no api_version"
    assert info.get("features"), "/api/info lists no features"


def check_ui(ui: str, expected_api: str) -> None:
    status, content_type, body = fetch(f"{ui}/")
    assert status == 200, f"UI answered {status}"
    assert "html" in content_type.lower(), f"UI content type: {content_type}"
    assert "<div" in body.lower(), "UI page has no markup"
    status, _, config = fetch(f"{ui}/env-config.js")
    assert status == 200, f"/env-config.js answered {status}"
    assert f'__MAS_LAB_API_BASE_URL__ = "{expected_api}"' in config, (
        f"the UI is not configured with the API address {expected_api}"
    )


def run(api: str, ui: str, timeout: float, ui_api: str = DEFAULT_UI_API) -> list[str]:
    """Run every check, waiting for each; return the failures, empty when the stack is healthy."""
    checks = {
        "api health": lambda: check_api_health(api),
        "api info": lambda: check_api_info(api),
        "ui": lambda: check_ui(ui, ui_api),
    }
    failures = []
    for name, check in checks.items():
        try:
            wait_until(check, timeout=timeout)
            print(f"ok    {name}")
        except AssertionError as exc:
            print(f"FAIL  {name}: {exc}")
            failures.append(f"{name}: {exc}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8090")
    parser.add_argument("--ui", default="http://127.0.0.1:8080")
    parser.add_argument("--ui-api", default=DEFAULT_UI_API, help="the API address the UI is configured with")
    parser.add_argument("--timeout", type=float, default=120.0, help="seconds to wait for each check")
    args = parser.parse_args(argv)
    return 1 if run(args.api.rstrip("/"), args.ui.rstrip("/"), args.timeout, args.ui_api) else 0


if __name__ == "__main__":
    sys.exit(main())
