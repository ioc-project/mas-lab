<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# gdb — GovernancePlugin

| Field | Value |
|-------|--------|
| **ID** | `gdb@v1` |
| **Name** | `gdb` |
| **Kind** | governance |
| **URN** | `mas.gov.gdb` |
| **Implementation** | `DebugScriptPlugin` in `mas.library.standard.plugins.governance.debug_script` |
| **Manifest keys** | Control `spec.governance` (`script` or `script_file`), or `--debug-script` |
| **Example** | [examples/governance/debug-script/](../../../../../../examples/governance/debug-script/) |

gdb-like breakpoints observe tool-call / tool-result so the process can act
as a debugger. They do not BLOCK. A Control names the plugin and a Flavour attaches
the Control; the agent does not carry it.

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
