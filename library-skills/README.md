# mas-library-skills

Adds [Agent Skills](https://agentskills.io) support to MAS Lab. A skill is a directory with a `SKILL.md` file that the agent loads on demand ("progressive disclosure"): the prompt carries only a catalog, and the model fetches full instructions when it needs them.

Use it when an agent needs specialised instructions, reference files or scripts without paying for them in every prompt.

## Quick start

```bash
uv add mas-library-skills
```

```yaml
# agent.yaml
apiVersion: mas/v1
kind: Agent
metadata:
  name: my-agent
spec:
  models:
    - model: gpt-4o-mini
  context:
    role: "Answer questions helpfully."
  skills:
    - answer-formatting          # points to ./skills/answer-formatting/SKILL.md
  tools:
    - ref: skills:tools/skill-access.tool.yaml
```

```bash
mas-ctl chat agent.yaml -q "What is the speed of light?"

# or add skills with an overlay; the base manifest never changes
mas-ctl chat agent.yaml -o skills:overlays/skills.yaml -q "What is the speed of light?"
```

`spec.skills` is the only place a manifest declares an agent's skills. It is a flat list; the context-manager plugin (which only picks a context-window strategy such as stack or sliding window) has no skills setting. Each entry is a bare name, resolved through the standard locator chain (app-local `skills/`, declared libraries, installed packages), or `@library/name` for an explicit source.

## Components

| Component | Kind | Description |
|-----------|------|-------------|
| `SkillCatalogPlugin` | `ContextContract` | Injects the skill catalog (name + description) into the `SYSTEM_SKILLS` band |
| `SkillToolsPlugin` | `ToolContract` | `activate_skill`, `list_skill_files`, `read_skill_file` |
| `RunSkillScriptPlugin` | `ToolContract` | `run_skill_script`: executes scripts in `scripts/` |
| `ContextPart.skills()` | runtime shorthand | `ContextPart` constructor for `SYSTEM_SKILLS` placement |
| `PluginCollection` | runtime utility | `collect_results()` dispatch matching the `ContextAssemblerPlugin` interface |

Skills are not a manifest-level contract. `spec.skills` supplies the content (which skills exist); the two plugins above serve it through existing runtime contracts:

| Plugin | Contract | Role |
|--------|----------|------|
| `SkillCatalogPlugin` | `ContextContract` | Injects the tier-1 catalog (name + description) into `SYSTEM_SKILLS` |
| `SkillToolsPlugin` | `ToolContract` | `activate_skill` / `list_skill_files` / `read_skill_file` (tiers 2-3) |

## Engines

Three interchangeable engines do discovery, frontmatter parsing and script execution. `SkillPluginRegistry(impl="native" | "adk" | "langchain")` selects one and imports the matching `plugin_skills_*.py` module. In overlay or manifest tool entries, set `impl` (and optionally `base_dir`); bootstrap passes the choice to both catalog injection and skill tool execution.

| Engine | Framework wrapped | Sandboxing | Notes |
|--------|--------------------|------------|-------|
| **native** (default) | bundled `agentskills` parser + script runner | best-effort environment filtering, timeout, POSIX rlimits | for cooperative local scripts; not a security sandbox |
| **adk** | `google.adk.skills` (`google-adk`) | none (delegated to ADK) | loads resources eagerly in memory |
| **langchain** | `deepagents` (LangGraph agent harness) | none (delegated to deepagents) | about 70 lines of adapter code, because deepagents is tool-call-oriented and has no plain "give me the skill body" API |

The base install includes the agentskills.io parser and the skill sandbox. The ADK and LangChain engines wrap optional third-party frameworks:

```bash
uv add "mas-library-skills[adk]"        # google-adk
uv add "mas-library-skills[langchain]"  # deepagents
uv add "mas-library-skills[all]"        # both
```

`task install-dev`, `task ci` and `task verify` install the `[all]` extra, so the full test suite (including `tests/test_skill_plugins_functional.py`, which exercises the real frameworks) runs by default.

### Security boundary

The native script runner is not a security boundary. It runs a local subprocess with a filtered environment, a timeout and best-effort CPU and address-space limits. It provides no filesystem isolation, network isolation, container boundary or complete syscall policy. Do not execute untrusted skill code with it; use Docker or another externally enforced isolation boundary.

## Further reading

- [docs/user-guide.md](docs/user-guide.md): writing skills, tools, overlays.
- [docs/developer-guide.md](docs/developer-guide.md): the plugin interface.
- [docs/spec-coverage.md](docs/spec-coverage.md): agentskills.io specification coverage matrix.
- `examples/quickstart/`: runnable before/after example. `tests/test_skill_plugins_functional.py` compares the engines.
