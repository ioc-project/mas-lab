<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# `mas-runtime`

`mas-runtime` is the embeddable Mealy kernel. It has no session CLI.

The `mas-runtime` console script is a leftover entry point. It prints a pointer
and exits 2:

```text
The mas-runtime CLI was removed. Use mas-ctl instead:
  mas-ctl chat <manifest> -q "..."
  mas-ctl validate <manifest>
Library API: mas.runtime + mas.ctl.session.bootstrap
```

| You want | Use |
|----------|-----|
| Chat, validate, compile, run a MAS | [`mas-ctl`](mas-ctl.md) |
| Benchmarks, telemetry, plots, controller | [`mas-lab`](mas-lab.md) |
| Embed the kernel in Python | `import mas.runtime` and `mas.ctl.session.bootstrap` |

Package map: [Packages](../what-is/packages.md). Contributor docs stay in the
source tree under [`runtime/docs/`](https://github.com/ioc-project/mas-lab/blob/main/runtime/docs/index.md)
and are indexed from [Runtime package docs](../references/runtime.md).
