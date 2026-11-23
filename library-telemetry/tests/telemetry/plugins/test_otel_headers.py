#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""OTLP export headers: SecretRefs revealed when the exporter is built, sent over https or to loopback only,
and nothing from the process environment."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from mas.runtime.boundary.obs.binding import ObservabilityBinding
from mas.runtime.boundary.obs.transition import TransitionEvent
from mas.runtime.secrets import SecretRefError, SecretResolver
from mas.runtime.secrets.resolver import set_process_resolver
from mas.runtime.secrets.transport import PlainHttpRefused
from secret_fakes import MemorySecretStore

pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")

_RECORDS = [
    {"kind": "system_specification", "timestamp": 1.0, "agents": [{"id": "planner"}], "app_name": "sample-app"},
    {"kind": "mas_call_start", "call_id": "m1", "timestamp": 1.0, "agent_id": "planner", "run_id": "r1"},
    {"kind": "mas_call_end", "call_id": "m1", "status": "success", "timestamp": 2.0},
]
_HEADERS = {"Authorization": {"secret": {"name": "otlp", "prefix": "Bearer "}}, "X-Tenant": "lab"}


class _Receiver:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str]]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_args) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def auth(self) -> set[str]:
        return {h.get("authorization", "") for path, h in self.requests if path == "/v1/traces"}


@pytest.fixture
def receiver(monkeypatch) -> Iterator[_Receiver]:
    for name in ("HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy", "OTEL_EXPORTER_OTLP_HEADERS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("mas.library.telemetry.plugins.otel_plugin.project_transition", lambda *_a, **_k: _RECORDS)
    rec = _Receiver()
    yield rec
    rec.server.shutdown()


@pytest.fixture
def store() -> Iterator[MemorySecretStore]:
    memory = MemorySecretStore({"otlp": {"value": "tok-v1-canary"}})
    set_process_resolver(SecretResolver(memory))
    yield memory


def _plugin(tmp_path: Path, url: str, headers: dict, *, export: bool = True):
    from mas.library.telemetry.plugins.otel_plugin import OtelObservabilityPlugin

    config = {"output_path": str(tmp_path / "spans.jsonl"), "app_name": "sample-app", "otlp_endpoint": url}
    binding = ObservabilityBinding(plugins=["otel"], plugin_configs={"otel": {**config, "headers": headers}})
    plugin = OtelObservabilityPlugin.from_binding(binding, base_dir=tmp_path, agent_id="planner")
    if export:
        plugin.on_transition(
            TransitionEvent(contract_id="orchestrator", mealy_symbol="user_input", phase="event", agent_id="planner")
        )
        plugin.flush()
    plugin.close()
    return plugin


def test_secret_header_reaches_a_loopback_collector_and_a_rotation_the_next_build(
    tmp_path: Path, receiver: _Receiver, store, monkeypatch: pytest.MonkeyPatch
) -> None:
    # kills: HTTP_PROXY receiving the plain-http OTLP token meant for a loopback collector; a stale token
    proxy = _Receiver()
    monkeypatch.setenv("HTTP_PROXY", proxy.url)
    plugin = _plugin(tmp_path, receiver.url, _HEADERS)
    assert receiver.auth() == {"Bearer tok-v1-canary"} and proxy.requests == []
    assert "tok-v1-canary" not in repr(vars(plugin)) + (tmp_path / "spans.jsonl").read_text()
    store.put("otlp", "tok-v2-canary")
    receiver.requests.clear()
    _plugin(tmp_path, receiver.url, _HEADERS)
    assert receiver.auth() == {"Bearer tok-v2-canary"}
    proxy.server.shutdown()


@pytest.mark.parametrize("name", ["Authorization", "x-api-key", "Cookie"])
def test_a_plain_credential_header_is_refused(tmp_path: Path, receiver: _Receiver, name: str) -> None:
    with pytest.raises(SecretRefError, match="SecretRef") as raised:
        _plugin(tmp_path, receiver.url, {name: "Bearer plain-canary"}, export=False)
    assert "plain-canary" not in str(raised.value) and receiver.requests == []


@pytest.mark.parametrize("url", ["http://collector.example:4318", "http://localhost.:4318"])
def test_a_secret_header_over_plain_http_to_a_remote_collector_is_refused(tmp_path: Path, store, url: str) -> None:
    with pytest.raises(PlainHttpRefused) as raised:
        _plugin(tmp_path, url, _HEADERS, export=False)
    assert "tok-v1-canary" not in str(raised.value)


@pytest.mark.parametrize(("url", "headers"), [("https://c.example:4318", _HEADERS), ("http://c.example:4318", {})])
def test_https_takes_a_secret_header_and_plain_headers_go_anywhere(tmp_path: Path, store, url, headers) -> None:
    _plugin(tmp_path, url, headers, export=False)


_CHILD = """
import sys
from mas.runtime.entrypoint import install_host_environment
install_host_environment()
from mas.library.telemetry.conversion.session import create_otel_export
export = create_otel_export(spans_path=sys.argv[2], app_name="app", otlp_endpoint=sys.argv[1], runtime_logs=True)
with export.provider.get_tracer("t").start_as_current_span("x"):
    pass
export.provider.force_flush()
export.provider.shutdown()
"""


def test_an_entry_point_sends_no_otlp_header_or_credential_provider_from_its_environment(
    receiver: _Receiver, tmp_path: Path
) -> None:
    # kills: OTEL_EXPORTER_OTLP_*HEADERS merged by the SDK, or a provider loaded from the environment
    leak = "otlp-env-" + "Q" * 40
    env = {**os.environ, "OTEL_EXPORTER_OTLP_HEADERS": f"x-leak={leak}"}
    env |= {"OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"x-leak2={leak}"}
    env |= {"OTEL_PYTHON_EXPORTER_OTLP_HTTP_CREDENTIAL_PROVIDER": "no-such-provider"}
    cmd = [sys.executable, "-c", _CHILD, receiver.url, str(tmp_path / "spans.jsonl")]
    subprocess.run(cmd, env=env, check=True, timeout=60)
    assert receiver.requests, "the span was not exported"
    assert leak not in json.dumps(receiver.requests)
