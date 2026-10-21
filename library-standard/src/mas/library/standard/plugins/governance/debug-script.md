<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# debug_script — runtime debugger

| Field | Value |
|-------|--------|
| **ID** | `debug_script@v1` |
| **Alias** | `debug_script`, `gdb` |
| **Kind** | runtime |
| **URN** | `mas.runtime.debug_script` |
| **Implementation** | `DebugScriptPlugin` in `mas.library.standard.plugins.governance.debug_script` |
| **Enable** | `config.yaml` `plugins:` / `lab.enable_plugins` |
| **Script** | `spec.debug` (`script` or `script_file`) or `--debug-script` |
| **Example** | [examples/governance/debug-script/](../../../../../../examples/governance/debug-script/) |

gdb-like breakpoints observe tool-call / tool-result so the process can act
as a debugger. They do not BLOCK. Observability and governance stay on the
agent spec.

```text
break tool_result web_search if first
commands
  checkpoint
  info checkpoints
  info session
  info working_memory
  continue
end
```
