#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Emulated infrastructure services that publish events to UI feed."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict


class InfrastructureEmulator:
    """Base class for emulated infrastructure services."""

    def __init__(self, service_id: str, feed_path: Path):
        self.service_id = service_id
        self.feed_path = feed_path
        self.enabled = True

    def emit_event(self, kind: str, payload: Dict[str, Any], target: str | None = None) -> None:
        """Emit an event to the UI feed."""
        if not self.enabled:
            return
        
        event = {
            "timestamp": time.time(),
            "kind": kind,
            "service": self.service_id,
            "payload": payload,
        }
        if target:
            event["target"] = target
        
        # Append to feed
        self.feed_path.parent.mkdir(parents=True, exist_ok=True)
        with self.feed_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    def on_agent_start(self, agent_id: str, run_id: str) -> None:
        """Called when an agent starts."""
        pass

    def on_agent_complete(self, agent_id: str, run_id: str) -> None:
        """Called when an agent completes."""
        pass

    def on_llm_call(self, agent_id: str, run_id: str, prompt: str) -> None:
        """Called when an LLM call is made."""
        pass


class ObserveEmulator(InfrastructureEmulator):
    """Emulated observability service."""

    def __init__(self, feed_path: Path):
        super().__init__("observe", feed_path)

    def on_agent_start(self, agent_id: str, run_id: str) -> None:
        """Log agent start event."""
        self.emit_event(
            "infra_observe",
            {"action": "agent_start", "agent_id": agent_id, "run_id": run_id},
            target=agent_id,
        )


class EvalEmulator(InfrastructureEmulator):
    """Emulated evaluation service."""

    def __init__(self, feed_path: Path):
        super().__init__("eval", feed_path)

    def on_agent_complete(self, agent_id: str, run_id: str) -> None:
        """Notify UI feed that a run finished (MCE scoring runs via pipeline eval-mce)."""
        self.emit_event(
            "infra_eval",
            {
                "action": "agent_complete",
                "agent_id": agent_id,
                "run_id": run_id,
                "status": "pending_eval",
            },
            target=agent_id,
        )


class ExplainEmulator(InfrastructureEmulator):
    """Emulated explanation service."""

    def __init__(self, feed_path: Path):
        super().__init__("explain", feed_path)

    def on_llm_call(self, agent_id: str, run_id: str, prompt: str) -> None:
        """Emit explainability hook for LLM calls (UI feed)."""
        preview = prompt[:200] + ("…" if len(prompt) > 200 else "")
        self.emit_event(
            "infra_explain",
            {
                "action": "llm_call",
                "agent_id": agent_id,
                "run_id": run_id,
                "prompt_preview": preview,
            },
            target=agent_id,
        )


def create_emulators(feed_path: Path) -> Dict[str, InfrastructureEmulator]:
    """Create all infrastructure emulators."""
    return {
        "observe": ObserveEmulator(feed_path),
        "eval": EvalEmulator(feed_path),
        "explain": ExplainEmulator(feed_path),
    }
