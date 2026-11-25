<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# none — identity scheme

| Field | Value |
|-------|--------|
| **Alias** | `none` |
| **Kind** | identity_scheme |
| **URN** | `mas.identity_scheme.none` |
| **Implementation** | `NoIdentity` in `mas.library.standard.plugins.identity.none` |
| **Capabilities** | `verify`, `describe` |

The explicit "no identity" scheme, a wallet of one identity (`anonymous`). A call presents nothing; an inbound caller
is verified as `anonymous`; `describe` reports `anonymous: true` so a policy can decide. It takes no params. Writing
`scheme: none` makes "no identity" a declared choice rather than an accident, and it is the smallest scheme that runs
the identity contract, the host's binding and the identity events end to end.

```yaml
- id: public-docs
  scheme: none
```
