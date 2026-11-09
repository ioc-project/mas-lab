<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Plugin names

A plugin has a **URN** (`mas.dp.react`, the identity), a **name** (`react`, the `name:` in its `library.yaml`
entry), and, sparingly, a **shortcut** or a **versioned name** (`react@v1`). Use them with parsimony: a plugin gets
one extra name only when people really write it. The bare name or shortcut stands for the default implementation,
so `react` can later point at another implementation while `react@v1` keeps meaning the first.

Names are unique within a type. `native` is both an observability plugin and a skill engine (the native
implementation of agentskills.io), and `drop` is both a comm stack layer and a context summarizer: a name always
arrives with a type, from the manifest key (`spec.design_pattern: react`) or from the list a Control names it in.
A name has no `.`, because `.` separates the parts of a URN.

## Lookup

- By type and name: `registry.resolve_by_type("design_pattern", "react")`. The name, shortcut, versioned name or
  URN must match exactly. There is no suffix, prefix or case guessing.
- By name alone, when a script has no type: `entries_named("react")` lists every plugin with that name.
- `entries_named(name, types)` limits it to the types a list takes, which is how a Control picks the right `native`.

Two plugins of one type that answer to the same name fail at registration, and so does a name with a `.`.

## Deprecated names

`aliases.yaml` has a `deprecated_aliases` section: names that were only another way to say the same thing
(`analyst`, `planner`, `linear`, `rate_limiter`, ...). They still resolve, with a one-time warning that names the
replacement, until a later release removes them.

## Not the same thing

`plugin_id` on a plugin class (for example `react@v1`) is the versioned identity the plugin reports about itself;
the versioned name in a manifest resolves to the same plugin.
