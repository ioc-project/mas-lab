#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json


def _binding(url, ref=None):
    from mas.lab.benchmark.pipeline.ui_bridge import DemoUiBinding

    return DemoUiBinding(ingest_url=url, ref=ref or {})


def test_ingest_subscriber_posts_native_and_control(monkeypatch) -> None:
    from mas.lab.benchmark.pipeline.keys import RunKey
    from mas.lab.benchmark.pipeline.ui_bridge import IngestSubscriber, post_ingest

    posted: list[bytes] = []

    class _Resp:
        def read(self) -> bytes:
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=0.4):
        posted.append(req.data)
        return _Resp()

    monkeypatch.setattr(
        "mas.lab.benchmark.pipeline.ui_bridge._CLI_DEFAULT",
        _binding("http://127.0.0.1:8090/api/ingest", {"overlay": "overlay-b"}),
    )
    monkeypatch.setattr("mas.lab.benchmark.pipeline.ui_bridge.open_ingest", fake_urlopen)
    post_ingest({"kind": "tool_call_start", "agent_id": "specialist"})
    assert len(posted) == 1
    body = json.loads(posted[0])
    assert body["ref"]["overlay"] == "overlay-b"
    assert body["record"]["kind"] == "tool_call_start"

    posted.clear()
    sub = IngestSubscriber()
    frame = type("F", (), {"body": {"kind": "llm_call_end"}, "offset": 3, "digest": "abc", "kind": "llm_call_end"})()
    sub.on_frame(frame, key=RunKey("e", "overlay-b", "item1", "r1"))
    kinds = [json.loads(item)["record"]["kind"] for item in posted]
    assert "llm_call_end" in kinds
    assert "control.structure" in kinds


def test_post_ingest_explicit_url_does_not_mutate_env(monkeypatch) -> None:
    from mas.lab.benchmark.pipeline.ui_bridge import post_ingest

    posted: list[str] = []

    class _Resp:
        def read(self) -> bytes:
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=0.4):
        posted.append(req.full_url)
        return _Resp()

    monkeypatch.setenv("MAS_DEMO_UI_INGEST_URL", "http://env.invalid/api/ingest")
    monkeypatch.setattr("mas.lab.benchmark.pipeline.ui_bridge.open_ingest", fake_urlopen)
    post_ingest({"kind": "metric", "metrics": {"n": 1}}, url="http://127.0.0.1:9/api/ingest")
    assert posted == ["http://127.0.0.1:9/api/ingest"]
    # the library never reads the variable: without a bound target nothing is posted
    posted.clear()
    post_ingest({"kind": "metric", "metrics": {"n": 1}})
    assert posted == []


def test_post_ingest_merges_scenario_scope(monkeypatch) -> None:
    from mas.lab.benchmark.pipeline.ui_bridge import pop_demo_ui_scope, post_ingest, push_demo_ui_scope

    posted: list[bytes] = []

    class _Resp:
        def read(self) -> bytes:
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=0.4):
        posted.append(req.data)
        return _Resp()

    monkeypatch.setattr(
        "mas.lab.benchmark.pipeline.ui_bridge._CLI_DEFAULT", _binding("http://127.0.0.1:8090/api/ingest")
    )
    monkeypatch.setattr("mas.lab.benchmark.pipeline.ui_bridge.open_ingest", fake_urlopen)
    scope = push_demo_ui_scope({"scenario": "overlay-b"})
    try:
        post_ingest({"kind": "tool_call_start", "agent_id": "specialist"})
    finally:
        pop_demo_ui_scope(scope)
    body = json.loads(posted[0])
    assert body["ref"]["scenario"] == "overlay-b"
    assert body["record"]["scenario"] == "overlay-b"


def test_bound_run_target_wins_and_is_restored(monkeypatch) -> None:
    from mas.lab.benchmark.pipeline.ui_bridge import DemoUiBinding, bind_demo_ui, post_ingest

    seen: list[str] = []

    class _Resp:
        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        "mas.lab.benchmark.pipeline.ui_bridge.open_ingest",
        lambda req, timeout=0.4: (seen.append(req.full_url), _Resp())[1],
    )
    monkeypatch.setattr("mas.lab.benchmark.pipeline.ui_bridge._CLI_DEFAULT", _binding("http://cli/api"))
    with bind_demo_ui(DemoUiBinding(ingest_url="http://run/api")):
        post_ingest({"kind": "metric"})
    post_ingest({"kind": "metric"})
    assert seen == ["http://run/api", "http://cli/api"]


def test_demo_ui_binding_from_env_parses_cli_variables() -> None:
    from mas.lab.benchmark.pipeline.ui_bridge import demo_ui_binding_from_env

    got = demo_ui_binding_from_env({"MAS_DEMO_UI_INGEST_URL": " http://x/api ", "MAS_DEMO_UI_REF": '{"a": 1}'})
    assert got.ingest_url == "http://x/api" and dict(got.ref) == {"a": 1}
    assert demo_ui_binding_from_env({"MAS_DEMO_UI_REF": "not json"}).ref == {}
