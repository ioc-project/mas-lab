<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Execution control and observability contracts

`ControlContract` is the pluggable execution control plane; `RecorderContract` is the structured event-recording
boundary. This page is for contributors who pause, steer or record agent runs.

```python
from mas.runtime.boundary.control.contract import ControlContract


def interrupt_for_review(control: ControlContract, session_id: str):
    control.pause(session_id, reason="Human approval required")
```

## ControlContract

Purpose: pause or resume a session, inject steering, snapshot and navigate
checkpoints, and send inbound user turns (`send_message` is A2A `message/send`).

| Method | Notes |
| --- | --- |
| `pause(session_id, reason=...)` | Temporary halt |
| `resume(session_id)` | Resume paused session |
| `send_message(session_id, text=...)` | Inbound user turn |
| `steer(session_id, text=..., mode=...)` | Mid-flight steering |
| `inspect(session_id)` / `inspect_context(session_id)` | Snapshot views |
| `snapshot` / `persist` / `list_checkpoints` / `navigate` | Checkpoint walk |
| `cancel_inflight(session_id)` | Drop the in-flight generation |

Import from `mas.runtime.boundary.control.contract`, not `mas.runtime.contracts`.

## RecorderContract

Purpose: capture hook and runtime events, support pluggable backends such as JSONL or OpenTelemetry, and keep
observability detached from business logic.

| Method | Parameters | Notes |
| --- | --- | --- |
| `emit(event)` | event dict | Core write operation |
| `flush()` | none | Flush buffered events |
| `close()` | none | Close resources |
| `query(*, kind, agent_id, call_id)` | all optional | Optional read-back; default empty |

Common event fields: `kind`, `timestamp`, `trace_id`, `span_id`, `parent_span_id`, `agent_id`, `hook_name`,
`plugin_name`, `error`, `metadata`.

```python
from mas.runtime.contracts import RecorderContract


def emit_tool_event(recorder: RecorderContract):
    recorder.emit(
        {
            "kind": "hook_start",
            "hook_name": "pre_tool_call",
            "plugin_name": "budget",
            "agent_id": "agent-123",
            "timestamp": 0.0,
        }
    )
```

## Interaction

```text
ControlContract.pause()
  -> runtime pauses at a safe boundary
  -> RecorderContract.emit() records the intervention

ControlContract.steer()
  -> runtime applies the directive before the next LLM step
  -> RecorderContract.emit() records the steering event
```
