#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from mas.ctl.benchmark.runner_dispatch import is_mas_manifest_kind, mas_manifest_path
from mas.ctl.compose.models import AgentBindSlice, EffectiveBindManifest, PlacementPlan, ResolvedInfra
from mas.ctl.compose.runner import ComposeResult
from mas.ctl.executor.mas_session import (
    MaterializedMas,
    agent_manifest_label,
    entry_agent_id,
    make_workflow_send,
    prepare_delegation_entry_session,
    resolve_entry_pattern_plugin_id,
    wire_peer_delegation,
)
from mas.library.standard.lib.stack.handle import SessionTurn
from mas.runtime.boundary.context.working_memory_registry import (
    WorkingMemoryConfig,
    reset_working_memory_registry,
)
from mas.runtime.driver.mocks import AutoCtxAssembler


def test_entry_agent_id_from_workflow_entry():
    mas = {"spec": {"workflow": {"entry": "moderator"}}}
    assert entry_agent_id(mas) == "moderator"


def test_declared_entry_id_is_empty_without_a_named_entry():
    from mas.ctl.infra.identities import declared_entry_id

    mas = {"spec": {"agency": {"agents": [{"id": "desk"}, {"id": "bill"}]}}}
    assert declared_entry_id(mas) == ""
    assert entry_agent_id(mas) == "desk"


def test_agent_manifest_label_from_loaded_runtime_dict(tmp_path: Path):
    agent_path = tmp_path / "agent.yaml"
    agent_path.write_text("kind: agent\nmetadata:\n  name: qa-agent\n", encoding="utf-8")
    runtime_cfg = {
        "_loaded_mas_raw": True,
        "mas": {"entry_agent": "qa-agent"},
        "agents": [{"id": "qa-agent", "name": "qa-agent"}],
    }
    assert agent_manifest_label(runtime_cfg, agent_path) == "qa-agent"
    assert agent_manifest_label(runtime_cfg, tmp_path / "agent.yaml") != "agent"


def test_agent_manifest_label_prefers_metadata_over_stem(tmp_path: Path):
    agent_path = tmp_path / "agent.yaml"
    doc = {"metadata": {"name": "qa-agent"}, "spec": {}}
    assert agent_manifest_label(doc, agent_path) == "qa-agent"


def test_resolve_entry_pattern_plugin_id_from_bind():
    compose = ComposeResult(
        mas_id="test",
        mas_config={},
        effective_bind={},
        placement_plan={},
        deployment={},
        infra_refs=[],
        bind=EffectiveBindManifest(
            mas_id="test",
            spec_revision="",
            runtime_id="mas-runtime-py",
            deployment_name="local",
            agents=[AgentBindSlice(agent_id="alpha", pattern_plugin_id="cot@v1")],
        ),
        plan=PlacementPlan(),
    )
    assert resolve_entry_pattern_plugin_id(compose, "alpha") == "cot@v1"


def test_resolve_entry_pattern_plugin_id_falls_back_to_manifest():
    compose = ComposeResult(
        mas_id="test",
        mas_config={},
        effective_bind={},
        placement_plan={},
        deployment={},
        infra_refs=[],
        bind=EffectiveBindManifest(
            mas_id="test",
            spec_revision="",
            runtime_id="mas-runtime-py",
            deployment_name="local",
            agents=[],
        ),
        plan=PlacementPlan(),
    )
    manifest = {"spec": {"design_pattern": {"type": "plan-execute"}}}
    assert resolve_entry_pattern_plugin_id(compose, "alpha", entry_manifest=manifest) == "plan_execute@v1"


def test_prepare_delegation_entry_session_sets_driver_agent_id(tmp_path: Path):
    driver = SimpleNamespace(agent_id=None, engine=MagicMock())
    instance = SimpleNamespace(driver=driver)
    compose = ComposeResult(
        mas_id="demo",
        mas_config={"spec": {"workflow": {"entry": "moderator"}}},
        effective_bind={},
        placement_plan={},
        deployment={},
        infra_refs=[],
        bind=EffectiveBindManifest(
            mas_id="demo",
            spec_revision="",
            runtime_id="mas-runtime-py",
            deployment_name="local",
            agents=[AgentBindSlice(agent_id="moderator", pattern_plugin_id="react@v1")],
        ),
        plan=PlacementPlan(),
    )
    materialized = MaterializedMas(
        compose=compose,
        materialized=SimpleNamespace(instances={"moderator": instance}),
        mas_base_dir=tmp_path,
    )
    entry_manifest = {"metadata": {"name": "moderator"}, "spec": {}}
    with patch("mas.ctl.executor.mas_session.enrich_entry_agent_for_delegation", return_value=entry_manifest):
        with patch("mas.ctl.executor.mas_session.wire_entry_engine_delegation"):
            prepare_delegation_entry_session(
                materialized,
                entry_id="moderator",
                entry_manifest=entry_manifest,
            )
    assert driver.agent_id == "moderator"


def _mas_config_two_level_delegation() -> dict:
    """moderator -> schedule_agent -> concierge_agent: schedule_agent is
    itself a delegate of moderator's, AND declares its own delegates_to —
    the shape wire_entry_engine_delegation alone (entry-only) can't wire."""
    return {
        "spec": {
            "workflow": {
                "entry": "moderator",
                "nodes": [
                    {"id": "moderator", "delegates_to": ["schedule_agent"]},
                    {"id": "schedule_agent", "delegates_to": ["concierge_agent"]},
                    {"id": "concierge_agent"},
                ],
            }
        }
    }


def _materialized_with_agents(agent_ids: list[str], tmp_path: Path):
    # `inner=None` is required, not cosmetic: leaf_engine() walks `.inner`
    # looking for a leaf (a wrapper with no further `.inner`). A bare
    # MagicMock() auto-vivifies a fresh child mock on every attribute access,
    # including `.inner` — so without this, leaf_engine() never terminates
    # and the process runs away (confirmed: unbounded memory growth).
    instances = {
        aid: SimpleNamespace(driver=SimpleNamespace(agent_id=None, engine=MagicMock(inner=None))) for aid in agent_ids
    }
    compose = ComposeResult(
        mas_id="demo",
        mas_config=_mas_config_two_level_delegation(),
        effective_bind={},
        placement_plan={},
        deployment={},
        infra_refs=[],
        bind=EffectiveBindManifest(
            mas_id="demo",
            spec_revision="",
            runtime_id="mas-runtime-py",
            deployment_name="local",
            agents=[AgentBindSlice(agent_id=aid, pattern_plugin_id="react@v1") for aid in agent_ids],
        ),
        plan=PlacementPlan(),
    )
    return MaterializedMas(
        compose=compose,
        materialized=SimpleNamespace(instances=instances),
        mas_base_dir=tmp_path,
    )


def test_wire_peer_delegation_wires_non_entry_agent_with_own_peers(tmp_path: Path):
    """schedule_agent isn't the entry, but its own workflow node declares
    delegates_to=[concierge_agent] — wire_peer_delegation must wire ITS
    engine too, not just the entry's. Regression for the "nested/multi-level
    delegation isn't a thing here" gap (mas_session.py's old from_agent-is-
    always-entry assumption)."""
    materialized = _materialized_with_agents(["moderator", "schedule_agent", "concierge_agent"], tmp_path)

    def _fake_manifest(bind, agent_id):
        return {"metadata": {"name": agent_id}, "spec": {}}

    wired_engines = []
    with patch("mas.ctl.executor.mas_session.load_agent_manifest_from_bind", side_effect=_fake_manifest):
        with patch(
            "mas.ctl.executor.mas_session.wire_entry_engine_delegation",
            side_effect=lambda engine, *a, **k: wired_engines.append(engine),
        ):
            newly_wired = wire_peer_delegation(
                materialized,
                entry_id="moderator",
                already_wired={"moderator"},
            )

    # moderator is already_wired (handled by prepare_delegation_entry_session
    # separately) — only schedule_agent has its own peers among the rest.
    assert newly_wired == ["schedule_agent"]
    assert wired_engines == [materialized.materialized.instances["schedule_agent"].driver.engine]


def test_wire_peer_delegation_does_not_load_a2a_without_explicit_protocol(tmp_path: Path):
    """Used Application endpoints without protocol: a2a must not construct A2A."""
    materialized = _materialized_with_agents(["moderator", "schedule_agent", "concierge_agent"], tmp_path)
    materialized.compose.mas_config.setdefault("spec", {})["agency"] = {"agents": [{"id": "remote-peer"}]}
    materialized.compose.resolved_infra = ResolvedInfra(
        applications={"remote-peer": {"url": "http://127.0.0.1:9", "usage": "use"}}
    )

    created: list[tuple[str, dict | None]] = []

    def _create(spec_key: str, binding=None, **kwargs):
        created.append((spec_key, binding))
        return MagicMock()

    def _fake_manifest(bind, agent_id):
        return {"metadata": {"name": agent_id}, "spec": {}}

    with patch("mas.ctl.executor.mas_session.load_agent_manifest_from_bind", side_effect=_fake_manifest):
        with patch("mas.ctl.executor.mas_session.wire_entry_engine_delegation"):
            with patch("mas.runtime.registry.get_registry", return_value=SimpleNamespace(create=_create)):
                wire_peer_delegation(materialized, entry_id="moderator", already_wired=set())

    assert all((binding or {}).get("type") != "a2a" for _, binding in created)
    assert any(binding in (None, {}) for _, binding in created)


def test_wire_peer_delegation_uses_the_library_default_link_not_mas_agent_comm(tmp_path: Path):
    """The in-process link is the registry default (``binding=None``); ctl names no
    variant, and MAS ``spec.agent_comm`` (refused by the MAS schema) is not read."""
    materialized = _materialized_with_agents(["moderator", "schedule_agent", "concierge_agent"], tmp_path)
    materialized.compose.mas_config.setdefault("spec", {})["agent_comm"] = {"protocol": "probe"}
    created: list[tuple[str, dict | None]] = []
    probe = MagicMock(name="probe-comm")

    def _create(spec_key: str, binding=None, **kwargs):
        created.append((spec_key, binding))
        return probe

    def _fake_manifest(bind, agent_id):
        return {"metadata": {"name": agent_id}, "spec": {}}

    wired_comms: list[object] = []
    with patch("mas.ctl.executor.mas_session.load_agent_manifest_from_bind", side_effect=_fake_manifest):
        with patch(
            "mas.ctl.executor.mas_session.wire_entry_engine_delegation",
            side_effect=lambda engine, *a, **k: wired_comms.append(k.get("comm")),
        ):
            with patch("mas.runtime.registry.get_registry", return_value=SimpleNamespace(create=_create)):
                wire_peer_delegation(materialized, entry_id="moderator", already_wired=set())

    assert created == [("agent_comm", None)]
    assert wired_comms == [probe, probe]


def test_prepare_delegation_entry_session_uses_the_library_default_link(tmp_path: Path):
    driver = SimpleNamespace(agent_id=None, engine=MagicMock(inner=None))
    instance = SimpleNamespace(driver=driver)
    compose = ComposeResult(
        mas_id="demo",
        mas_config={
            "spec": {
                "workflow": {"entry": "moderator"},
                "agent_comm": {"protocol": "probe"},
            }
        },
        effective_bind={},
        placement_plan={},
        deployment={},
        infra_refs=[],
        bind=EffectiveBindManifest(
            mas_id="demo",
            spec_revision="",
            runtime_id="mas-runtime-py",
            deployment_name="local",
            agents=[AgentBindSlice(agent_id="moderator", pattern_plugin_id="react@v1")],
        ),
        plan=PlacementPlan(),
    )
    materialized = MaterializedMas(
        compose=compose,
        materialized=SimpleNamespace(instances={"moderator": instance}),
        mas_base_dir=tmp_path,
    )
    entry_manifest = {"metadata": {"name": "moderator"}, "spec": {}}
    created: list[tuple[str, dict | None]] = []
    probe = MagicMock(name="probe-comm")

    def _create(spec_key: str, binding=None, **kwargs):
        created.append((spec_key, binding))
        return probe

    wired_comms: list[object] = []
    with patch("mas.ctl.executor.mas_session.enrich_entry_agent_for_delegation", return_value=entry_manifest):
        with patch(
            "mas.ctl.executor.mas_session.wire_entry_engine_delegation",
            side_effect=lambda engine, *a, **k: wired_comms.append(k.get("comm")),
        ):
            with patch("mas.ctl.executor.subagent_spawner.wire_subagent_spawning"):
                with patch("mas.runtime.registry.get_registry", return_value=SimpleNamespace(create=_create)):
                    prepare_delegation_entry_session(
                        materialized,
                        entry_id="moderator",
                        entry_manifest=entry_manifest,
                    )

    assert created == [("agent_comm", None)]
    assert wired_comms == [probe]


def test_wire_peer_delegation_skips_agents_without_their_own_peers(tmp_path: Path):
    """concierge_agent has no delegates_to of its own — must not be wired,
    even though it's a valid, materialized agent in the workflow."""
    materialized = _materialized_with_agents(["moderator", "schedule_agent", "concierge_agent"], tmp_path)

    def _fake_manifest(bind, agent_id):
        return {"metadata": {"name": agent_id}, "spec": {}}

    with patch("mas.ctl.executor.mas_session.load_agent_manifest_from_bind", side_effect=_fake_manifest):
        with patch("mas.ctl.executor.mas_session.wire_entry_engine_delegation") as mock_wire:
            wire_peer_delegation(
                materialized,
                entry_id="moderator",
                already_wired=set(),
            )

    wired_ids = {call.kwargs.get("entry_agent_id") for call in mock_wire.call_args_list}
    assert wired_ids == {"moderator", "schedule_agent"}
    assert "concierge_agent" not in wired_ids


def test_is_mas_manifest_kind_workflow_app(tmp_path: Path):
    for kind in ("workflow", "app"):
        path = tmp_path / f"{kind}.yaml"
        path.write_text(f"kind: {kind}\n", encoding="utf-8")
        assert is_mas_manifest_kind({"kind": kind}, path) is True
        assert mas_manifest_path({"kind": kind}, path) == path


def _fake_materialized_for_send(agent_ids: list[str]) -> SimpleNamespace:
    instances = {aid: SimpleNamespace(driver=SimpleNamespace(agent_id=None)) for aid in agent_ids}
    return SimpleNamespace(instances=instances, bus=None)


def test_send_gives_each_delegation_invocation_a_unique_turn_id():
    """Regression: repeated delegation to the SAME agent within one turn used
    to always resolve to turn_id="u1" when turn_id was omitted. caller_call_id
    (already a real, globally-unique id per delegation invocation) is reused
    as turn_id, making exec_id unique per call even when the SessionController
    is reused so trace plugins do not stack.
    """
    materialized = _fake_materialized_for_send(["moderator", "schedule_agent"])
    captured_turn_ids: list[str] = []

    class _FakeResult:
        text = "ok"
        awaiting_hitl = False

    def _fake_run_turn(self, prompt, *, turn_id=None, parent_call_id="", auto_hitl=True):
        captured_turn_ids.append(turn_id)
        return _FakeResult()

    with patch("mas.ctl.executor.mas_session.SessionController.run_turn", _fake_run_turn):
        with patch("mas.ctl.executor.mas_session.turn_failed", return_value=False):
            send = make_workflow_send(materialized, display=None, verbose=0, from_agent="moderator")
            assert isinstance(send, SessionTurn)  # a chain link can await it (``asend``)
            send("schedule_agent", "turn 1", caller_call_id="deleg-call-aaa")
            send("schedule_agent", "turn 2", caller_call_id="deleg-call-bbb")
            send("schedule_agent", "turn 3", caller_call_id="deleg-call-ccc")

    assert len(captured_turn_ids) == 3
    assert len(set(captured_turn_ids)) == 3, captured_turn_ids
    assert captured_turn_ids == ["deleg-call-aaa", "deleg-call-bbb", "deleg-call-ccc"]


def test_send_reuses_session_controller_so_trace_plugins_do_not_stack():
    """A new SessionController per send() subscribed a fresh CliTrace plugin
    on the reused specialist driver. Later replies printed N times with
    different +offsets from stale turn_start_mono baselines."""
    materialized = _fake_materialized_for_send(["moderator", "revenue_ops"])
    constructed: list[object] = []
    orig_init = None

    from mas.ctl.session.controller import SessionController as RealController

    orig_init = RealController.__init__

    def _counting_init(self, *args, **kwargs):
        constructed.append(self)
        return orig_init(self, *args, **kwargs)

    class _FakeResult:
        text = "ok"
        awaiting_hitl = False

    def _fake_run_turn(self, prompt, *, turn_id=None, parent_call_id="", auto_hitl=True):
        return _FakeResult()

    with patch.object(RealController, "__init__", _counting_init):
        with patch("mas.ctl.executor.mas_session.SessionController.run_turn", _fake_run_turn):
            with patch("mas.ctl.executor.mas_session.turn_failed", return_value=False):
                send = make_workflow_send(materialized, display=None, verbose=0, from_agent="moderator")
                send("revenue_ops", "turn 1", caller_call_id="c1")
                send("revenue_ops", "turn 2", caller_call_id="c2")
                send("revenue_ops", "turn 3", caller_call_id="c3")

    assert len(constructed) == 1
    assert constructed[0].caller_agent_id == "moderator"


def test_send_sequential_workflow_calls_without_caller_call_id_still_get_unique_turn_ids():
    """Sequential-workflow steps (no caller_call_id — not a delegation) must
    still get distinct turn_ids across repeated calls to the same agent, via
    the per-closure monotonic fallback counter — not an heuristic guess, an
    exact per-run sequence number."""
    materialized = _fake_materialized_for_send(["worker"])
    captured_turn_ids: list[str] = []

    class _FakeResult:
        text = "ok"
        awaiting_hitl = False

    def _fake_run_turn(self, prompt, *, turn_id=None, parent_call_id="", auto_hitl=True):
        captured_turn_ids.append(turn_id)
        return _FakeResult()

    with patch("mas.ctl.executor.mas_session.SessionController.run_turn", _fake_run_turn):
        with patch("mas.ctl.executor.mas_session.turn_failed", return_value=False):
            send = make_workflow_send(materialized, display=None, verbose=0, from_agent="worker")
            send("worker", "step 1")
            send("worker", "step 2")

    assert len(set(captured_turn_ids)) == 2, captured_turn_ids


def _capturing_run_turn(captured_session_ids: list[str]):
    """Fake ONLY SessionController.run_turn — construction runs for real, so
    `self` here is a genuine SessionController and `self.session_id` reflects
    whatever the real dataclass field/`__post_init__` actually produced.

    Deliberately NOT faking __init__ too: an earlier version of these tests
    faked __init__ with its own hand-written `session_id=""` parameter,
    which meant renaming/removing SessionController.session_id in the real
    class would go completely undetected here — the fake would happily keep
    accepting a `session_id` kwarg no real class had anymore. Letting the
    real constructor run means that exact rename would raise
    `TypeError: unexpected keyword argument 'session_id'` out of send(),
    which is what these tests are actually supposed to catch.
    """

    class _FakeResult:
        text = "ok"
        awaiting_hitl = False

    def _fake_run_turn(self, prompt, *, turn_id=None, parent_call_id="", auto_hitl=True):
        captured_session_ids.append(self.session_id)
        return _FakeResult()

    return _fake_run_turn


def test_make_workflow_send_threads_explicit_session_id_into_every_delegate():
    materialized = _fake_materialized_for_send(["moderator", "schedule_agent"])
    captured: list[str] = []
    fake_run_turn = _capturing_run_turn(captured)

    with patch("mas.ctl.executor.mas_session.SessionController.run_turn", fake_run_turn):
        with patch("mas.ctl.executor.mas_session.turn_failed", return_value=False):
            send = make_workflow_send(
                materialized,
                display=None,
                verbose=0,
                from_agent="moderator",
                session_id="entry-session-abc",
            )
            send("schedule_agent", "q1", caller_call_id="c1")
            send("schedule_agent", "q2", caller_call_id="c2")

    assert captured == ["entry-session-abc", "entry-session-abc"]


def test_make_workflow_send_mints_one_session_id_and_reuses_it_when_none_given():
    """No session_id passed in -> make_workflow_send mints exactly one and
    every delegate call within THIS closure reuses it (still shared across
    the closure's whole lifetime, just not tied to any caller's value)."""
    materialized = _fake_materialized_for_send(["moderator", "schedule_agent"])
    captured: list[str] = []
    fake_run_turn = _capturing_run_turn(captured)

    with patch("mas.ctl.executor.mas_session.SessionController.run_turn", fake_run_turn):
        with patch("mas.ctl.executor.mas_session.turn_failed", return_value=False):
            send = make_workflow_send(materialized, display=None, verbose=0, from_agent="moderator")
            send("schedule_agent", "q1", caller_call_id="c1")
            send("schedule_agent", "q2", caller_call_id="c2")

    assert len(captured) == 2
    assert captured[0] == captured[1]
    import uuid

    uuid.UUID(captured[0])  # a real minted uuid, not a placeholder


def test_two_separate_workflow_send_closures_get_different_session_ids_when_unspecified():
    materialized = _fake_materialized_for_send(["moderator", "schedule_agent"])
    captured: list[str] = []
    fake_run_turn = _capturing_run_turn(captured)

    with patch("mas.ctl.executor.mas_session.SessionController.run_turn", fake_run_turn):
        with patch("mas.ctl.executor.mas_session.turn_failed", return_value=False):
            send_a = make_workflow_send(materialized, display=None, verbose=0, from_agent="moderator")
            send_a("schedule_agent", "q1", caller_call_id="c1")
            send_b = make_workflow_send(materialized, display=None, verbose=0, from_agent="moderator")
            send_b("schedule_agent", "q2", caller_call_id="c2")

    assert captured[0] != captured[1]


def _fake_instance_with_ctx(*, persistent: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        driver=SimpleNamespace(agent_id=None, ctx=AutoCtxAssembler()),
        working_memory=WorkingMemoryConfig(persistent=persistent),
    )


def _capturing_working_memory_key_run_turn(captured: list[str]):
    """Fake SessionController.run_turn that captures self.working_memory_key
    (real __init__/__post_init__ ran -- only the method is patched) --
    proves what send() actually wires, without needing a real kernel/driver.

    The real persistence/isolation/persistent:false behavior itself is now
    owned entirely by SessionController (sync_working_memory_in/out in
    _run_user_turn/_finalize_turn) and covered end-to-end with a REAL
    SessionController + RuntimeInstance in
    test_session_controller_working_memory.py -- this file only needs to
    prove make_workflow_send resolves and passes the right key through."""

    def _fake_run_turn(self, prompt, *, turn_id=None, parent_call_id="", auto_hitl=True):
        captured.append(self._working_memory_key())
        return SimpleNamespace(text="ok", awaiting_hitl=False)

    return _fake_run_turn


def test_send_passes_session_id_as_the_default_working_memory_key():
    """No context_id given -> the constructed SessionController's working
    memory syncs under the session's default bucket (session_id)."""
    reset_working_memory_registry()
    session_id = "session-xyz"
    captured: list[str] = []
    materialized = SimpleNamespace(instances={"schedule_agent": _fake_instance_with_ctx()}, bus=None)

    with patch(
        "mas.ctl.executor.mas_session.SessionController.run_turn",
        _capturing_working_memory_key_run_turn(captured),
    ):
        with patch("mas.ctl.executor.mas_session.turn_failed", return_value=False):
            send = make_workflow_send(
                materialized, display=None, verbose=0, from_agent="moderator", session_id=session_id
            )
            send("schedule_agent", "Please generate Foo", caller_call_id="c1")

    assert captured == [session_id]


def test_send_passes_context_id_as_the_working_memory_key_when_given():
    """An explicit context_id (LLM-chosen, via the delegate tool's optional
    context_id argument) overrides the session default as the working-memory
    bucket key -- lets a moderator run more than one context with the same
    peer inside one session."""
    reset_working_memory_registry()
    session_id = "session-two-contexts"
    captured: list[str] = []
    materialized = SimpleNamespace(instances={"schedule_agent": _fake_instance_with_ctx()}, bus=None)

    with patch(
        "mas.ctl.executor.mas_session.SessionController.run_turn",
        _capturing_working_memory_key_run_turn(captured),
    ):
        with patch("mas.ctl.executor.mas_session.turn_failed", return_value=False):
            send = make_workflow_send(
                materialized, display=None, verbose=0, from_agent="moderator", session_id=session_id
            )
            send("schedule_agent", "plan a Paris trip", caller_call_id="c1", context_id="trip-paris")
            send("schedule_agent", "plan a Tokyo trip", caller_call_id="c2", context_id="trip-tokyo")
            send("schedule_agent", "plan a Rome trip", caller_call_id="c3")  # no context_id

    assert captured == ["trip-paris", "trip-tokyo", session_id]


def test_workflow_send_closures_own_distinct_working_memory_registries():
    registries = []
    materialized = SimpleNamespace(
        instances={"schedule_agent": _fake_instance_with_ctx()},
        bus=None,
    )

    def capture_registry(self, _prompt, *, turn_id=None, parent_call_id="", auto_hitl=True):
        registries.append(self.working_memory_registry)
        return SimpleNamespace(text="ok", awaiting_hitl=False)

    with (
        patch(
            "mas.ctl.executor.mas_session.SessionController.run_turn",
            capture_registry,
        ),
        patch("mas.ctl.executor.mas_session.turn_failed", return_value=False),
    ):
        first = make_workflow_send(
            materialized,
            display=None,
            verbose=0,
            from_agent="moderator",
            session_id="same-id",
        )
        second = make_workflow_send(
            materialized,
            display=None,
            verbose=0,
            from_agent="moderator",
            session_id="same-id",
        )
        first("schedule_agent", "first", caller_call_id="first-call")
        second("schedule_agent", "second", caller_call_id="second-call")

    assert len(registries) == 2
    assert registries[0] is not registries[1]


def test_prepare_delegation_entry_session_mints_session_id_when_none_given(tmp_path: Path):
    driver = SimpleNamespace(agent_id=None, engine=MagicMock())
    instance = SimpleNamespace(driver=driver)
    compose = ComposeResult(
        mas_id="demo",
        mas_config={"spec": {"workflow": {"entry": "moderator"}}},
        effective_bind={},
        placement_plan={},
        deployment={},
        infra_refs=[],
        bind=EffectiveBindManifest(
            mas_id="demo",
            spec_revision="",
            runtime_id="mas-runtime-py",
            deployment_name="local",
            agents=[AgentBindSlice(agent_id="moderator", pattern_plugin_id="react@v1")],
        ),
        plan=PlacementPlan(),
    )
    materialized = MaterializedMas(
        compose=compose,
        materialized=SimpleNamespace(instances={"moderator": instance}),
        mas_base_dir=tmp_path,
    )
    entry_manifest = {"metadata": {"name": "moderator"}, "spec": {}}
    with patch("mas.ctl.executor.mas_session.enrich_entry_agent_for_delegation", return_value=entry_manifest):
        with patch("mas.ctl.executor.mas_session.wire_entry_engine_delegation"):
            prepared_no_session = prepare_delegation_entry_session(
                materialized,
                entry_id="moderator",
                entry_manifest=entry_manifest,
            )
            prepared_with_session = prepare_delegation_entry_session(
                materialized,
                entry_id="moderator",
                entry_manifest=entry_manifest,
                session_id="given-session-id",
            )

    assert prepared_no_session.session_id  # minted, non-empty
    assert prepared_with_session.session_id == "given-session-id"


def test_a_request_from_another_agent_to_the_entry_is_not_shown_as_the_user():
    """A request that reaches the entry over a chain carries the caller's call id. It runs under the
    agent's label: shown with the user's display it read "You: <request>" next to the input prompt."""
    import io

    from mas.ctl.session.controller import SessionController as RealController
    from mas.ctl.ui.stdout import StdoutConversationDisplay

    displays: list[object] = []
    orig_init = RealController.__init__

    def _init(self, *args, **kwargs):
        displays.append(kwargs.get("display"))
        return orig_init(self, *args, **kwargs)

    class _Result:
        text = "ok"
        awaiting_hitl = False

    user_display = StdoutConversationDisplay(out=io.StringIO(), agent_label="billing")
    with patch.object(RealController, "__init__", _init):
        with patch("mas.ctl.executor.mas_session.SessionController.run_turn", lambda self, *a, **k: _Result()):
            with patch("mas.ctl.executor.mas_session.turn_failed", return_value=False):
                peer = make_workflow_send(
                    _fake_materialized_for_send(["billing"]), display=user_display, verbose=0, from_agent="billing"
                )
                peer("billing", "refund?", 0, "triage-call-1")
                own = make_workflow_send(
                    _fake_materialized_for_send(["billing"]), display=user_display, verbose=0, from_agent="billing"
                )
                own("billing", "step one")
    assert displays[0] is not user_display and displays[0]._user_prompt_echoed and displays[0]._agent_label == "billing"
    assert displays[1] is user_display  # a sequential step of the entry itself keeps the session display


def test_an_unknown_peer_on_a_non_entry_node_is_reported_when_the_session_is_wired(tmp_path: Path):
    from mas.runtime.boundary.agentcomm import UnknownDelegationPeerError

    materialized = _materialized_with_agents(["mod", "a"], tmp_path)
    materialized.compose.mas_config["spec"] = {
        "workflow": {
            "entry": "mod",
            "nodes": [{"id": "mod", "delegates_to": ["a"]}, {"id": "a", "delegates_to": ["ghost"]}],
        }
    }

    def _fake_manifest(bind, agent_id):
        return {"metadata": {"name": agent_id}, "spec": {}}

    with patch("mas.ctl.executor.mas_session.load_agent_manifest_from_bind", side_effect=_fake_manifest):
        with patch("mas.ctl.executor.mas_session.wire_entry_engine_delegation"):
            with pytest.raises(UnknownDelegationPeerError, match="ghost"):
                wire_peer_delegation(materialized, entry_id="mod", already_wired=set())
