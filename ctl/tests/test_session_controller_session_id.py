#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""SessionController.session_id — minted once, reused across every turn."""

from __future__ import annotations

from mas.ctl.session.controller import ConversationConfig, SessionController
from mas.runtime.boundary.gov.transition import GovTransition
from mas.runtime.driver.instance import RuntimeInstance
from mas.runtime.engine.simulated import SimulatedEngine
from mas.runtime.kernel.config import KernelConfig
from mas.runtime.kernel.coupling import GovDecision
from mas.runtime.schema.ingress import EngineIoReturn


class _SilentDisplay:
    def on_user(self, text: str, *, turn_id: str = "") -> None:
        return

    def on_agent(self, text: str) -> None:
        return

    def on_turn_error(self, message: str, *, detail: str = "") -> None:
        return

    def on_hitl_request(self, request: object) -> None:
        return

    def on_system(self, message: str) -> None:
        return

    def on_error(self, message: str) -> None:
        return


class _RecordingGovPlugin:
    def __init__(self) -> None:
        self.seen: list[GovTransition] = []

    def on_transition(self, transition: GovTransition) -> None:
        self.seen.append(transition)

    def evaluate_egress(self, intent, *, config):
        return GovDecision.ALLOW, "test", ""


def _engine() -> SimulatedEngine:
    return SimulatedEngine(
        script={
            1: EngineIoReturn(correlation_id=1, response_kind="MODEL_TEXT", next_step="STOP", text="ok"),
            2: EngineIoReturn(correlation_id=2, response_kind="MODEL_TEXT", next_step="STOP", text="ok2"),
        }
    )


def test_session_id_defaults_to_a_fresh_uuid_when_not_given() -> None:
    plugin = _RecordingGovPlugin()
    instance = RuntimeInstance.from_parts(engine=_engine(), config=KernelConfig(egress_governance_plugin=plugin))
    instance.capture_session_baseline()
    controller = SessionController(
        instance=instance, display=_SilentDisplay(), config=ConversationConfig(single_turn=False)
    )
    assert controller.session_id
    # Well-formed uuid4, not e.g. an empty string or a placeholder.
    import uuid

    uuid.UUID(controller.session_id)


def test_explicit_session_id_is_kept_verbatim() -> None:
    instance = RuntimeInstance.from_parts(engine=_engine())
    instance.capture_session_baseline()
    controller = SessionController(
        instance=instance,
        display=_SilentDisplay(),
        config=ConversationConfig(single_turn=False),
        session_id="fixed-session-id",
    )
    assert controller.session_id == "fixed-session-id"


def test_session_id_is_shared_across_turns_task_id_is_not() -> None:
    """The whole point: a follow-up turn on the same controller continues the
    SAME session (governance/observability see session_id unchanged) but
    gets its own fresh task_id."""
    plugin = _RecordingGovPlugin()
    instance = RuntimeInstance.from_parts(engine=_engine(), config=KernelConfig(egress_governance_plugin=plugin))
    instance.capture_session_baseline()
    controller = SessionController(
        instance=instance,
        display=_SilentDisplay(),
        config=ConversationConfig(single_turn=False),
        session_id="fixed-session-id",
    )

    controller.run_turn("first", auto_hitl=False)
    first_task_ids = {t.task_id for t in plugin.seen}
    plugin.seen.clear()

    controller.run_turn("second", auto_hitl=False)
    second_task_ids = {t.task_id for t in plugin.seen}

    assert all(t.session_id == "fixed-session-id" for t in plugin.seen)
    assert first_task_ids and second_task_ids
    assert first_task_ids != second_task_ids
