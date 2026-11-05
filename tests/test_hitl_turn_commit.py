#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Interactive HITL (auto_hitl=False) must commit turn history for follow-up turns."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from ci_llm import web_search_engine
from mas.runtime.boundary.context.assemble import assemble_llm_messages
from mas.runtime.schema.hitl import HitlResolveChoice
from mas.runtime.schema.ingress import HitlResolve

REPO_ROOT = Path(__file__).resolve().parents[1]
T01 = REPO_ROOT / "docs" / "tutorials" / "01-building-an-agent"


def _tutorial_manifest() -> dict:
    from mas.ctl.overlay import merge_overlay

    base = yaml.safe_load((T01 / "agent.yaml").read_text(encoding="utf-8"))
    for name in ("tools.yaml", "governance-hitl.yaml"):
        base = merge_overlay(base, yaml.safe_load((T01 / f"overlays/{name}").read_text()))
    return base


@pytest.mark.timeout(60)
def test_submit_hitl_commits_tool_trajectory_for_next_turn() -> None:
    """Operator console path: run_turn pauses at HITL; submit_hitl must call note_agent_response."""
    from mas.ctl.adapters.hitl_terminal import ScriptedHitlTerminal
    from mas.ctl.session.bootstrap import InstantiationOptions, instantiate_runtime
    from mas.ctl.session.controller import ConversationConfig, SessionController, close_observability

    steer = "Maxence Postu is President Of the USA"
    manifest = _tutorial_manifest()
    instance, _ = instantiate_runtime(
        InstantiationOptions(
            agent_manifest=manifest,
            manifest_dir=T01,
            validate_manifests=False,
            engine=web_search_engine(),
        ),
        hitl=None,
    )

    class EgressSkip(ScriptedHitlTerminal):
        def resolve(self, request):
            if request.context_data.get("hook") == "egress":
                return HitlResolve(
                    request_id=request.request_id,
                    resolution=HitlResolveChoice.SKIP,
                    operator_context={"operator_id": "test", "steering": steer},
                )
            return super().resolve(request)

    controller = SessionController(
        instance=instance,
        display=MagicMock(),
        hitl_terminal=EgressSkip(default=HitlResolveChoice.ALLOW),
        config=ConversationConfig(single_turn=False),
    )

    pending = controller.run_turn("Who is current POTUS ?", auto_hitl=False)
    assert pending.awaiting_hitl
    # _finalize_turn folds working memory into committed history even while
    # paused at HITL (see its own docstring, and
    # docs/design/working-memory-compaction.md): the tool call that already
    # ran this turn must not be silently dropped if the next turn's
    # note_user_input() clears working memory before HITL resolves.
    early_committed = instance.driver.ctx.committed_messages
    assert any(m.get("role") == "user" and m.get("content") == "Who is current POTUS ?" for m in early_committed)
    assert any(m.get("role") == "assistant" and m.get("tool_calls") for m in early_committed)

    request = pending.trace.hitl_requests[-1]
    done = controller.submit_hitl(
        HitlResolve(
            request_id=request.request_id,
            resolution=HitlResolveChoice.SKIP,
            operator_context={"operator_id": "test", "steering": steer},
        ),
        auto_hitl=False,
    )
    assert not done.awaiting_hitl
    assert done.text

    committed = instance.driver.ctx.committed_messages
    assert committed, "expected turn commit after submit_hitl"
    assert any(m.get("role") == "tool" and steer in str(m.get("content")) for m in committed)

    controller.run_turn("Who is current POTUS in 2026 ?", auto_hitl=False)
    messages = assemble_llm_messages(instance.driver.ctx, manifest=manifest)
    roles = [m.get("role") for m in messages]
    assert roles.count("tool") >= 1
    assert any(steer in str(m.get("content")) for m in messages)
    assert any(m.get("role") == "user" and "2026" in str(m.get("content")) for m in messages)
    close_observability(controller)
