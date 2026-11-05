#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""run_agent_turn must thread its session_id into the SessionController it
constructs, not just report it back in AgentTurnResult.

Regression: run_agent_turn minted/accepted `sid`, used it as turn_id, and
returned it as AgentTurnResult.session_id — giving every appearance of a
real session id — but never actually passed session_id=sid into
SessionController(...), so the governance/observability session id was
silently a different, unrelated value every time.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from mas.lab.controller.agent_chat import run_agent_turn

_MANIFEST_YAML = "apiVersion: mas/v1\nkind: Agent\nmetadata:\n  name: probe\nspec: {}\n"


def _run_and_capture_real_session_id(tmp_path: Path, *, session_id):
    """Fake ONLY SessionController.run_turn — construction runs for real
    (against a mocked `instance`, since __post_init__ never touches it), so
    `self.session_id` inside the fake reflects the REAL dataclass field.

    Deliberately not faking the whole SessionController class: a
    `class _FakeSessionController: def __init__(self, **kwargs): ...`
    accepts any keyword whatsoever, so it would keep "passing" even if
    run_agent_turn's call site and the real class's field name drifted
    apart — exactly the failure mode this regression test exists to catch.
    """
    captured: dict = {}

    def _fake_run_turn(self, prompt, *, turn_id=None, auto_hitl=True):
        captured["session_id"] = self.session_id
        return MagicMock()

    with patch("mas.ctl.infra.resolve.resolve_infra_refs", return_value=MagicMock()):
        with patch(
            "mas.ctl.session.bootstrap.instantiate_runtime",
            return_value=(MagicMock(), MagicMock()),
        ):
            with patch(
                "mas.ctl.session.hitl_config.resolve_hitl_from_manifest",
                return_value=(None, None),
            ):
                with patch("mas.ctl.session.controller.SessionController.run_turn", _fake_run_turn):
                    with patch(
                        "mas.ctl.ui.turn_result.turn_to_agent_result",
                        return_value=MagicMock(status="ok", response="ok", error_message="", error_detail=""),
                    ):
                        run_agent_turn(
                            _MANIFEST_YAML,
                            "hello",
                            base_dir=tmp_path,
                            session_id=session_id,
                        )
    return captured


def test_explicit_session_id_is_passed_into_session_controller(tmp_path: Path) -> None:
    captured = _run_and_capture_real_session_id(tmp_path, session_id="fixed-session-id")
    assert captured.get("session_id") == "fixed-session-id"


def test_minted_session_id_is_also_passed_into_session_controller(tmp_path: Path) -> None:
    """No session_id given -> run_agent_turn mints its own "ui:<uuid>" — that
    SAME minted value (not some other default) must reach SessionController,
    since it's also what's reported back in AgentTurnResult.session_id."""
    captured = _run_and_capture_real_session_id(tmp_path, session_id=None)
    sid = captured.get("session_id")
    assert sid is not None and sid.startswith("ui:")
