#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Tutorial 14 — Governance and HITL: manifest validation, hitl_mode
resolution, and a governed tool call end to end (auto-approve vs auto-deny).
"""

from __future__ import annotations

import pytest
from conftest import T14, live_cli_env, live_tutorial_chat, load_yaml, run_cli

OVERLAYS = [
    "tools.yaml",
    "governance-interactive.yaml",
    "governance-auto-approve.yaml",
    "governance-auto-deny.yaml",
    "agent-asks-user.yaml",
]


class TestManifestValidation:
    def test_validate_base_agent(self):
        r = run_cli(["mas-ctl", "validate", str(T14 / "agent.yaml")])
        assert r.returncode == 0, r.stderr
        assert "OK" in r.stdout

    @pytest.mark.parametrize("overlay", OVERLAYS)
    def test_validate_with_overlay(self, overlay):
        r = run_cli(
            [
                "mas-ctl",
                "validate",
                str(T14 / "agent.yaml"),
                "--overlay",
                str(T14 / "overlays" / overlay),
            ]
        )
        assert r.returncode == 0, r.stderr

    def test_validate_tools_plus_auto_approve(self):
        r = run_cli(
            [
                "mas-ctl",
                "validate",
                str(T14 / "agent.yaml"),
                "--overlay",
                str(T14 / "overlays" / "tools.yaml"),
                "--overlay",
                str(T14 / "overlays" / "governance-auto-approve.yaml"),
            ]
        )
        assert r.returncode == 0, r.stderr


class TestManifestStructure:
    @pytest.mark.parametrize(
        "overlay,expected_mode",
        [
            ("governance-interactive.yaml", "interactive"),
            ("governance-auto-approve.yaml", "auto-approve"),
            ("governance-auto-deny.yaml", "auto-deny"),
        ],
    )
    def test_overlay_declares_hitl_on_tool_and_mode(self, overlay, expected_mode):
        ov = load_yaml(T14 / "overlays" / overlay)
        entry = ov["spec"]["patch"]["governance"]["$op"]["add"][0]["sample_governance"]
        assert entry["hitl_on_tool"] is True
        assert entry["hitl_on_tool_result"] is False
        assert entry["hitl_mode"] == expected_mode


def _merged_agent(*overlay_names: str) -> dict:
    from mas.ctl.overlay import merge_overlay

    merged = load_yaml(T14 / "agent.yaml")
    for name in overlay_names:
        merged = merge_overlay(merged, load_yaml(T14 / "overlays" / name))
    return merged


class TestHitlModeResolution:
    """spec.governance[].sample_governance.hitl_mode picks the in-process responder."""

    def test_auto_approve_resolves_to_auto_approve_responder(self):
        from mas.ctl.session.hitl_config import resolve_hitl_from_manifest
        from mas.library.standard.plugins.hitl.responders import AutoApproveResponder

        manifest = _merged_agent("tools.yaml", "governance-auto-approve.yaml")
        responder, terminal = resolve_hitl_from_manifest(manifest, session_interactive=False)
        assert isinstance(responder, AutoApproveResponder)
        assert terminal is None

    def test_auto_deny_resolves_to_auto_deny_responder(self):
        from mas.ctl.session.hitl_config import resolve_hitl_from_manifest
        from mas.library.standard.plugins.hitl.responders import AutoDenyResponder

        manifest = _merged_agent("tools.yaml", "governance-auto-deny.yaml")
        responder, terminal = resolve_hitl_from_manifest(manifest, session_interactive=False)
        assert isinstance(responder, AutoDenyResponder)
        assert terminal is None

    def test_interactive_mode_falls_back_to_auto_approve_when_not_a_terminal(self):
        """hitl_config.py: interactive + non-interactive session => auto-approve, not a hang."""
        from mas.ctl.session.hitl_config import resolve_hitl_from_manifest
        from mas.library.standard.plugins.hitl.responders import AutoApproveResponder

        manifest = _merged_agent("tools.yaml", "governance-interactive.yaml")
        responder, _terminal = resolve_hitl_from_manifest(manifest, session_interactive=False)
        assert isinstance(responder, AutoApproveResponder)


def _calc_engine(*, expression: str = "125 * 3 + 42", answer: str = "The total is $417."):
    from mas.runtime.engine.simulated import SimulatedEngine

    def llm_next_step(cid: int) -> str:
        return "TOOL_CALL" if cid == 1 else "STOP"

    def llm_tool_intent(cid: int) -> tuple[str, dict]:
        if cid == 1:
            return "calc", {"expression": expression}
        return "", {}

    return SimulatedEngine(
        llm_next_step=llm_next_step,
        llm_tool_intent=llm_tool_intent,
        stop_text=answer,
    )


def _context_text(instance) -> str:
    parts: list[str] = []
    for e in instance.driver.kernel.run.events:
        if getattr(e, "text", None):
            parts.append(e.text)
    for m in getattr(instance.driver.ctx, "committed_messages", None) or []:
        if isinstance(m, dict):
            parts.append(str(m.get("content") or ""))
    return "\n".join(parts)


def _hitl_resolutions(instance) -> list[str]:
    """How the wired HITL responder actually resolved each request.

    The GOVERNANCE_DECISION event for a gated tool call always records
    ``decision: HITL`` (the plugin's own verdict, before resolution); the
    responder's ALLOW/BLOCK/SKIP answer shows up as a separate HITL_RESOLVE
    event instead.
    """
    from mas.runtime.schema.observability import ObsEventKind

    sink = instance.driver.observability
    return [
        e.payload.get("resolution")
        for e in (sink.events if sink else [])
        if e.kind == ObsEventKind.HITL_RESOLVE
    ]


class TestGovernedToolCallEndToEnd:
    """docs/tutorials/14-governance-hitl/README.md § Step 5 — the actual behavioral claim."""

    def _run(self, *, overlay: str):
        from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime
        from mas.ctl.session.controller import ConversationConfig, SessionController, close_observability
        from mas.ctl.session.hitl_config import resolve_hitl_from_manifest
        from mas.ctl.ui.stdout import StdoutConversationDisplay

        manifest = _merged_agent("tools.yaml", overlay)
        responder, _terminal = resolve_hitl_from_manifest(manifest, session_interactive=False)
        instance, _store = instantiate_runtime(
            InstantiationOptions(
                agent_manifest=manifest,
                manifest_dir=T14,
                validate_manifests=False,
                engine=_calc_engine(),
            ),
            hitl=responder,
        )
        controller = SessionController(
            instance=instance,
            display=StdoutConversationDisplay(show_labels=False, verbose=0),
            config=ConversationConfig(single_turn=True),
        )
        result = controller.run_turn("Calculate 125 * 3 + 42.")
        close_observability(controller)
        return instance, result

    def test_auto_approve_lets_the_calculator_run(self):
        instance, result = self._run(overlay="governance-auto-approve.yaml")
        assert _hitl_resolutions(instance) == ["ALLOW"]
        # The engine's (simulated) tool result made it into context — the
        # protected action was not skipped.
        assert "[simulated tool result" in _context_text(instance)
        assert result.text

    def test_auto_deny_blocks_the_calculator(self):
        instance, result = self._run(overlay="governance-auto-deny.yaml")
        blob = _context_text(instance)
        assert _hitl_resolutions(instance) == ["BLOCK"]
        # No simulated tool result reached context — the call never ran.
        assert "[simulated tool result" not in blob
        assert "Operator blocked tool 'calc' via HITL." in blob
        # BLOCK ends the turn: the engine's follow-up stop_text must not leak.
        assert "Operator blocked tool 'calc' via HITL." in result.text
        assert "417" not in result.text
        resolve_events = [
            e
            for e in (instance.driver.observability.events if instance.driver.observability else [])
            if e.kind.value == "hitl.resolve"
        ]
        assert resolve_events
        assert resolve_events[0].payload.get("operator_id") == "auto-denier"


# ═══════════════════════════════════════════════════════════════════════════
# Live mas-ctl chat (real LLM)
# ═══════════════════════════════════════════════════════════════════════════

_LIVE = live_tutorial_chat
_LIVE_QUERY = "Calculate 125 * 3 + 42."


@_LIVE
class TestLiveGovernedChat:
    def test_auto_approve_runs_the_tool(self):
        r = run_cli(
            [
                "mas-ctl",
                "chat",
                "agent.yaml",
                "-o",
                "overlays/tools.yaml",
                "-o",
                "overlays/governance-auto-approve.yaml",
                "--no-cache-read",
                "--model",
                "gpt-4o",
                "-q",
                _LIVE_QUERY,
            ],
            cwd=T14,
            timeout=120,
            extra_env=live_cli_env(),
        )
        assert r.returncode == 0, r.stderr
        assert "417" in r.stdout

    def test_auto_deny_blocks_the_tool(self):
        r = run_cli(
            [
                "mas-ctl",
                "chat",
                "agent.yaml",
                "-o",
                "overlays/tools.yaml",
                "-o",
                "overlays/governance-auto-deny.yaml",
                "--no-cache-read",
                "--model",
                "gpt-4o",
                "-q",
                _LIVE_QUERY,
            ],
            cwd=T14,
            timeout=120,
            extra_env=live_cli_env(),
        )
        assert r.returncode == 0, r.stderr
        assert "417" not in r.stdout
        assert "blocked" in r.stdout.lower()
