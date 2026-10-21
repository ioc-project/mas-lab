<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-library-standard

Reusable multi-agent system (MAS) artifacts: plugins, tools, skills, prompts, flavours and overlays.

Use it when you want ready-made governance, context, observability or design-pattern plugins for an agent without writing them yourself.

```bash
uv pip install -e library-standard
mas-ctl chat agent.yaml -o pkg://mas.library.standard/overlays/observability-native.yaml
```

## Documentation

- User guide: [docs/user-guide.md](docs/user-guide.md)
- Developer guide: [docs/developer-guide.md](docs/developer-guide.md)
- Plugin id cards: [src/mas/library/standard/plugins/README.md](src/mas/library/standard/plugins/README.md)
- Overlay index: [src/mas/library/standard/overlays/README.md](src/mas/library/standard/overlays/README.md)
- Examples: [examples/](examples/README.md) (by plugin kind; Agent or MAS)

Shipped overlays: `observability-native`, `with-hardened`, `cheap-summarizer`.
Governance example: [examples/governance/undeclared-tool/](examples/governance/undeclared-tool/).
Summarizer example: [examples/context/summarizer-override/](examples/context/summarizer-override/).