<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Tool Definition Reference

How a tool (a callable function an agent can invoke) is defined and registered.

A tool has two parts:

1. A **Python class** that implements `ToolContract` (the behaviour).
2. A **`kind: Tool` manifest** that advertises it (name, description, parameters) and points at the class through `spec.impl`. Agents reference the manifest through `spec.tools[].ref`.

Authoritative manifest reference: [docs/manifests/tool.md](../../../../docs/manifests/tool.md) ·
contract reference: [docs/references/tool-contract.md](../../../../docs/references/tool-contract.md).

## Tool class

Declare the parameters as a Pydantic `Input` model. The JSON Schema advertised to the model is derived from it
(`get_parameters_schema()`), and arguments are validated before `execute()` runs.

```python
from pydantic import BaseModel, Field
from mas.runtime.contracts import ToolContract


class AddTool(ToolContract):
    class Input(BaseModel):
        a: float = Field(..., description="First addend")
        b: float = Field(0, description="Second addend (default 0)")

    def get_name(self) -> str:
        return "add"

    def get_description(self) -> str:
        return "Add two numbers."

    def execute(self, **kwargs) -> dict:
        args = self.Input(**kwargs)          # validated by Pydantic
        return {"result": args.a + args.b}
```

### Methods

| Method | Required | Purpose |
| --- | --- | --- |
| `get_name()` | yes | Call name the model uses |
| `get_description()` | no | Text shown to the model |
| `Input` (nested `BaseModel`) or `get_parameters_schema()` | no | Parameter schema (the nested model is the recommended style; overriding `get_parameters_schema()` with hand-written JSON Schema is the legacy style) |
| `execute(**kwargs)` | yes | Synchronous implementation; returns a `dict` |
| `is_idempotent()`, `is_read_only()`, `is_destructive()`, `is_open_world()` | no | Hints for retry and governance; `None` means unspecified |
| `get_timeout_seconds()` | no | Per-tool default timeout |
| `get_output_schema()`, `get_title()`, `get_semantics()` | no | Optional advertise extras |

Tools that serve several call names override `list_tools()` and `call_tool()` directly.

### Field constraints

Standard Pydantic constraints apply and appear in the advertised schema: `ge`/`le`/`gt`/`lt`,
`min_length`/`max_length`, `pattern`, defaults (`Field(10)` makes a parameter optional, `Field(...)` makes it required).
Give every field a `description`; the model reads it.

## Manifest

```yaml
apiVersion: mas/v1
kind: Tool
metadata:
  name: add
spec:
  description: Add two numbers.
  parameters:
    - name: a
      type: number
      required: true
    - name: b
      type: number
  impl:
    kind: python
    module_path: ./add_tool.py
    class_name: AddTool
```

Reference it from the agent:

```yaml
spec:
  tools:
    - ref: ./tools/add.tool.yaml
```

See [docs/manifests/agent.md](../../../../docs/manifests/agent.md) for the full `spec.tools` shape.

## Testing a tool

```python
def test_add():
    out = AddTool().call_tool("add", {"a": 2, "b": 2})
    assert out["result"] == 4
```

## Governance

Tool calls pass through the governance chokepoints (budget, guardrails, HITL approval) described in
[governance-policy-engine.md](../../governance-policy-engine.md); no change to the tool class is needed.

## Best practices

1. Describe what the tool does and when to call it; constrain inputs with Pydantic validators.
2. Return a structured `dict`; report expected failures as data (for example an `error` key) instead of raising.
3. Keep tools focused: one responsibility per tool.
4. Unit-test each tool, including the error path (for example a missing required argument).
5. Treat a changed name, schema or output as breaking: it can change agent behaviour.

For the plugin side of the API, see [plugin-and-tool-authoring.md](../../plugin-and-tool-authoring.md).
