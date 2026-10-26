#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""HITL-on-tool regression — scripted terminal + events.jsonl hitl_gate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from ci_llm import CI_REPLAY_YAML, isolated_mas_env, require_ci_cache, web_search_engine
from mas.runtime.schema.hitl import HitlResolveChoice
from mas.runtime.schema.observability import ObsEventKind

REPO_ROOT = Path(__file__).resolve().parents[1]
T01 = REPO_ROOT / "docs" / "tutorials" / "01-building-an-agent"
MAS_CTL = Path(sys.executable).parent / "mas-ctl"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _merged_tutorial_agent() -> dict:
    from mas.ctl.overlay import merge_overlay

    base = _load_yaml(T01 / "agent.yaml")
    for name in ("tools.yaml", "governance-hitl.yaml"):
        base = merge_overlay(base, _load_yaml(T01 / "overlays" / name))
    return base


def _event_kinds(events_path: Path) -> list[str]:
    kinds: list[str] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        kinds.append(json.loads(line).get("kind", ""))
    return kinds


def _boundary_hitl_requests(instance) -> list:
    sink = instance.driver.observability
    events = getattr(sink, "events", None)
    if events is None and hasattr(sink, "operator"):
        events = sink.operator.events
    return [e for e in (events or []) if e.kind == ObsEventKind.HITL_REQUEST]


@pytest.mark.timeout(60)
def test_scripted_hitl_terminal_allow_runs_tool(tmp_path: Path) -> None:
    """ScriptedHitlTerminal (ALLOW) — HITL fires and tool runs after approval."""
    from mas.ctl.adapters.hitl_terminal import ScriptedHitlTerminal
    from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime
    from mas.ctl.session.controller import ConversationConfig, SessionController, close_observability
    from mas.ctl.ui.stdout import StdoutConversationDisplay

    manifest = _merged_tutorial_agent()
    instance, _store = instantiate_runtime(
        InstantiationOptions(
            agent_manifest=manifest,
            manifest_dir=T01,
            validate_manifests=False,
            engine=web_search_engine(),
        ),
        hitl=None,
    )
    controller = SessionController(
        instance=instance,
        display=StdoutConversationDisplay(show_labels=False, verbose=0),
        hitl_terminal=ScriptedHitlTerminal(default=HitlResolveChoice.ALLOW),
        config=ConversationConfig(single_turn=True),
    )
    result = controller.run_turn("Who is POTUS")
    close_observability(controller)

    assert _boundary_hitl_requests(instance), "expected boundary HITL_REQUEST events"
    assert result.text, "expected agent response after ALLOW"


@pytest.mark.timeout(60)
def test_scripted_hitl_block_skips_tool(tmp_path: Path) -> None:
    """BLOCK resolution — HITL fires; tool must not run."""
    from mas.ctl.adapters.hitl_terminal import ScriptedHitlTerminal
    from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime
    from mas.ctl.session.controller import ConversationConfig, SessionController, close_observability
    from mas.ctl.ui.stdout import StdoutConversationDisplay

    manifest = _merged_tutorial_agent()
    instance, _store = instantiate_runtime(
        InstantiationOptions(
            agent_manifest=manifest,
            manifest_dir=T01,
            validate_manifests=False,
            engine=web_search_engine(),
        ),
        hitl=None,
    )
    controller = SessionController(
        instance=instance,
        display=StdoutConversationDisplay(show_labels=False, verbose=0),
        hitl_terminal=ScriptedHitlTerminal(default=HitlResolveChoice.BLOCK),
        config=ConversationConfig(single_turn=True),
    )
    result = controller.run_turn("Who is POTUS")
    close_observability(controller)

    assert _boundary_hitl_requests(instance)
    assert "[calculator]" not in result.text.lower()
    assert "blocked" in result.text.lower()


@pytest.mark.timeout(60)
def test_cli_batch_hitl_emits_hitl_gate(tmp_path: Path) -> None:
    """mas-ctl chat -q path (verify-chat-smoke) still records hitl_gate in events."""
    if not MAS_CTL.is_file():
        pytest.skip("mas-ctl CLI not in venv")
    require_ci_cache()
    events_file = tmp_path / "events.jsonl"
    proc = subprocess.run(
        [
            str(MAS_CTL),
            "chat",
            "agent.yaml",
            "-q",
            "Who is POTUS",
            "--infra-ref",
            "standard:openai",
            "--infra-ref",
            str(CI_REPLAY_YAML),
            "-o",
            "overlays/tools.yaml",
            "-o",
            "overlays/governance-hitl.yaml",
            "--events",
            "--events-file",
            str(events_file),
        ],
        cwd=str(T01),
        env=isolated_mas_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=55,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert events_file.is_file()
    assert "hitl_gate" in _event_kinds(events_file)
    # Workspace config.yaml sets mas_ctl.trace: summary — no --trace flag.
    err = proc.stderr
    assert "USER -> AGENT" in err
    assert "-> LLM[" in err
    assert "LLM[" in err and "-> AGENT" in err
    assert "-> TOOL[web-search]" in err
    assert "TOOL[web-search] -> AGENT" in err
    assert "LLM[gpt-4o" in err
    assert "-> USER" in err
    assert "[system]" not in err
    user_out = [line for line in err.splitlines() if "-> USER" in line]
    assert user_out
    assert "..." not in user_out[-1]
