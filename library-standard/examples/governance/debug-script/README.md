<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# gdb example

gdb-like breakpoints as a **governance** plugin (`gdb`). A Control names it, a Flavour
attaches the Control; the agent does not carry it.

```yaml
# control.yaml
spec:
  governance:
    - gdb: {script_file: ./debug.gdb}
```

```bash
mas-ctl validate library-standard/examples/governance/debug-script/agent.yaml
mas-ctl validate library-standard/examples/governance/debug-script/control.yaml
mas-ctl chat library-standard/examples/governance/debug-script/agent.yaml \
  --flavour library-standard/examples/governance/debug-script/flavour.yaml \
  --checkpoint-dir .mas/debug-script-checkpoints
```

`--debug-script @library-standard/examples/governance/debug-script/debug.gdb` writes the same Control for you.

`recover.ctl` pauses and persists a stopped session. `steer-fix.ctl` steers
a chat resumed from that checkpoint.
