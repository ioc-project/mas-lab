<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# mas-library-skills User Guide

`mas-library-skills` implements the [Agent Skills specification](https://agentskills.io) for MAS Lab. Agents load specialised instructions on demand instead of carrying them all in the context window. This guide is for authors who write skills and attach them to agents.

Smallest example: list a skill in the agent manifest and chat.

```yaml
spec:
  skills:
    - answer-formatting      # ./skills/answer-formatting/SKILL.md
```

```bash
mas-ctl chat agent.yaml -q "What is the speed of light?"
```

## Progressive disclosure

| Tier | What the model sees | When | Token cost |
| ------ | --------------------- | ------ | ------------ |
| 1 — Catalog | Name + description of each skill | Session start (always) | ~50–100 per skill |
| 2 — Instructions | Full `SKILL.md` body | When model calls `activate_skill(name)` | <5000 (recommended) |
| 3 — Resources | Scripts, references, assets | When model calls `read_skill_file(skill, path)` | Varies |

The catalog holds each skill's name and when-to-use text from the frontmatter. The description tells the model when the skill applies and to call `activate_skill(name)`. The body says how to apply the skill and is not in the catalog.

## Step 1 — Write a SKILL.md

A skill is a directory containing a `SKILL.md` file:

```
skills/
└── answer-formatting/
    ├── SKILL.md            ← required
    ├── references/         ← optional: files loaded via read_skill_file
    └── scripts/            ← optional: scripts run via run_skill_script
```

The `SKILL.md` has YAML front matter and a Markdown body:

```markdown
---
name: answer-formatting
description: >
  Use when answering factual questions. Call `activate_skill("answer-formatting")`
  first and follow the loaded instructions; the catalog text is when-to-use
  only, not the layout.
tags: [formatting, qa]
---
# Answer Formatting

## Rules

1. Start with a **one-sentence summary**.
2. Follow with **2-3 bullet points** of supporting detail.
3. End with a confidence indicator: HIGH / MEDIUM / LOW.
```

Required front matter fields:

- `name`: must match the directory name (a mismatch logs a warning; the skill still loads).
- `description`: when to use the skill, plus a cue to call `activate_skill(name)` and follow the loaded body.

## Step 2 — Reference the skill in the agent manifest

```yaml
spec:
  skills:
    - answer-formatting      # relative to the manifest directory
```

The runtime searches for `SKILL.md` in:

1. `<manifest_dir>/answer-formatting/SKILL.md`
2. `<manifest_dir>/skills/answer-formatting/SKILL.md`
3. Walking up to the nearest `skills/` directory (library-level shared skills)

Every non-namespaced skill listed in `spec.skills` must resolve to an existing
`SKILL.md` through these search paths. A missing skill fails bootstrap with an
error naming the missing reference; it is never silently omitted from the
system prompt. A present file with invalid front matter is reported through the discovery parser's validation warnings.

## Step 3 — Skill tools (implicit, or explicit in the manifest)

The model sees nothing from the skills system by default.

Listing at least one skill adds `activate_skill`, `list_skill_files` and `read_skill_file` as system tools, enables the LLM tool loop so the tools are advertised, and injects the catalog (name + frontmatter description) into the system prompt.

```yaml
spec:
  skills:
    - answer-formatting
```

If a listed skill has a `scripts/` directory with at least one file,
`run_skill_script` is added the same way.

To declare the same tools explicitly:

```yaml
spec:
  tools:
    - kind: system
      name: activate_skill
    - kind: system
      name: run_skill_script   # even if no skill ships scripts yet
```

The older explicit form, a YAML ref (`skills:tools/skill-access.tool.yaml`), still works. You can also use the overlay:

```bash
mas-ctl chat agent.yaml \
  -o skills:overlays/skills.yaml \
  -q "What is the speed of light?"
```

## What the model receives

**Session start (tier 1):**

```
## Available Skills

Listed skills show name and when-to-use from each skill's frontmatter.
Full instructions load via `activate_skill(name)`.

- **answer-formatting**: Use when answering factual questions. Call
  `activate_skill("answer-formatting")` first and follow the loaded
  instructions; the catalog text is when-to-use only, not the layout.
```

**After calling `activate_skill("answer-formatting")` (tier 2):**

```xml
<skill_content name="answer-formatting">
# Answer Formatting

## Rules
...

<skill_resources>
  <file>references/examples.md</file>
</skill_resources>

Skill directory: /path/to/skills/answer-formatting
</skill_content>
```

## Available tools

### `activate_skill(name)`

Loads the full `SKILL.md` body. Returns the Markdown body (front matter stripped) wrapped in `<skill_content>` tags, plus a listing of bundled resource files.

```json
{"name": "answer-formatting"}
→ {"content": "<skill_content name=\"answer-formatting\">...", "skill": "...", "base_dir": "..."}
```

The `unload` parameter is off by default, is not advertised and is not part of the Agent Skills spec. Opt in on the skill engine:

```yaml
spec:
  context_sources:
    - native:
        allow_unload: true
```

When enabled, the same tool also accepts `unload=true` to unpin a loaded
skill. The catalog listing stays; prior tool results in the transcript are
not rewritten.

```json
{"name": "answer-formatting", "unload": true}
→ {"notice": "Skill 'answer-formatting' is no longer in force. ...", "deactivated": true}
```

### `list_skill_files(skill)`

List all files in a skill's directory (except `SKILL.md` itself).

```json
{"skill": "answer-formatting"}
→ {"files": ["references/examples.md"], "base_dir": "..."}
```

### `read_skill_file(skill, path)`

Reads one file from the skill's directory. The path is relative to that directory; paths that escape it are rejected.

```json
{"skill": "answer-formatting", "path": "references/examples.md"}
→ {"content": "# Examples\n...", "skill": "...", "path": "..."}
```

## Shell tool (optional — trusted environments only)

`run_skill_script` executes scripts from a skill's `scripts/` directory.
It is added implicitly when a listed skill already has a `scripts/` file.
To opt in without shipping scripts, declare the system tool (or set
`auto_inject` on the skill engine):

```yaml
spec:
  tools:
    - kind: system
      name: run_skill_script
```

Or use the shell overlay:

```bash
mas-ctl chat agent.yaml \
  -o skills:overlays/skills-shell.yaml \
  -q "Analyse this data"
```

Warning: enable this only in trusted environments where skill scripts have been reviewed. The subprocess runs with the agent process's operating-system permissions.

To grant it to every skill-declaring agent in a deployment without hand-listing
the tool ref on each one, opt in per-deployment instead:

```yaml
spec:
  context_sources:
    - native:
        auto_inject: true
```

## Overlays reference

| Overlay | What it adds |
| --------- | ------------- |
| `skills:overlays/skills.yaml` | Explicit `{kind: system, name: activate_skill}` (same tools are implicit if `spec.skills` is listed) |
| `skills:overlays/skills-shell.yaml` | Explicit `activate_skill` + `run_skill_script` |

## Quickstart example

`examples/quickstart/` is a runnable example that shows the before and after effect of adding a skill overlay.
