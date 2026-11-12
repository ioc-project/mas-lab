<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Control manifest (`kind: Control`)

**Package:** `runtime/` | **Schema:** [`control.schema.yaml`](../schemas/runtime/control.schema.yaml)

A **Control** (`kind: Control`) holds the observability and governance plugin
sets a [Flavour](flavour.md) attaches to agents. Those lists used to sit on
the agent spec; they live here. A Control defines no endpoint and no infra row.
Every entry is a plugin id; the schema does not name `retry`, `circuit_breaker`,
or `native` as properties. Overlay patches that set these keys target
`kind: Control`. Attach does not copy them onto the agent. Leftover Agent
`spec.observability` / `spec.governance` are deprecated and still applied when
no matching Control names them.

A Flavour lists Control files under `spec.control`. Each row is `{ref, agents}`.
`agents` is `$all` (default), `$entry` (`workflow.entry` or `spec.entry_agent`; omitted, nobody), `$not-entry`, `$delegates`, one agent id,
a list of ids, or the same agent path as `--override` (`.mas.agent[id]`,
`.mas.agent[id=name]`, `.mas.agent[*]`, `mas:agent[id]`). That is the
same idea as Flavour `bind`: `$all` is the default chain, one id is
`bind.agents.<id>`. A field after the agent is not a selector. When the Flavour
row omits `agents`, the Control's own `spec.agents` is the default (`$all`).

```yaml
apiVersion: control/v1
kind: Control
metadata:
  name: company-baseline
spec:
  agents: $all
  observability:
    - mas.observability.native
  governance:
    - sample_governance
    - budget: {max_llm_calls: 50, max_tool_calls: 100}
```

A Flavour attaches it:

```yaml
apiVersion: flavour/v1
kind: Flavour
metadata: {name: desk}
spec:
  bind:
    agent_comm: [desk-node]
  control:
    - ref: controls/company-baseline.yaml
      agents: $all
    - ref: controls/hitl.yaml
      agents: desk
    - ref: controls/company-baseline.yaml   # same file, other settings, on one agent
      agents: bill
      settings:
        budget: {max_llm_calls: 5}
```

`settings` is how one Control file gives different settings to different agents. Each key is a plugin
the Control lists, and its value is merged over that plugin's own settings for the agents of that row. A key the
Control does not list is an error, and the merged Control is validated again. The same file on the same
agents with the same settings is one attachment; with other settings or other agents it is another.

Every item names a registered plugin by its one name (see [plugin names](https://github.com/ioc-project/mas-lab/blob/main/runtime/docs/plugin-names.md)),
matched exactly: no suffix, prefix or alias matching. Two types can share a name (`native`), so the
name is looked up among the plugins whose registered type the list takes: `observability` or `governance`. It must match exactly one, or the Control is refused when it loads and
by `mas-ctl validate`. A name is a URN, a plugin's name or shortcut, or a versioned name such as `react@v1`;
none of them is a suffix of another. Nothing is looked up by a conventional id: the runtime names no plugin.

Matching Controls union the two lists by plugin id (sets; a later
Control's config for the same id wins). Governance plugins that attach to the
same transition run in `priority` order (higher first, default 0; same-priority
order is the plugin id). `priority` is an integer; anything else is refused when the
Control loads and by `mas-ctl validate`. Call ceilings are the `budget` governance plugin
(`max_llm_calls`, `max_tool_calls`), not a Control key. `--without-gov` turns governance plugins off;
`--no-events` and `--events-format` replace event plugins.

`spec.plugins`, `spec.control`, and `spec.impair` on a Control are leftover and refused.
Delay, drop, fail, corrupt and replay are not Control plugins: they belong on the infra
pipelines with retry, the circuit breaker, cache, scheduler and fault injection.

A run records events once, from the entry agent's plugins: `$all`, `$entry`,
`.mas.agent[*]`, or a list naming it (`$not-entry` and `$delegates` never
select it). `--no-events` and `--events-format` replace the plugin set. Else
the Control observability set is the plugin set. Flavour
`bind.events` (or `--events-sink ROW`) then adds the plugin each named infra
row's `protocol` gives; native still records unless a Control named the
observability list (including `[]`) or leftover agent observability named
plugins. One warning names a Control or CLI flag that replaced
another source. `--events` only forces recording. `--events-file` and
`--events-stdout` name native's JSONL file and stream, so they add `native`
when it is not in the set; none of the three replaces a plugin. The whole
Control is in the run hash, including a `native` `categories` filter (a list
or `{include, exclude}`).

## Design rules

A Control follows the same rules as the logic manifests:

- **It names plugins.** `spec.observability` and `spec.governance` are sets.
  Entries are plugin names with their settings, such as `governance: [sample_governance]` or
  `governance: [{budget: {max_llm_calls: 50}}]`. A Control holds no code, no
  endpoint and no infra row.
- **It uses no absolute path.** A path inside a Control is relative to the Control file, and a
  reference to a shipped Control is `pkg://…` or a library reference. `mas-ctl validate` and
  every load refuse an absolute path in a Control, a path that walks up with `..`, and an
  absolute `ref` in a Flavour's `spec.control`. A Flavour `ref` cannot walk out of that
  Flavour's directory with `..`; `pkg://` and the command line may name a file elsewhere. A
  plugin lists the parameters that are paths in its library row: `attributes.path_params` (a file next to the
  Control, as `gdb` does for `script_file`) or `attributes.run_path_params` (a path the run writes, as `native`
  does for `path`). A `path_params` relative value resolves against the Control's own directory when the session
  loads. A relative path whose key is in neither list is refused. A script next to the Control is found wherever
  the checkout is, no directory enters the spec, and the run hash does not depend on the checkout.
- **The Flavour attaches it.** `spec.control` of a Flavour lists `{ref, agents}` rows. This is
  the one mechanism; the command line is another way to write the same Flavour.

## Attach from the command line

`--control REF[@SELECTOR]` adds a row to the session's Flavour, to a Flavour file given with
`--flavour` or to the default one. It is repeatable. `REF` is a path (relative to the current
directory or the manifest, or absolute: the command line is not a manifest), `pkg://…` or a
library reference. `SELECTOR` is `$all` (the default), `$entry`, `$not-entry`, `$delegates`,
agent ids, or the same agent path as `--override` (`.mas.agent[id]` / `.mas.agent[*]`).
Quote a selector so the shell does not expand `*`. `@*` is not a selector.

```bash
mas-ctl chat agent.yaml --control controls/governance-hitl.yaml
mas-ctl run-mas mas.yaml --control pkg://mas.library.standard/controls/with-hardened.yaml@$entry
mas-ctl chat agent.yaml --control 'gov.yaml@.mas.agent[*]'
mas-ctl chat agent.yaml --flavour desk.yaml --events-sink collector
mas-lab benchmark run experiment.yaml --control controls/governance-hitl.yaml
```

`--events-sink ROW` adds an infra Application row to `bind.events`. `--control` and
`--events-sink` work on `chat`, `tui`, `serve` and `run-mas`; `--no-events` and
`--events-format` replace the plugin set on those four; `--control` also works on
`compile`, `infra resolved`, and `mas-lab benchmark`. Passing a Control with `-o`, or
listing Controls under an Overlay's `spec.control`, still works and warns once: use `--control`.
A scenario's `overlays.control` attaches Control files the same way `--control` does.
A stacked lab Control overlay copies the lists onto agent spec from that file
(and warns once when an empty list replaces leftover YAML);
`$entry` is `workflow.entry` (then `mas.entry_agent`); omitted, it matches nobody.
`$delegates` is that entry node's `delegates_to` list; omitted or empty, it matches
nobody.

Control composes explicitly. Matching Controls union as one set.
Nothing is added because an infra row exists; a Flavour with no Control,
and a Control that names no such plugin, add none. Only what `bind` and a Control name is used. Retry,
circuit breaker, cache, scheduler and fault injection are not Control entries; they are layers on the infra pipeline (`standard:llm-proxy-hardened` turns the breaker on).

The agent and MAS spec keys `observability` and `governance` are deprecated and still
applied, each on its own, when no matching Control names that key: a Control that names
only observability leaves the agent's governance in force. Agent `spec.budget` is deprecated
and still enforced; name the `budget` plugin instead. `mas-ctl validate` warns once; move
them into a Control. Agent `spec.control` is deprecated too: `retry` and `circuit_breaker`
in it still apply, and `budget` and `rate_limiter` in it are read by nothing (validate warns).

Retry and the circuit breaker are not Control plugins. They wrap LLM and tool calls as layers on the infra
pipeline, as the LLM cache, the scheduler and fault injection do. Agent `spec.control.retry` and `.circuit_breaker`
still apply (deprecated, one warning) and become those steps.

See [flavour.md](flavour.md) and [governance.md](governance.md).
