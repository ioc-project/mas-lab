#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Each A2A conversation of a run-mas agent is wired like the run, and shares nothing with it."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mas.ctl.compose.runner import ComposeRequest, compose_run
from mas.ctl.executor.mas_session import materialize_mas_compose
from mas.ctl.executor.run_mas import _EntrySession, _QuietDisplay, open_entry_session
from mas.ctl.infra.bind import bind_session
from mas.ctl.infra.identities import agency_ids
from mas.ctl.session.engine_factory import EngineSelection
from mas.ctl.session.exposure import close_exposures, start_materialized_exposures
from mas.ctl.session.params_sidecar import params_from_mas_config
from mas.runtime.engine.simulated import SimulatedEngine

AGENT = """apiVersion: mas/v1
kind: Agent
metadata:
  name: solo
spec:
  description: test
  models:
    - model: gpt-4o-mini
"""

MAS = """apiVersion: mas/v1
kind: MAS
metadata:
  name: conversations
spec:
  agency:
    agents:
      - id: solo
        ref: agent.yaml
"""


def _selection(*_args, **_kwargs) -> EngineSelection:
    return EngineSelection(engine=SimulatedEngine(llm_next_step=lambda _cid: "STOP", stop_text="hi"), mode="injected")


def test_two_conversations_of_a_run_share_no_runtime(tmp_path):
    (tmp_path / "agent.yaml").write_text(AGENT, encoding="utf-8")
    (tmp_path / "mas.yaml").write_text(MAS, encoding="utf-8")
    with patch("mas.ctl.session.bootstrap.build_engine", side_effect=_selection):
        result = compose_run(
            ComposeRequest(manifest=tmp_path / "mas.yaml", validate=False, infra_refs=["standard:openai"])
        )
        result.resolved_infra = bind_session(result.resolved_infra, {}, agent_ids=agency_ids(result.mas_config))
        args = _EntrySession(
            entry="solo",
            declared="solo",
            delegates=[],
            runtime_params=params_from_mas_config(result.mas_config),
            flavour_spec={},
            session_interactive=False,
        )
        opened = []
        for _ in range(2):
            fresh = materialize_mas_compose(result, mas_base_dir=tmp_path)
            opened.append(open_entry_session(fresh, args, display=_QuietDisplay()))

    first, second = opened
    assert first.instance is not second.instance
    assert first.instance.driver.engine is not second.instance.driver.engine
    assert first.instance.driver.ctx is not second.instance.driver.ctx
    assert first.session_id != second.session_id
    assert first.working_memory_registry is not second.working_memory_registry


def test_run_mas_listener_opens_a_fresh_copy_per_context_id(tmp_path):
    """The listener must call the run-mas factory, not the template instance."""
    (tmp_path / "agent.yaml").write_text(AGENT, encoding="utf-8")
    (tmp_path / "mas.yaml").write_text(MAS, encoding="utf-8")
    with patch("mas.ctl.session.bootstrap.build_engine", side_effect=_selection):
        result = compose_run(
            ComposeRequest(manifest=tmp_path / "mas.yaml", validate=False, infra_refs=["standard:openai"])
        )
        result.resolved_infra = bind_session(result.resolved_infra, {}, agent_ids=agency_ids(result.mas_config))
        result.resolved_infra.applications = {
            "solo": {"protocol": "a2a", "usage": "deploy", "url": "http://127.0.0.1:9001"}
        }
        args = _EntrySession(
            entry="solo",
            declared="solo",
            delegates=[],
            runtime_params=params_from_mas_config(result.mas_config),
            flavour_spec={},
            session_interactive=False,
        )
        materialized = materialize_mas_compose(result, mas_base_dir=tmp_path)
        built: list[object] = []

        def factory():
            fresh = materialize_mas_compose(result, mas_base_dir=tmp_path)
            opened = open_entry_session(fresh, args, display=_QuietDisplay())
            built.append(opened.instance)
            return opened.instance

        captured: dict[str, object] = {}

        def _create(spec_key: str, binding=None, **kwargs):
            del spec_key, binding, kwargs
            handle = MagicMock()
            handle.host = "127.0.0.1"
            handle.port = 9001
            handle.server = SimpleNamespace(thread=SimpleNamespace(is_alive=lambda: True))
            exposure = MagicMock()

            def serve_background(manifest, handler):
                del manifest
                captured["handler"] = handler
                return handle

            exposure.serve_background.side_effect = serve_background
            return exposure

        with patch("mas.runtime.registry.get_registry", return_value=SimpleNamespace(create=_create)):
            with patch("mas.ctl.session.exposure._wait_listening"):
                handles = start_materialized_exposures(
                    materialized,
                    entry_id="solo",
                    conversation_factories={"solo": factory},
                )
        try:
            handler = captured["handler"]
            assert callable(handler)
            handler("from alice", session_id="alice")
            handler("from bob", session_id="bob")
        finally:
            close_exposures(handles)

    assert len(built) == 2
    assert built[0] is not built[1]
    assert built[0] is not materialized.materialized.instances["solo"]
    assert built[0].driver.engine is not built[1].driver.engine
    assert built[0].driver.ctx.turn_index == 1
    assert built[1].driver.ctx.turn_index == 1
    assert [user for user, _ in built[0].driver.ctx.turn_history] == ["from alice"]
    assert [user for user, _ in built[1].driver.ctx.turn_history] == ["from bob"]
