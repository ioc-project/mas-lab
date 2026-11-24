#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Canary matrix: a secret value must not appear in dumps, logs, files or holders.

Every test reads the canary from a real file store through the real resolver, so a sink that
reveals the value (or stores a revealed copy) fails the assertion.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.secrets import build_resolver, write_file_secret
from mas.ctl.workspace.config import UserConfig, WorkspaceConfig
from mas.runtime.secrets import SecretRef, SecretResolver, SecretValue, set_process_resolver
from secret_fakes import CANARY

URL_CANARY = "userinfo-canary-71bc"


def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(WorkspaceConfig, "load", lambda *a, **k: WorkspaceConfig({}))
    monkeypatch.setattr(UserConfig, "load", lambda *a, **k: UserConfig({}))


@pytest.fixture
def canary_store(tmp_path: Path) -> Iterator[SecretResolver]:
    """A file store holding the canary as secret ``openai``, bound as the process resolver."""
    directory = tmp_path / "secret-store"
    directory.mkdir(mode=0o700)
    write_file_secret(directory, "openai", CANARY)
    resolver = build_resolver(directory=directory, store="file")
    assert resolver.reveal(SecretRef(name="openai")) == CANARY  # the canary really is in the store
    set_process_resolver(resolver)
    yield resolver


def _real_value(resolver: SecretResolver) -> SecretValue:
    """The opaque value object the store hands out."""
    return resolver.store.get(SecretRef(name="openai"))


def _proxy_yaml(tmp_path: Path, *, base: str = f"https://u:{URL_CANARY}@h.example/v1") -> Path:
    path = tmp_path / "proxy.yaml"
    path.write_text(
        "apiVersion: infra/v1\nkind: LLMProxy\nmetadata: {name: arm}\n"
        "spec:\n  protocol: openai\n  proxy:\n"
        f"    api_base: {base}\n"
        "    api_key: {secret: openai}\n"
        "  models: {allowed: [m]}\n",
        encoding="utf-8",
    )
    return path


def test_compose_cli_payload_omits_resolved_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, canary_store: SecretResolver
) -> None:
    from mas.ctl.compose.runner import ComposeRequest, compose_run

    _isolate(monkeypatch)
    agent = tmp_path / "agent.yaml"
    agent.write_text(
        "apiVersion: mas/v1\nkind: Agent\nmetadata: {name: a, id: a}\nspec: {models: [{model: m}]}\n",
        encoding="utf-8",
    )
    mas = tmp_path / "mas.yaml"
    mas.write_text(
        "apiVersion: mas/v1\nkind: MAS\nmetadata: {name: demo, id: demo}\n"
        "spec:\n  entry: a\n  agents:\n    a: agent.yaml\n",
        encoding="utf-8",
    )
    result = compose_run(
        ComposeRequest(
            manifest=mas, validate=False, infra_refs=[str(_proxy_yaml(tmp_path, base="https://h.example/v1"))]
        )
    )
    payload = {
        "mas_id": result.mas_id,
        "infra_refs": result.infra_refs,
        "effective_bind": result.effective_bind,
        "placement_plan": result.placement_plan,
        "deployment": result.deployment,
    }
    text = yaml.safe_dump(payload, sort_keys=False)
    assert CANARY not in text


def test_engine_reason_must_not_include_api_base_userinfo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    canary_store: SecretResolver,
) -> None:
    from mas.ctl.session.engine_factory import build_engine
    from mas.runtime.boundary.agentcomm.protocol import AgentCommError
    from mas.runtime.driver.mocks import AutoCtxAssembler

    _isolate(monkeypatch)
    infra = ResolvedInfra(
        refs=[],
        llm_proxy={
            "api_base": f"https://u:{URL_CANARY}@h.example/v1",
            "api_key": {"secret": "openai"},
            "protocol": "openai",
        },
    )
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(AgentCommError, match="credentials") as exc:
            build_engine(
                AutoCtxAssembler(),
                {"spec": {"models": [{"model": "m"}]}},
                infra,
                anchor=tmp_path,
            )
    for text in (str(exc.value), caplog.text):
        assert URL_CANARY not in text
        assert CANARY not in text


def test_control_inspect_snapshot_omits_the_canary(canary_store: SecretResolver) -> None:
    from mas.runtime.boundary.control.rpc import _jsonable
    from mas.runtime.session.snapshot import Snapshot, SnapshotRef

    value = _real_value(canary_store)
    ref = SnapshotRef.from_state(session_id="s", parent_snapshot_id=None, turn=1, kernel={})
    snapshot = Snapshot(
        ref=ref,
        kernel={"held": value},
        working_memory=[{"note": value}],
        spec={"spec": {"api_key": SecretRef(name="openai"), "held": value}},
    )
    blob = json.dumps({"rpc": _jsonable(snapshot), "ref": _jsonable(ref)}, default=str)
    blob += json.dumps(_jsonable({"spec": snapshot.spec, "kernel": snapshot.kernel}), default=str)
    assert CANARY not in blob
    assert "<secret:openai>" in blob
    assert '"name": "openai"' in blob


def test_events_file_omits_the_canary(tmp_path: Path, canary_store: SecretResolver) -> None:
    from mas.library.standard.lib.observability.emit import JsonlFileEmitter
    from mas.library.standard.lib.observability.native.transform import (
        NativeObservabilityTransform,
        TransformContext,
    )
    from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
    from mas.library.standard.plugins.observability.native_plugin import NativeObservabilityPlugin
    from mas.runtime.boundary.obs.transition import TransitionEvent

    value = _real_value(canary_store)
    provider = OpenAILLMProvider(api_base="http://127.0.0.1:9/v1", api_key=SecretRef(name="openai"))
    assert provider._key_headers() == {"Authorization": f"Bearer {CANARY}"}
    events_path = tmp_path / "events.jsonl"
    emitter = JsonlFileEmitter(events_path)
    plugin = NativeObservabilityPlugin(
        transforms=[NativeObservabilityTransform()],
        emitters=[emitter],
        context=TransformContext(agent_id="a", run_id="run-1"),
        mas_id="m",
    )
    plugin.on_transition(
        TransitionEvent(
            contract_id="tool",
            mealy_symbol="TOOL_CALL",
            phase="start",
            agent_id="a",
            run_id="run-1",
            correlation_id=1,
            boundary_kind="engine.io",
            attributes={
                "op": "TOOL_CALL",
                "tool_name": "t",
                "tool_arguments": {"provider": repr(provider), "api_key": SecretRef(name="openai"), "held": value},
                "envelope": True,
            },
        )
    )
    emitter.close()
    text = events_path.read_text(encoding="utf-8")
    assert text.strip(), "the emitter wrote nothing"
    assert CANARY not in text


class _Unauthorized(BaseHTTPRequestHandler):
    seen: list[str] = []

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        type(self).seen.append(self.headers.get("Authorization", ""))
        body = f"nope, {self.headers.get('Authorization', '')}".encode()
        self.send_response(401)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


def test_http_layer_401_omits_the_canary(monkeypatch: pytest.MonkeyPatch, canary_store: SecretResolver) -> None:
    from library_ioa.plugins.http_layer import HttpLayer
    from mas.library.standard.lib.stack import Envelope
    from mas.runtime.boundary.agentcomm.protocol import AgentCommError

    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    _Unauthorized.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Unauthorized)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        layer = HttpLayer(
            url=f"http://127.0.0.1:{server.server_address[1]}/v1",
            headers={"Authorization": {"secret": {"name": "openai", "prefix": "Bearer "}}},
        )
        holder = json.dumps({"repr": repr(layer), "attrs": {k: repr(v) for k, v in vars(layer).items()}}, default=str)
        assert CANARY not in holder
        with pytest.raises(AgentCommError, match="401") as exc:
            asyncio.run(layer.asend(Envelope(payload=b"{}", headers={})))
    finally:
        server.shutdown()
        server.server_close()
    assert _Unauthorized.seen == [f"Bearer {CANARY}"]  # the real client really sent the revealed value
    assert CANARY not in str(exc.value)
    assert CANARY not in repr(exc.value)


def test_chat_run_leaks_the_canary_nowhere(tmp_path: Path) -> None:
    """A full ``mas-ctl chat`` against a loopback model with the canary in a file store, at debug level."""
    from fake_openai import FakeOpenAI, text

    work = tmp_path / "work"
    work.mkdir()
    directory = work / "secrets"
    directory.mkdir(mode=0o700)
    write_file_secret(directory, "openai", CANARY)
    (work / "agent.yaml").write_text(
        "apiVersion: mas/v1\nkind: Agent\nmetadata: {name: solo}\n"
        "spec:\n  description: test\n  models: [{model: canary-model}]\n",
        encoding="utf-8",
    )
    events = work / "events.jsonl"
    home = tmp_path / "home"
    home.mkdir()
    with FakeOpenAI(lambda body: text("hello")) as fake:
        infra = work / "infra.yaml"
        infra.write_text(
            "apiVersion: infra/v1\nkind: LLMProxy\nmetadata: {name: fake}\n"
            f"spec:\n  protocol: openai\n  proxy:\n    api_base: {fake.url}\n    api_key: {{secret: openai}}\n"
            "  models: {allowed: [canary-model]}\n",
            encoding="utf-8",
        )
        import os

        env = {k: v for k, v in os.environ.items() if not k.startswith(("MAS_", "OPENAI_"))}
        env.update(
            HOME=str(home),
            XDG_CONFIG_HOME=str(home / "config"),
            XDG_DATA_HOME=str(home / "data"),
            MAS_INFRA_REFS=str(infra),
            NO_PROXY="127.0.0.1,localhost",
            no_proxy="127.0.0.1,localhost",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from mas.ctl.cli.main import app; app()",
                "-vvv",
                "--secrets-dir",
                str(directory),
                "chat",
                "agent.yaml",
                "--no-cache-read",
                "--trace",
                "--events",
                "--events-file",
                str(events),
                "-q",
                "hi",
            ],
            capture_output=True,
            text=True,
            cwd=work,
            env=env,
            timeout=120,
        )
    assert result.returncode == 0, result.stderr[-2000:]
    assert fake.bodies, "the loopback model was never called"
    assert events.is_file() and events.stat().st_size > 0
    assert CANARY not in result.stdout
    assert CANARY not in result.stderr
    for path in work.rglob("*"):
        if path.is_file() and directory not in path.parents:
            assert CANARY.encode() not in path.read_bytes(), f"canary written to {path.relative_to(work)}"
