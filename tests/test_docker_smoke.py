#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The container smoke test passes on a healthy stack and names what is wrong on a broken one."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "docker_smoke.py"


@pytest.fixture(scope="module")
def smoke():
    spec = importlib.util.spec_from_file_location("docker_smoke", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Stack:
    """A loopback server standing in for the API and the UI; ``routes`` maps a path to (status, type, body)."""

    def __init__(self, routes: dict[str, tuple[int, str, str]]) -> None:
        routes_ = routes

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                status, content_type, body = routes_.get(self.path, (404, "text/plain", "not found"))
                payload = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _healthy(api_url: str = "") -> dict[str, tuple[int, str, str]]:
    return {
        "/api/health": (200, "application/json", json.dumps({"status": "ok"})),
        "/api/info": (200, "application/json", json.dumps({"api_version": "0.3.0", "features": ["scopes"]})),
        "/": (200, "text/html", "<!doctype html><div id='root'></div>"),
        "/env-config.js": (200, "text/javascript", f'window.__MAS_LAB_API_BASE_URL__ = "{api_url}";'),
    }


@pytest.fixture
def stack():
    started: list[_Stack] = []

    def start(routes):
        server = _Stack(routes)
        started.append(server)
        return server

    yield start
    for server in started:
        server.close()


def test_a_healthy_stack_passes(smoke, stack) -> None:
    api = stack(_healthy())
    ui = stack(_healthy(api.url))
    assert smoke.run(api.url, ui.url, timeout=2, ui_api=api.url) == []


def test_an_unhealthy_api_is_named(smoke, stack) -> None:
    routes = _healthy()
    routes["/api/health"] = (200, "application/json", json.dumps({"status": "degraded"}))
    api = stack(routes)
    ui = stack(_healthy(api.url))
    failures = smoke.run(api.url, ui.url, timeout=0.5, ui_api=api.url)
    assert len(failures) == 1 and failures[0].startswith("api health")


def test_an_api_without_features_fails_the_info_check(smoke, stack) -> None:
    routes = _healthy()
    routes["/api/info"] = (200, "application/json", json.dumps({"api_version": "0.3.0", "features": []}))
    api = stack(routes)
    ui = stack(_healthy(api.url))
    assert [f.split(":")[0] for f in smoke.run(api.url, ui.url, timeout=0.5, ui_api=api.url)] == ["api info"]


def test_a_ui_pointing_at_another_api_fails(smoke, stack) -> None:
    api = stack(_healthy())
    ui = stack(_healthy("http://elsewhere:1"))
    failures = smoke.run(api.url, ui.url, timeout=0.5, ui_api=api.url)
    assert len(failures) == 1 and "not configured with the API address" in failures[0]


def test_a_server_that_is_down_fails_after_the_timeout(smoke) -> None:
    failures = smoke.run("http://127.0.0.1:9", "http://127.0.0.1:9", timeout=0.3)
    assert [f.split(":")[0] for f in failures] == ["api health", "api info", "ui"]


def test_wait_until_returns_as_soon_as_the_check_passes(smoke) -> None:
    calls = []

    def check() -> None:
        calls.append(1)
        assert len(calls) >= 3, "not yet"

    smoke.wait_until(check, timeout=5, interval=0.01)
    assert len(calls) == 3


def test_main_exit_code_follows_the_result(smoke, stack) -> None:
    api = stack(_healthy())
    ui = stack(_healthy(api.url))
    assert smoke.main(["--api", api.url, "--ui", ui.url, "--ui-api", api.url, "--timeout", "1"]) == 0
    assert smoke.main(["--api", "http://127.0.0.1:9", "--ui", ui.url, "--timeout", "0.2"]) == 1


def test_the_task_and_the_workflow_run_the_script() -> None:
    root = SCRIPT.parents[1]
    assert "python3 scripts/docker_smoke.py" in (root / "Taskfile.yml").read_text()
    workflow = (root / ".github/workflows/docker-check.yml").read_text()
    assert "task docker-smoke" in workflow
    assert 'cron: "41 3 * * 1"' in workflow  # weekly, not daily
    docker = (root / ".github/workflows/docker.yml").read_text()
    assert "uses: ./.github/workflows/docker-check.yml" in docker
    assert docker.count("needs: check") == 2  # both images wait for the check


def test_the_called_check_asks_for_no_permission_its_caller_lacks() -> None:
    """A called workflow cannot exceed its caller: docker.yml has no ``issues`` permission."""
    root = SCRIPT.parents[1] / ".github/workflows"
    assert "issues:" not in (root / "docker-check.yml").read_text()
    report = (root / "docker-check-report.yml").read_text()
    assert "issues: write" in report
    assert 'workflows: ["Docker check"]' in report


def test_the_backend_image_copies_the_readme_the_root_package_declares() -> None:
    """``uv sync`` builds the root package, and hatchling fails when its readme file is missing."""
    import tomllib

    root = SCRIPT.parents[1]
    readme = tomllib.loads((root / "pyproject.toml").read_text())["project"]["readme"]
    lines = (root / "docker/backend/Dockerfile").read_text().splitlines()
    copy_of_root_metadata = next(line for line in lines if line.startswith("COPY pyproject.toml"))
    assert readme in copy_of_root_metadata.split()
    sync = next(i for i, line in enumerate(lines) if line.startswith("RUN uv sync"))
    assert lines.index(copy_of_root_metadata) < sync
    assert (root / readme).is_file()


def test_the_backend_entrypoint_does_not_install_dev_dependencies_at_start() -> None:
    """``uv run`` syncs the dev group unless told not to: every new container would download it again."""
    entrypoint = (SCRIPT.parents[1] / "docker/backend/entrypoint.sh").read_text()
    execs = [line.strip() for line in entrypoint.splitlines() if line.strip().startswith("exec uv run")]
    assert execs == ['exec uv run --no-sync "$@"']
