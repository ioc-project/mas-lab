#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Session /reset — restore baseline context and clear working memory."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from ci_llm import stop_engine

REPO_ROOT = Path(__file__).resolve().parents[1]
T01 = REPO_ROOT / "docs" / "tutorials" / "01-building-an-agent"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _merged_tutorial_agent() -> dict:
    from mas.ctl.overlay import merge_overlay

    base = _load_yaml(T01 / "agent.yaml")
    for name in ("tools.yaml",):
        base = merge_overlay(base, _load_yaml(T01 / "overlays" / name))
    return base


@pytest.mark.timeout(60)
def test_reset_clears_working_memory_and_turn_history() -> None:
    from mas.ctl.adapters.hitl_terminal import ScriptedHitlTerminal
    from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime
    from mas.ctl.session.controller import ConversationConfig, SessionController, close_observability
    from mas.runtime.schema.hitl import HitlResolveChoice

    manifest = _merged_tutorial_agent()
    instance, _store = instantiate_runtime(
        InstantiationOptions(
            agent_manifest=manifest,
            manifest_dir=T01,
            validate_manifests=False,
            engine=stop_engine(text="ok"),
        ),
        hitl=None,
    )
    ctx = instance.driver.ctx
    baseline_len = len(ctx.injected_context)

    controller = SessionController(
        instance=instance,
        display=type(
            "D",
            (),
            {
                "on_system": lambda *_a, **_k: None,
                "on_user": lambda *_a, **_k: None,
                "on_agent": lambda *_a, **_k: None,
            },
        )(),
        hitl_terminal=ScriptedHitlTerminal(default=HitlResolveChoice.ALLOW),
        config=ConversationConfig(single_turn=False),
    )
    controller.run_turn("Who is POTUS?")
    assert ctx.turn_history or ctx.working_memory.messages or ctx.committed_messages

    ctx.injected_context.append("[operator steer] temporary injection")
    assert controller.reset_session() is True

    assert not ctx.turn_history
    assert not ctx.committed_messages
    assert not ctx.working_memory.messages
    assert ctx.last_user_text == ""
    assert len(ctx.injected_context) == baseline_len
    assert "[operator steer]" not in "\n".join(ctx.injected_context)
    close_observability(controller)
