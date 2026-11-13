#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Two A2A conversations on one hosted agent never see each other's messages.

This runs the real engine (bootstrap, context assembly, live LLM engine)
against a local OpenAI-compatible server and reads what the model was sent.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from mas.ctl.compose.models import ResolvedInfra
from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime
from mas.ctl.session.exposure import make_runtime_handler
from mas.ctl.workspace.config import WorkspaceConfig

AGENT = {
    "apiVersion": "mas/v1",
    "kind": "Agent",
    "metadata": {"name": "solo"},
    "spec": {"description": "test", "models": [{"model": "gpt-4o-mini"}]},
}


class _Model(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        users = [str(m.get("content")) for m in body.get("messages", []) if m.get("role") == "user"]
        self.server.requests.append(users)  # type: ignore[attr-defined]
        out = json.dumps(
            {
                "choices": [
                    {"message": {"role": "assistant", "content": f"echo {users[-1]}"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *_args: object) -> None:
        return None


@pytest.fixture
def model_server(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Model)
    server.requests = []  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    from mas.ctl.workspace.config import UserConfig

    monkeypatch.setattr(WorkspaceConfig, "load", lambda *a, **k: WorkspaceConfig({}))
    monkeypatch.setattr(UserConfig, "load", lambda *a, **k: UserConfig({}))
    monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("MAS_CTL_MODEL", raising=False)
    monkeypatch.delenv("MAS_LLM_MODEL", raising=False)
    yield server
    server.shutdown()


def _hosted_agent(tmp_path: Path):
    options = InstantiationOptions(
        agent_manifest=json.loads(json.dumps(AGENT)),
        manifest_dir=tmp_path,
        resolved_infra=ResolvedInfra(refs=[], llm_proxy={}),
        workspace=WorkspaceConfig({}),
        validate_manifests=False,
    )
    instance, _ = instantiate_runtime(options)
    # Never let a test reach a real provider.
    assert instance.driver.engine.api_base.startswith("http://127.0.0.1")
    return instance


def test_conversations_of_one_hosted_agent_see_only_their_own_messages(model_server, tmp_path):
    handler = make_runtime_handler(_hosted_agent(tmp_path))
    answers: dict[str, list[str]] = {}

    def talk(who: str) -> None:
        answers[who] = [handler(f"{who} says {n}", session_id=who)["text"] for n in range(3)]

    threads = [threading.Thread(target=talk, args=(who,)) for who in ("alice", "bob")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for who in ("alice", "bob"):
        assert answers[who] == [f"echo {who} says {n}" for n in range(3)]
    for users in model_server.requests:
        owners = {text.split(" says ")[0] for text in users}
        assert len(owners) == 1, users
    # Each conversation's last request carries its whole history, and only that.
    last = {users[-1].split(" says ")[0]: users for users in model_server.requests if users[-1].endswith("says 2")}
    assert last["alice"] == [f"alice says {n}" for n in range(3)]
    assert last["bob"] == [f"bob says {n}" for n in range(3)]


def test_a_conversation_does_not_inherit_the_template_instances_history(model_server, tmp_path):
    template = _hosted_agent(tmp_path)
    template.run_user_text("template only")
    handler = make_runtime_handler(template)
    handler("fresh start", session_id="carol")
    assert model_server.requests[-1] == ["fresh start"]
