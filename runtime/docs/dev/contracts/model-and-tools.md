<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Model and tool contracts

These contracts cover tool exposure, memory I/O and model execution.
This page is for contributors who write tools, memory backends or model providers.

```python
from pydantic import BaseModel, Field
from mas.runtime.contracts import ToolContract


class CheckHealthTool(ToolContract):
    class Input(BaseModel):
        service_name: str = Field(..., description="Service name or URL")

    def get_name(self) -> str:
        return "check-service-health"

    def get_description(self) -> str:
        return "Check whether a service is healthy."

    def execute(self, **kwargs):
        args = self.Input(**kwargs)
        return {"healthy": True, "service": args.service_name}
```

## ToolContract

`ToolContract` is the primary external action boundary. It exposes tools to an LLM or planner, validates and
normalizes arguments, and executes a tool behind governance hooks.

| Method | Parameters | Notes |
| --- | --- | --- |
| `get_name()` | none | Single-tool helper API |
| `get_description()` | none | Human-readable description |
| `get_parameters_schema()` | none | JSON Schema or schema derived from `Input` |
| `execute(**kwargs)` | validated tool args | Single-tool execution path |
| `list_tools()` | none | Returns LLM-visible tool descriptors |
| `call_tool(tool_name, arguments)` | tool name, dict arguments | Composite or single-tool dispatch |

Runtime call path:

```text
collect_tools
  -> ToolContract.list_tools()

execute_tool
  -> BudgetTracker.allow_tool()
  -> ToolContract.call_tool()
  -> RecorderContract.emit()
```

## MemoryContract

`MemoryContract` handles memory I/O behind hooks, with provenance and versioning. It is separate from session
state and shared coordination state. Supported memory types: `episodic`, `semantic`, `procedural`.

| Method | Parameters | Notes |
| --- | --- | --- |
| `read_memory(memory_type, query)` | memory type, query dict | Query memory backend |
| `write_memory(memory_type, payload)` | memory type, payload dict | Persist memory item |

Implement `MemoryContract` for a backend. There is no shipped dummy store class.

## How models are reached

The design pattern schedules model calls as `LLM_CALL` egress symbols, and the engine executes them
(`EngineContract.invoke`, `mas.runtime.engine`). Providers are `llm_provider` plugins resolved by kind (default `openai`).
The `llm_cache` infra middleware wraps the provider. The agent manifest's
`spec.models` sets the model slot an agent uses. Governance and observability apply at the envelope, not inside a
provider. See [docs/manifests/llm-cache.md](../../../../docs/manifests/llm-cache.md) and
[docs/references/runtime.md](../../../../docs/references/runtime.md).
