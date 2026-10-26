<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# agentskills.io Specification Coverage

This page lists which parts of the agentskills.io specification `mas-library-skills` implements. It is for contributors and for users who port skills from other clients. It follows the
[agentskills.io client implementation guide](https://agentskills.io/client-implementation/adding-skills-support)
and [specification](https://agentskills.io/specification).

**Last reviewed:** 2026-08-26  
**Spec version:** agentskills.io (2026-08)

Legend: ✅ implemented, ⚠️ partial, ❌ not implemented, 🔲 out of scope.

---

## Step 1 — Discover skills

| Feature | Status | Notes |
|---------|--------|-------|
| Project-level scan (manifest dir + parent walk) | ✅ | `lib/resolver.skill_search_roots()` |
| App-level `<dir>/skills/` subfolder | ✅ | Included in search roots |
| Project-level `.agents/skills/` (cross-client) | ✅ | `<base_dir>/.agents/skills/` |
| User-level `~/.agents/skills/` | ✅ | Included in search roots |
| MAS Lab user-level `~/.mas/skills/` | ✅ | Included in search roots |
| Client-specific `~/.<client>/skills/` | ❌ | Not implemented; use `.agents/skills/` |
| Ancestor dirs (monorepo) | ✅ | `agentskills.discovery.Discovery._walk_ancestors()` walks parent `skills/` dirs (nearest first) up to, not including, `~`; no git required |
| XDG config directory scan | ❌ | Not implemented |
| Scan depth / directory bounds | ❌ | No max-depth guard (not yet needed at manifest-ref scale) |
| Skip `.git`, `node_modules` | ❌ | Not needed at manifest-ref scale |
| `.gitignore` respect | ❌ | Not implemented |
| Name collision detection + warning | ✅ | First-found wins; collision logged as WARNING |
| Trust level gating for project skills | 🔲 | MAS Lab uses manifest-explicit refs only; no untrusted auto-scan |
| Cloud-hosted / sandboxed discovery | 🔲 | Use `spec.skills` refs from manifest |

---

## Step 2 — Parse SKILL.md files

| Feature | Status | Notes |
|---------|--------|-------|
| Opening/closing `---` frontmatter extraction | ✅ | `lib/frontmatter.parse_skill_frontmatter()` |
| `name` field — required | ✅ | Stored in `SkillRecord.name` |
| `description` field — required | ✅ | Stored in `SkillRecord.description`; skill skipped if missing |
| `license` field — optional | ✅ | Stored in `SkillRecord.license` |
| `compatibility` field — optional | ✅ | Stored in `SkillRecord.compatibility`; returned by `activate_skill` |
| `tags` field — optional | ✅ | Stored as `SkillRecord.tags: tuple[str, ...]` |
| `metadata` field — optional | ✅ | Stored in `SkillRecord.metadata: tuple[tuple[str,str],...]`; `.metadata_dict` accessor |
| `allowed-tools` field — experimental | ✅ | Stored in `SkillRecord.allowed_tools: tuple[str, ...]`; returned by `activate_skill` |
| Malformed YAML fallback (unquoted colons) | ✅ | `_parse_yaml_block()` retries with quoting fix |
| Name > 64 chars → warn, load anyway | ✅ | Lenient: warning logged, skill still loaded |
| Name doesn't match directory → warn, load | ✅ | Lenient: warning logged, skill still loaded |
| Description missing → skip, log error | ✅ | Enforced: skill excluded from catalog |
| YAML completely unparseable → skip, log | ✅ | Falls back to empty meta; description missing → skip |
| Body content (after `---`) stored | ✅ | Read at activation time (`activate_skill`) |
| Source scope tracking | ✅ | `SkillRecord.source_scope`: project \| user \| builtin |

---

## Step 3 — Disclose available skills to the model

| Feature | Status | Notes |
|---------|--------|-------|
| Catalog injected at session start | ✅ | `SkillCatalogPlugin.collect_context()` → `SYSTEM_SKILLS` band |
| Name + description in catalog | ✅ | Bullet-list format in system prompt |
| When-to-use + activate cue | ✅ | Each skill's frontmatter `description` is listed verbatim; short catalog header points at `activate_skill` |
| Location field in catalog | ⚠️ | `activate_skill` response includes `base_dir`; catalog itself omits it |
| Catalog placement: system prompt band | ✅ | `ContextPlacement.SYSTEM_SKILLS`, priority 40, pinned=True |
| Catalog placement: tool description embedding | ❌ | Only system-prompt placement supported |
| No catalog when no skills available | ✅ | Empty catalog → nothing injected, no empty block |
| Skill filtering (disabled/permission) | ✅ | `disabled: true` in frontmatter skips the skill entirely |
| `activate_skill` enum of valid skill names | ✅ | `SkillToolsPlugin(registry=…)` adds JSON Schema `enum` to name parameter; description lists names |
| Don't register tool when no skills | ✅ | `SkillToolsPlugin.on_collect_tools()` returns `[]` when no registry / empty registry; plugin is not injected unless `spec.skills` or `{kind: system, name: activate_skill}` |

---

## Step 4 — Activate skills

| Feature | Status | Notes |
|---------|--------|-------|
| Dedicated `activate_skill` tool | ✅ | `sk_tools.SkillToolsPlugin` |
| File-read activation via standard tool | ✅ | `read_skill_file(skill, path)` covers this path |
| Frontmatter stripped from returned body | ✅ | `parse_skill_frontmatter()` → body only |
| Structured wrapping (`<skill_content>`) | ✅ | `<skill_content name="…">…</skill_content>` |
| Skill directory path in response | ✅ | `"base_dir"` field in response dict |
| `<skill_resources>` listing | ✅ | `scripts/`, `references/`, `assets/`, root files listed |
| Resources not eagerly loaded | ✅ | Listed only; loaded via `read_skill_file` |
| `compatibility` note in activation response | ✅ | `"compatibility"` field included if non-empty |
| User-explicit activation (slash commands) | ✅ | `/skill <name>` and `/skills` session commands; `--skill <name>` CLI flag |
| `activate_skill` JSON Schema `enum` constraint | ❌ | Only in description text, not in `parameters.enum` |
| Permission allowlisting for skill dirs | ❌ | Governance applies at `ToolContract` boundary only |

---

## Step 5 — Manage skill context over time

| Feature | Status | Notes |
|---------|--------|-------|
| Deduplication — track activated skills | ✅ | `lib/session.SkillSessionState` |
| Re-activation returns notice (not body) | ✅ | `{notice: "…", already_activated: True}` |
| Re-activation counter for telemetry | ✅ | `SkillSessionState.notices` |
| Catalog pinned against context compaction | ✅ | `ContextPart.skills(…, pinned=True)` |
| Activated skill body pinned against compaction | ✅ | `ActivatedSkillsContextPlugin` emits activated bodies as pinned `SYSTEM_SKILLS` parts |
| Skill tool outputs exempt from history compaction | ✅ | `activate_skill` attaches `chunk.pin_policy: never`; context managers fold from that metadata (`lib/context/pinned_history.py`) |
| Subagent delegation (optional) | 🔲 | Advanced; MAS Lab supports multi-agent patterns separately |

---

## Shell execution (bonus — beyond spec)

| Feature | Status | Notes |
|---------|--------|-------|
| `run_skill_script` tool | ✅ | `sk_shell.RunSkillScriptPlugin` |
| Path traversal guard (scripts/ only) | ✅ | Plain filename; resolved path checked against `scripts_dir` |
| Environment sanitization (`_SAFE_ENV_KEYS` allowlist) | ✅ | API keys / tokens stripped |
| Caller-supplied `env` passthrough | ✅ | Merged after safe base env |
| POSIX resource limits (CPU + memory) | ✅ | `preexec_fn=_posix_set_limits` (best-effort) |
| Timeout cap | ✅ | Default 30s, max 120s |
| Interpreter selection (.py/.sh/shebang) | ✅ | Extension-based |
| Working directory = skill base_dir | ✅ | `cwd=str(record.base_dir)` |

---

## Specification field coverage

| Field | Required | Stored | Used |
|-------|----------|--------|------|
| `name` | Yes | `SkillRecord.name` | Catalog, registry key, dedup |
| `description` | Yes | `SkillRecord.description` | Catalog; skip if missing |
| `license` | No | `SkillRecord.license` | Stored; not yet surfaced in responses |
| `compatibility` | No | `SkillRecord.compatibility` | Returned in `activate_skill` response |
| `tags` | No | `SkillRecord.tags` | Stored; not yet used for filtering |
| `metadata` | No | `SkillRecord.metadata` | `metadata_dict` accessor available |
| `allowed-tools` | No | `SkillRecord.allowed_tools` | Stored; governance wiring not yet implemented |

---

## What's not implemented and why

| Feature | Decision |
|---------|----------|
| `metadata` + `allowed-tools` fields | Stored in raw `meta` dict; not yet wired to anything useful. Wiring them into `GovernancePolicyEngine` needs skill and activation state in the condition data of `evaluate_trigger()`; that engine is declarative and YAML-driven (trigger, condition, action; see `runtime/src/mas/runtime/boundary/gov/policy_engine.py`). |
| User-explicit skill activation (`/skill-name`) | Needs a session-layer hook (for example a `mas-ctl chat` pre-processor). |
| Skill filtering / disable flag | No per-skill disable mechanism in MAS Lab manifests today. Use `spec.skills` ref list as the allowlist (omit unwanted skills). |
| `activate_skill` JSON Schema `enum` | `ManifestToolProvider` loads tools without access to a live registry at schema time. Workaround: description text lists valid names. Full enum requires registry-aware tool loading. |
| Trust level for project skills | MAS Lab uses explicit manifest refs only (no auto-discovery of untrusted repos). Not applicable. |
| Subagent delegation | Supported generically by MAS Lab multi-agent patterns; no skill-specific delegate wrapper needed. |
| Cloud/sandboxed skill discovery | Skills travel with manifests in MAS Lab. Remote registries are out of scope. |
