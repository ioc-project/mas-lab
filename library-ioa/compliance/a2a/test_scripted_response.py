from __future__ import annotations

import sys
from pathlib import Path

LIBRARY_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(LIBRARY_ROOT))

from mas.runtime.kernel.config import KernelConfig  # noqa: E402
from mas.runtime.kernel.state import QProduct, RunLedger  # noqa: E402
from mas.runtime.registry import get_registry  # noqa: E402
from mas.runtime.registry.bootstrap import register_manifest_file  # noqa: E402
from mas.runtime.schema.ingress import UserInputReceived  # noqa: E402


def _run(params: dict, *, turn_id: str, prompt: str = "test"):
    registry = get_registry()
    if registry.resolve_by_type("design_pattern", "scripted_response") is None:
        register_manifest_file(registry, LIBRARY_ROOT / "a2a_compliance_lib" / "library.yaml")
    plugin_info = registry.resolve_by_type("design_pattern", "scripted_response")
    assert plugin_info is not None
    plugin = plugin_info.load_class()()
    config = KernelConfig(
        agent_spec={
            "design_pattern": {
                "type": "scripted_response",
                "params": params,
            }
        }
    )
    return plugin.handle_event(
        QProduct(),
        RunLedger(),
        UserInputReceived(user_turn_id=turn_id, text=prompt),
        config=config,
    )[0]


def test_matches_first_specific_rule_and_preserves_structured_response() -> None:
    response = _run(
        {
            "rules": [
                {
                    "turn_id_prefix": "artifact-file-url",
                    "response": {
                        "text": "done",
                        "artifacts": [{"kind": "url", "url": "https://example.test"}],
                    },
                },
                {
                    "turn_id_prefix": "artifact-file",
                    "response": {"text": "wrong"},
                },
            ]
        },
        turn_id="artifact-file-url-123",
    )

    assert response.content == "done"
    assert response.artifacts == ({"kind": "url", "url": "https://example.test"},)


def test_prompt_constraint_and_default_are_deterministic() -> None:
    params = {
        "rules": [
            {
                "turn_id_prefix": "history",
                "prompt": "second",
                "response": {"text": "matched", "task_state": "input_required"},
            }
        ],
        "default": {"text": "fallback"},
    }

    assert _run(params, turn_id="history-1", prompt="first").content == "fallback"
    matched = _run(params, turn_id="history-1", prompt="second")
    assert matched.content == "matched"
    assert matched.task_state == "input_required"
