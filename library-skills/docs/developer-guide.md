<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-library-skills Developer Guide

This guide explains how the skills plugins are built and how to extend them. It is for contributors who add a context source or a skill tool. Run the tests with `uv run pytest library-skills/tests/ -v`; user-facing usage is in the [user guide](user-guide.md).

## Module map

```
src/mas/library/skills/
├── lib/
│   ├── frontmatter.py   parse_skill_frontmatter(text) → (meta, body)
│   ├── resolver.py      resolve_skill_path(ref, base_dir) → Path | None
│   ├── registry.py      SkillRecord + SkillRegistry
│   └── spec.py          skill_refs_from_manifest(manifest) → list[str]
├── plugins/
│   ├── sk_catalog.py    SkillCatalogPlugin(ContextContract) + attach_skill_catalog_plugin()
│   ├── sk_tools.py      SkillToolsPlugin(ToolContract): activate_skill, list_skill_files, read_skill_file
│   └── sk_shell.py      RunSkillScriptPlugin(ToolContract): run_skill_script
└── __init__.py
tools/
├── skill-access.tool.yaml        YAML manifest for sk_tools
└── run-skill-script.tool.yaml    YAML manifest for sk_shell
overlays/
├── skills.yaml                   add skill-access tools
└── skills-shell.yaml             add skill-access + shell tools
examples/
└── quickstart/                   runnable before/after example
docs/
├── user-guide.md
├── spec-coverage.md
└── developer-guide.md            (this file)
```

## FSM integration

The plugin participates in two points of the MAS finite state machine (FSM):

### `ctx_collect_execute` (tier 1 — catalog injection)

`SkillCatalogPlugin(ContextContract)` fires on `ctx_collect_execute` via the
v0.1 bridge in `assemble_llm_messages()`:

```
assemble_llm_messages()
  └─ _inject_context_plugins(ctx, system_parts)
       └─ ctx.plugin_collection.collect_results("collect_context")
            └─ SkillCatalogPlugin.on_collect_context()
                 └─ SkillCatalogPlugin.collect_context() → [ContextPart.skills(...)]
```

Parts are sorted by `ContextPlacement` band and priority before injection. `SYSTEM_SKILLS` (priority 40) sits between `SYSTEM_TOOLS` (30-39) and `SYSTEM_ONTOLOGY` (50-59).

### `tool_execute` (tier 2/3 — stochastic path)

`SkillToolsPlugin(ToolContract)` fires when the model calls `activate_skill`, `list_skill_files` or `read_skill_file`. The tool receives `ctx` as a kwarg in `on_execute_tool(**kwargs)` and reads `ctx.skill_registry` to look up paths.

---

## PluginCollection

`runtime.boundary.context.plugin_collection.PluginCollection` connects the library plugin to the context assembly pipeline. It implements:

- `register(plugin)` — append to ordered collection
- `collect_results(hook_name)` — call `on_{hook_name}()` on all registered plugins, flatten results
- `get_plugins_by_type(contract_type)` — filter by type

This is the interface `ContextAssemblerPlugin` (library-standard) expects as `agent.registry`.

---

## SkillRegistry

`lib/registry.py` holds the session-scoped `SkillRecord` → path mapping.
It is built once by `SkillCatalogPlugin.__init__()` and stored on
`ctx.skill_registry` so `SkillToolsPlugin` can look up paths at tool-call time
without re-scanning the filesystem.

`SkillRecord` is a frozen dataclass:
```python
SkillRecord(name="answer-formatting", description="...", path=Path("/...SKILL.md"))
rec.base_dir  # parent of SKILL.md — root for resource paths
```

---

## Frontmatter parsing

`lib/frontmatter.py` parses `SKILL.md` files following the
[Agent Skills specification](https://agentskills.io/specification):

1. Opening `---` at start of file
2. YAML block
3. Closing `---`
4. Markdown body

**Lenient fallback:** values with unquoted colons (common in descriptions from other clients) are re-quoted and parsed again.

**Validation policy:**
- Missing/unparseable YAML → `{}` meta, full text as body; warn
- Missing `description` → skill skipped from catalog; warn
- Name mismatch with directory → warn, load anyway

---

## Add a new context source plugin

```python
from mas.runtime.contracts.context_contract import ContextContract, ContextPart

class MySourcePlugin(ContextContract):
    def collect_context(self) -> list[ContextPart]:
        return [ContextPart.skills("my content", source="my-source")]
```

Register it at bootstrap:
```python
ctx.plugin_collection.register(MySourcePlugin())
```

---

## Add a new tool

1. Implement `ToolContract` in `plugins/my_tool.py`
2. Add `on_execute_tool(tool_name, arguments, **kwargs)` — extract `ctx` from `kwargs`
3. Add `on_collect_tools(**_)` returning the tool schema list
4. Create `tools/my-tool.tool.yaml` with `spec.impl.module_path` pointing to the class
5. Add to `library.yaml` under `plugins:`
6. Add tests in `tests/test_my_tool.py`

## Testing approach

```
tests/
├── test_frontmatter.py      Unit: parse_skill_frontmatter (7 cases)
├── test_catalog_plugin.py   Unit: SkillCatalogPlugin + attach_skill_catalog_plugin (9 cases)
├── test_skill_tools.py      Unit: SkillToolsPlugin (activate/list/read, security guards)
├── test_skill_shell.py      Unit: RunSkillScriptPlugin (exec, traversal guard, timeout)
└── test_quickstart.py       Integration: before/after skill overlay
```

## Naming conventions

- `sk_*` prefix: skill-related plugins (catalog, tools, shell)
- `lib/` modules: pure functions, no plugin lifecycle
- `SkillRecord` is frozen (immutable after construction)
- All file reads are `encoding="utf-8"` — no locale assumptions

---

## Security model

| Operation | Constraint |
|-----------|-----------|
| `read_skill_file` | Path resolved under skill `base_dir`; `ValueError` on escape |
| `run_skill_script` | Basename only (no path separators); resolved under `scripts/`; `ValueError` on escape |
| `subprocess.run` | `capture_output=True`; timeout capped at 120 s |
| Interpreter | `.py` → `sys.executable`, `.sh` → `/bin/sh`, other → direct exec |
