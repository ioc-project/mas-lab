<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Design patterns

Reasoning patterns (ReAct, chain-of-thought, plan/execute, tree-of-thoughts, and others) are
**runtime plugins** registered in `mas-library-standard` and selected from agent manifests or
**overlays** (`kind: Overlay`).

## Enable via overlay

An overlay selects the pattern for one agent of a MAS (`type` is a plugin alias or URN, `config` is
passed to the plugin):

```yaml
apiVersion: mas/v1
kind: Overlay
metadata:
  name: react-moderator
spec:
  target:
    kind: MAS
  patch:
    agents:
      moderator:
        design_pattern:
          type: react
          config:
            max_steps: 10
```

Apply it with `mas-ctl chat mas.yaml --overlay react-moderator.yaml` (or `run-mas`). Working examples are in
`library-samples/overlays/` (`react-moderator.yaml`, `cot-moderator.yaml`, `reflection-moderator.yaml`).

## Plugin implementations

Design-pattern plugins ship in `library-standard`, under
`library-standard/src/mas/library/standard/plugins/design_patterns/` (`react.py`, `cot.py`, `plan_execute.py`,
`tree_of_thoughts.py`, `introspection.py`, `single_pass.py`, ...). Each implements the Mealy
design-pattern contract (`runtime/src/mas/runtime/machines/design_pattern/protocol.py`) and composes with
governance and observability on the kernel envelope path. `mas-ctl registry list` shows the registered patterns.

Integration tests: `runtime/tests/integration/test_design_pattern_plugins_integration.py`.

## Formal model

The design-pattern automaton \(M_{dp}\) is one factor in the runtime product. See
[automaton-product-model.md](automaton-product-model.md).

## Tutorials

- [Building an agent](../../docs/tutorials/01-building-an-agent/README.md)
- [Creating a MAS](../../docs/tutorials/02-creating-a-mas/README.md)

## Related

- [production-path.md](production-path.md) — supported execution path
- [plugin-and-tool-authoring.md](plugin-and-tool-authoring.md) — writing plugins
- [contracts-reference.md](contracts-reference.md) — contract families
