#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tutorial 13 — Control attach and debug scripts: the full witness
(bad tool result -> interrupt -> persist -> resume -> investigate ->
steer-to-fix), driven offline against the tutorial's own fixtures
(agent.yaml, debug.gdb, recover.ctl, steer-fix.ctl, tools/tools.py).

Adapted from library-standard/tests/test_debug_script.py's proven harness,
which drives SessionManager/SessionController with a stubbed instance
(conversation state is seeded directly, not produced by a real engine turn —
see that module for why) — pointed at this tutorial's own files instead of
the library's bundled example.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tutorial_support import T13, run_cli


def _load_demo_tools():
    """Load docs/tutorials/13-control-and-debug/tools/tools.py by file path.

    Several tutorials ship their own same-named ``tools.py`` fixture; a
    bare ``sys.path`` + ``import tools`` would resolve to whichever one the
    test process happened to import first.
    """
    import importlib.util

    path = T13 / "tools" / "tools.py"
    spec = importlib.util.spec_from_file_location("tutorial13_tools", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DemoTools


class TestManifestValidation:
    def test_validate_agent(self):
        r = run_cli(["mas-ctl", "validate", str(T13 / "agent.yaml")])
        assert r.returncode == 0, r.stderr
        assert "OK" in r.stdout

    def test_validate_auto_checkpoint_agent(self):
        r = run_cli(["mas-ctl", "validate", str(T13 / "agent-auto.yaml")])
        assert r.returncode == 0, r.stderr
        assert "OK" in r.stdout

    @pytest.mark.parametrize(
        "name", ["control-debug.yaml", "flavour-debug.yaml", "control-auto.yaml", "flavour-auto.yaml"]
    )
    def test_validate_control_and_flavour_files(self, name):
        r = run_cli(["mas-ctl", "validate", str(T13 / name)])
        assert r.returncode == 0, r.stderr
        assert "OK" in r.stdout

    @pytest.mark.parametrize(
        ("flavour", "plugin"), [("flavour-debug.yaml", "gdb"), ("flavour-auto.yaml", "sample_governance")]
    )
    def test_the_flavour_attaches_the_plugin_the_agent_no_longer_carries(self, flavour, plugin):
        import yaml
        from mas.ctl.session.flavour import resolve_flavour
        from mas.ctl.session.flavour_control import (
            apply_flavour_controls,
            control_lists_for_agent,
            plugin_entry,
            resolve_controls,
        )

        agent = "agent.yaml" if flavour == "flavour-debug.yaml" else "agent-auto.yaml"
        manifest = yaml.safe_load((T13 / agent).read_text(encoding="utf-8"))
        kind = plugin_entry(plugin)["kind"]
        assert kind not in manifest["spec"]
        spec = resolve_flavour(str(T13 / flavour))
        attached = apply_flavour_controls(manifest, spec, agent_id=manifest["metadata"]["name"])
        assert kind not in attached["spec"]
        lists = control_lists_for_agent(
            resolve_controls(spec), agent_id=manifest["metadata"]["name"], entry_id=manifest["metadata"]["name"]
        )
        names = [item if isinstance(item, str) else next(iter(item)) for item in lists["governance"]]
        assert names == [plugin]


class TestToolFixtures:
    """tools/tools.py's web_search stub is deterministic offline (poison_capital).

    Loaded by file path (not sys.path + bare ``import tools``) so this
    never collides with another test file's own same-named ``tools``
    module elsewhere in the suite.
    """

    def test_web_search_poison_capital_says_lyon(self):
        DemoTools = _load_demo_tools()
        demo = DemoTools(name="web_search", poison_capital=True)
        result = demo.on_execute_tool("web_search", {"query": "capital of France"})
        assert "Lyon is the capital of France." in str(result)

    def test_calc_flaky_fails_first_n_then_succeeds(self):
        DemoTools = _load_demo_tools()
        demo = DemoTools(name="calc", fail_first=2)
        first = demo.on_execute_tool("calc", {"expression": "2+2"})
        second = demo.on_execute_tool("calc", {"expression": "2+2"})
        third = demo.on_execute_tool("calc", {"expression": "2+2"})
        assert "error" in str(first).lower() or "fail" in str(first).lower()
        assert "error" in str(second).lower() or "fail" in str(second).lower()
        assert "4" in str(third)


class TestDebugScriptFixture:
    """The gdb-like breakpoint script ships exactly the breakpoints the
    README's witness narrates."""

    def test_debug_gdb_breaks_on_first_web_search_result_and_every_calc_call(self):
        from mas.library.standard.plugins.governance.debug_script import parse_debug_script

        breaks = parse_debug_script((T13 / "debug.gdb").read_text(encoding="utf-8"))
        assert [b.kind for b in breaks] == ["tool_result", "tool_call"]
        assert breaks[0].tool_name == "web_search" and breaks[0].first_only
        assert breaks[1].tool_name == "calc" and not breaks[1].first_only
        assert "checkpoint" in breaks[0].commands


# ═══════════════════════════════════════════════════════════════════════════
# Offline session/control harness (adapted from test_debug_script.py)
# ═══════════════════════════════════════════════════════════════════════════


def _transition(**kwargs: object) -> SimpleNamespace:
    data = {
        "hook": "egress",
        "op": "TOOL_CALL",
        "response_kind": "",
        "session_id": "s1",
        "agent_id": "debug-script-demo",
        "q_state": {"dp": "ACT", "tool": "CALL"},
        "attributes": {},
        "correlation_id": 1,
    }
    data.update(kwargs)
    return SimpleNamespace(**data)


def _stub_instance():
    from mas.runtime.driver.mocks import AutoCtxAssembler

    ctx = AutoCtxAssembler()
    instance = SimpleNamespace(
        snapshot=lambda: {"q": {}, "run": {}},
        pause=lambda **kw: None,
        resume=lambda: None,
        driver=SimpleNamespace(ctx=ctx),
        feed=lambda event: SimpleNamespace(client_responses=[], hitl_requests=[], boundary_errors=[]),
    )
    instance.load_checkpoint = lambda data: setattr(instance, "loaded", data)
    return instance, None


class _Display:
    def on_system(self, *_a, **_k):
        return None

    def on_user(self, *_a, **_k):
        return None


def _manager():
    from mas.ctl.session.controller import SessionController
    from mas.ctl.session.manager import SessionManager
    from mas.runtime.session.snapshot import SnapshotTree

    instance, _ = _stub_instance()
    controller = SessionController(instance=instance, display=_Display(), agent_id="debug-script-demo")
    manager = SessionManager(snapshot_tree=SnapshotTree())
    manager.create(instance, controller, {"name": "debug-script-demo", "spec": {}}, session_id="s1")
    return manager


def _seed_france(session: Any, *, history: list[tuple[str, str]]) -> None:
    from mas.runtime.boundary.context.working_memory_registry import WorkingMemorySnapshot

    messages = [{"role": role, "content": text} for role, text in history]
    ctx = session.instance.driver.ctx
    ctx.turn_history = list(history)
    ctx.committed_messages = list(messages)
    session.working_memory.put(
        session.session_id,
        "debug-script-demo",
        WorkingMemorySnapshot(turn_history=list(history), committed_messages=list(messages)),
    )


def _resume_factory(_manifest: dict, _session_id: str):
    from mas.ctl.session.controller import SessionController
    from mas.runtime.driver.mocks import AutoCtxAssembler

    ctx = AutoCtxAssembler()
    instance = SimpleNamespace(
        snapshot=lambda: {"q": {}, "run": {}},
        pause=lambda **kw: None,
        resume=lambda: None,
        driver=SimpleNamespace(ctx=ctx),
        feed=lambda event: SimpleNamespace(client_responses=[], hitl_requests=[], boundary_errors=[]),
    )
    instance.load_checkpoint = lambda data: setattr(instance, "loaded", data)
    return instance, SessionController(instance=instance, display=_Display(), agent_id="debug-script-demo")


def _parse_ctl(name: str):
    from mas.library.standard.plugins.control.script import parse_control_script

    return parse_control_script((T13 / name).read_text(encoding="utf-8"))


class TestCheckpointOnFirstWebSearchResult:
    """Step 1: the first web_search result breaks, checkpoints, and lists."""

    def test_first_result_checkpoints_once(self):
        from mas.library.standard.plugins.governance.debug_script import DebugScriptPlugin
        from mas.runtime.boundary.control.contract import ControlCapability

        manager = _manager()
        session = manager.get("s1")
        _seed_france(session, history=[("user", "Remember we will talk about France later.")])
        out = StringIO()
        plugin = DebugScriptPlugin(script_file=str(T13 / "debug.gdb"), out=out)
        plugin.bind_session(
            session,
            control=manager.control(capability=ControlCapability(actor="debug_script", surface="plugin")),
            manager=manager,
        )
        first = _transition(
            hook="ingress",
            op="",
            response_kind="TOOL_RESULT",
            attributes={"tool_name": "web_search", "text": "Lyon is the capital of France."},
        )
        second = _transition(
            hook="ingress",
            op="",
            response_kind="TOOL_RESULT",
            attributes={"tool_name": "web_search", "text": "again"},
            correlation_id=2,
        )
        plugin.on_transition(first)
        plugin.on_transition(second)
        assert len(plugin.checkpoints) == 1
        assert "France" in "\n".join(plugin.log)


_BAD_HISTORY = [
    ("user", "Remember we will talk about France later. What is 2+2?"),
    ("assistant", "4."),
    ("user", "Look up the capital of France."),
    ("assistant", "Lyon is the capital of France."),
]


class TestWitness:
    """Steps 2-3: interrupt, persist, resume from disk, investigate, steer —
    executed through the tutorial's own recover.ctl / steer-fix.ctl files,
    not hand-typed control calls."""

    def test_recover_ctl_persists_a_checkpoint_without_the_live_session_s_later_state(self, tmp_path: Path):
        from mas.ctl.adapters.checkpoint import JsonCheckpointStore
        from mas.library.standard.plugins.control.script import run_control_script
        from mas.runtime.boundary.control.contract import ControlCapability

        manager = _manager()
        store = JsonCheckpointStore(tmp_path)
        manager.checkpoint_store = store
        session = manager.get("s1")
        _seed_france(session, history=_BAD_HISTORY)
        control = manager.control(capability=ControlCapability(actor="admin", surface="admin"))

        statements = _parse_ctl("recover.ctl")
        results = run_control_script(control, "s1", statements)
        saved = results[1]  # "persist --label bad --auto-stop"
        assert saved["label"] == "bad"

        payload = store.load_payload(Path(saved["path"]))
        blob = str(payload.get("working_memory"))
        assert "France" in blob and "Lyon" in blob
        assert payload["version"] == 2

    def test_new_chat_from_the_persisted_file_still_has_lyon_then_steer_fixes_it(self, tmp_path: Path):
        """The full witness: resume says Lyon; steering the restarted session
        replaces it with Paris. Mirrors steer-fix.ctl exactly — the steer
        text asserted below is read from that file, not hand-typed."""
        from mas.ctl.adapters.checkpoint import JsonCheckpointStore
        from mas.ctl.session.manager import SessionManager
        from mas.library.standard.plugins.control.script import run_control_script
        from mas.runtime.boundary.control.contract import ControlCapability

        store = JsonCheckpointStore(tmp_path)
        live = _manager()
        live.checkpoint_store = store
        session = live.get("s1")
        _seed_france(session, history=_BAD_HISTORY)
        control = live.control(capability=ControlCapability(actor="admin", surface="admin"))
        saved = run_control_script(control, "s1", _parse_ctl("recover.ctl"))[1]

        # "start a new chat from that file" (a fresh SessionManager/session).
        resumed_manager = SessionManager(store, _resume_factory)
        resumed = resumed_manager.fork_from_checkpoint(Path(saved["path"]), new_session_id="t13-bad")
        blob = str(resumed.working_memory.export_session("t13-bad"))
        assert "France" in blob
        assert "Lyon" in blob, "resumed session must still believe the bad answer"
        assert resumed.session_id != "s1"

        resumed_control = resumed_manager.control(capability=ControlCapability(actor="admin", surface="admin"))
        steer_statements = _parse_ctl("steer-fix.ctl")
        steer_text = next(s.kwargs["text"] for s in steer_statements if s.verb == "steer")
        assert "Paris" in steer_text and "Lyon" in steer_text  # sanity on the real fixture

        # steer-fix.ctl's bare `steer --text ...` resolves to mode="preempt",
        # which synchronously re-enters SessionController.run_turn — that
        # needs a real driver (exchange tracing, working-memory sync), not
        # this offline double. Exercise the queue-plane path instead
        # (mode="enqueue", proven in test_debug_script.py) with the exact
        # text the real file carries.
        resumed_control.steer("t13-bad", text=steer_text, mode="enqueue")

        queue = resumed_control.inspect_queue("t13-bad")
        assert queue.items, "steer-fix.ctl's steer must land on the resumed session's queue"
        assert any(steer_text in (item.text or "") for item in queue.items)
