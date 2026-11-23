#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The demo-ui target is bound per run (never read from the process environment)."""

from __future__ import annotations

import json
import urllib.request

from mas.lab.benchmark.pipeline import ui_bridge as ub


def test_cli_variables_parse_into_a_binding() -> None:
    env = {"MAS_DEMO_UI_INGEST_URL": " http://ui/api/ingest ", "MAS_DEMO_UI_REF": '{"lab": "x"}'}
    assert ub.demo_ui_binding_from_env(env) == ub.DemoUiBinding("http://ui/api/ingest", {"lab": "x"})
    for bad in ("{broken", "[1]"):
        assert ub.demo_ui_binding_from_env({"MAS_DEMO_UI_REF": bad}).ref == {}


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self):
        return b""


def test_a_run_binding_wins_over_the_cli_default_and_carries_the_token(monkeypatch) -> None:
    from secret_fakes import bound_secrets

    sent: list[urllib.request.Request] = []
    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open", lambda _self, req, timeout=0: sent.append(req) or _Resp()
    )
    ub.install_cli_defaults(ub.DemoUiBinding("http://cli/ingest", {"from": "cli"}))
    try:
        with ub.bind_demo_ui(None):
            pass
        with (
            ub.bind_demo_ui(ub.DemoUiBinding("https://run/ingest", {"from": "run"})),
            bound_secrets({"ui-ingest": "tok"}),
        ):
            ub.post_ingest({"name": "a"})
        ub.post_ingest({"name": "b"})
    finally:
        ub.install_cli_defaults(None)
    first, second = sent
    assert first.full_url == "https://run/ingest"
    assert first.get_header("Authorization") == "Bearer tok"
    assert json.loads(first.data)["ref"] == {"from": "run"}
    assert second.full_url == "http://cli/ingest"
    assert second.get_header("Authorization") is None


def test_no_target_posts_nothing(monkeypatch) -> None:
    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("posted"))
    )
    ub.post_ingest({"name": "a"})
