<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Revocation (offline environment example)

After the second `pay_fare` tool result, the lab revokes the credential at a
loopback issuer. `revoked_mid` still sees two allowed calls (the payments
cache has not refreshed). `revoked_mid_wait` advances a virtual clock 21 s
and then sees zero.

```bash
mas-lab benchmark run labs/revocation.lab/experiment.yaml --force
```

The third `pay_fare` is still allowed on `revoked_mid` (payments cache).
`revoked_mid_wait` advances 21 s on the third `pay_fare` tool call, so that
pay is denied.

`traces/environment.jsonl` holds `trigger.matched` with
`anchor.occurrence == 2` immediately followed by `action.done` for
`http_call`, before any anchor with occurrence 3.

Fixtures under `fixtures/` import nothing from the lab. Tutorial: `docs/tutorials/15-world-events-and-fixtures/README.md`.
