<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Plugin Registry: Manifests, Types, and Discovery

This document explains how `mas-runtime` knows *which plugins exist* and
*which types of plugin are valid* — the mechanism behind `mas.runtime.
registry`. For how a plugin's canonical URN relates to human-friendly
short names, see [plugin-aliases.md](plugin-aliases.md); this document is
about registration, not naming.

## The problem this solves

Earlier versions of the runtime had a hardcoded Python catalog
(`plugin_registry.yaml` parsed by literal Python code, plus separate
hardcoded dicts in `mas-lab-bench` for pipeline steps and artifact codecs).
Every new plugin *type* — not just every new plugin — required a code
change in the runtime or in bench. That meant:

- Libraries could not introduce a new kind of pluggable thing without a
  runtime code change.
- There was no single place to answer "what plugins exist, and where do
  they come from?"
- Bench (the benchmark *engine*) accumulated built-in implementations of
  pluggable concepts (steps, codecs) that architecturally belong in a
  library, not the engine.

The manifest/fixpoint system below fixes this: **plugin types are data,
not code**. A library can introduce `type: my_new_thing` in a manifest
with zero runtime code changes, as long as something (a builtin, an
explicit `types:` declaration, or another plugin's `provides_types:`)
establishes that the type is legitimate before the fixpoint pass ends.

## Core building blocks

| Concept | Where | What it is |
| --- | --- | --- |
| `PluginEntry` | `registry/__init__.py` | One registered plugin: URN, description, variants, shortcuts, attributes. |
| `VariantInfo` | `registry/__init__.py` | One implementation of a plugin (`module` + `class_name` + `version` + optional `requires`/`extra`). |
| `PluginRegistry` | `registry/__init__.py` | Process-wide singleton (`get_registry()`) holding all entries, aliases, and known types. |
| Known type | `PluginRegistry._known_types` | A category string (`"design_pattern"`, `"step"`, `"codec"`, ...) the registry currently accepts. |
| Manifest | usually `library.yaml`, or `*.plugins.yaml` for libraries that split it up | A YAML file declaring `types:`, `plugins:`, `aliases:`, `defaults:`. Parsed by `bootstrap._parse_generic_manifest`. |

## Manifest schema

The `types:`/`plugins:`/`aliases:`/`defaults:` payload below is the same
regardless of which file carries it. In `library.yaml` it sits alongside
the `apiVersion: mas/v1` / `kind: Library` header and the library's own
metadata (see [`docs/schemas/library.schema.yaml`](../../docs/schemas/library.schema.yaml)
for the full, validated shape); a split-out `plugins/<name>.plugins.yaml`
file has no header at all — `register_manifest_file` reads the same four
keys directly.

```yaml
# Optional: types this manifest itself introduces, independent of any
# plugin entry below. Rarely needed — see "How a type becomes known".
types: [my_new_thing]

plugins:
  - type: step                       # required — the plugin's category
    name: my_step                    # short name; becomes the default shortcut
    urn: mas.step.my_step            # optional — derived from type+name if omitted
    module: mas.library.lab.steps.my_step
    class: MyStep
    shortcuts: [my_step]             # optional — defaults to [name]
    description: "..."               # optional
    attributes: {}                   # optional — merged into PluginEntry.attributes
    provides_types: [my_new_thing]   # optional — see below
    requires: []                     # optional — import names needed to use this plugin
    extra: ""                        # optional — pip/uv extra that installs requires: (see below)

  - type: my_new_thing
    name: instance_one
    module: mas.library.lab.widgets
    class: WidgetOne

# Optional: alias table merged into the registry (role/short-name -> URN).
aliases:
  my_short_name: mas.step.my_step

# Optional: per-type default plugin id, used when a spec omits an explicit
# binding. Mirrors the runtime-wide mechanism in defaults.yaml/defaults.schema.yaml
# (see agent-defaults.md) but scoped to this manifest's own types.
defaults:
  my_new_thing: mas.widget.instance_one
```

A manifest entry can declare `variants:` (a dict of named implementations,
e.g. `builtin`/`otel`) instead of a single top-level `module`/`class`, for
plugins that ship more than one backing implementation behind the same URN.

### `eval_metric`

Session metrics registered onto an eval provider (default `mce`). One plugin
is either a single `EvalMetric` subclass (`module` + `class`) or a family
factory (`factory: package.module:build_metrics` returning an iterable of
metrics). `library-eval` walks this type at load and calls
`EvalProvider.register_metric` on that provider. Duplicate ids raise.
`eval_mce` scores every requested id through `get_provider("mce").compute_metrics`
(stock MCE session ids, registered plugins, and step-local `prompt_metrics`).

```yaml
types: [eval_metric]
plugins:
  - type: eval_metric
    name: toy_echo
    module: mas.library.eval.metrics.toy
    class: ToyEchoMetric
    attributes:
      provider: mce
  - type: eval_metric
    name: challenge_family
    factory: mas.library.example.eval.challenges:build_metrics
    provider: mce
    description: One factory yields many metric ids.
```

## How a type becomes known — and why it matters

The registry does **not** trust a plugin's own `type:` field just because
it appears in `plugins:`. A type is only "known" — and therefore
registerable — if at least one of these is true *before* the fixpoint
resolves that candidate:

1. It's a core runtime type (`design_pattern`, `context_manager`,
   `assembler`, `memory`, `governance`, `step`, `codec`).
2. It's listed in this manifest's own top-level `types:` (how
   `summarizer` is introduced — a sub-plugin composed by the summarising
   context manager, not a top-level agent spec key).
3. It's listed in `provides_types:` on some *other* candidate that has
   already been resolved (possibly from an earlier-loaded manifest, or
   builtins).

This is a deliberate design choice, not an oversight: if simply appearing
in `plugins:` were enough to legitimize a type, `provides_types` would be
meaningless (nothing would ever actually wait for it), and a typo like
`type: sttep` would silently create a brand-new, permanently-empty
category instead of failing loudly. Every manifest that introduces plugins
of a genuinely new type must say so explicitly — either via `types:` or via
another plugin's `provides_types:`.

### Resolution algorithm (fixpoint)

`bootstrap._register_candidates_fixpoint` repeatedly sweeps the list of
not-yet-registered candidates:

1. Any candidate whose type is currently known gets registered; its own
   `provides_types` are added to the known-types set immediately (so a
   later candidate in the *same* sweep can already use them).
2. Repeat until a sweep makes no progress.
3. If candidates remain unresolved after a sweep makes no progress, raise
   `ValueError` naming every stuck URN and its type — loudly, at bootstrap
   time, not as a confusing `None` deep inside a run.

This means **one plugin can register a type for another plugin to use**,
regardless of which library either one ships in, and regardless of
manifest file boundaries — the exact capability needed for a library to
extend the plugin type system without a runtime code change.

```yaml
# library-a: introduces "widget" as a real category
plugins:
  - type: design_pattern
    name: widget_provider
    urn: mas.dp.widget_provider
    module: mas.library.a.provider
    class: WidgetProviderPlugin
    provides_types: [widget]

---
# library-b: registers a widget instance, loaded before OR after library-a's
# manifest — order across manifests doesn't matter because register_manifest_data
# merges reg._known_types (already includes anything provided by previously
# processed manifests) with what this manifest itself declares/provides.
plugins:
  - type: widget
    name: sample
    urn: mas.widget.sample
    module: mas.library.b.widgets
    class: SampleWidget
```

## Discovery: where manifests come from

`bootstrap.load_registry()` runs, in order:

1. **Built-ins** (`_BUILTIN_PLUGINS`, `_BUILTIN_TYPES`, plus package
   defaults from `defaults.yaml` — see [agent-defaults.md](agent-defaults.md))
   — the small set of plugins the runtime itself ships (design patterns,
   context managers, the context assembler, semantic memory, sample
   governance). This is intentionally minimal; it is not where
   library-provided pluggable content (steps, codecs, tools, ...) lives.
2. **Aliases** (`aliases.yaml` + `config.yaml` overrides) — see
   [plugin-aliases.md](plugin-aliases.md). Validated with
   `PluginRegistry.validate_aliases()`: an alias pointing at a URN that
   isn't registered raises immediately, rather than silently resolving to
   `None` later.
3. **Library plugin manifests** — every library root returned by
   [`mas.library_roots.discover_library_roots`](../src/mas/library_roots.py)
   (lab-local first, then workspace `manifest_libraries:`, installed
   libraries, `MAS_LIBRARY_PATHS`, ancestor walk — see
   [library-discovery.md](../../docs/library-discovery.md)) is checked by
   [`mas.library_catalog.discover_plugin_manifests`](../src/mas/library_catalog.py)
   for plugin manifests. **`library.yaml` *is* the plugin manifest** — the
   same file carries `kind: Library` metadata (name/description/version/
   module_base, validated against
   [`docs/schemas/library.schema.yaml`](../../docs/schemas/library.schema.yaml))
   and optional `apps:`/`datasets:`/`tools:` catalogs; when it also
   declares `types:`/`plugins:` directly (a key-presence check —
   `mas.library_catalog._declares_plugins` — not a value-shape guess),
   that content is registered too. A library only reaches for a separate
   file if it genuinely wants to split plugin declarations across
   multiple manifests, via either:
   - an explicit `plugin_manifests:` map in `library.yaml` (`name: relative/path.yaml`)
     — a deliberately different key from `plugins:`, so the two conventions
     never need to be told apart by inspecting a value's type, or
   - a scan fallback: any `plugins/**/*.plugins.yaml` under the library root.

   Every discovered manifest (`library.yaml` itself, and/or any split-out
   file) is registered via `register_manifest_file()`/`register_manifest_data()`
   — the exact same fixpoint mechanism described above. A malformed manifest
   or an unresolvable type raises immediately at startup; it is not
   swallowed.

`mas-library-lab`'s pipeline steps and artifact codecs are registered this
way (see `library-lab/library.yaml`'s `types:`/`plugins:` block) without
`mas-lab-bench` containing a single hardcoded step or codec class — bench
ships only the pipeline *engine* (`Pipeline`, `PipelineExecutor`,
`PipelineStep` base class, caching, dependency resolution); every concrete
step or codec is a library plugin resolved through this registry at run
time.

## Availability gate: `requires:`/`extra:` for optional dependencies

A plugin can carry code with a third-party dependency the library doesn't
force on every install (an OTel exporter, a vector DB client, ...).
Declare the import names it needs and, optionally, the pip/uv extra that
installs them:

```yaml
plugins:
  - type: observability
    name: otel
    urn: mas.observability.otel
    module: mas.library.telemetry.plugins.otel_plugin
    class: OtelObservabilityPlugin
    requires: [opentelemetry.sdk.trace]
    extra: "mas-library-telemetry[convert]"
```

This is the real, shipping declaration for `mas.observability.otel` in
[`library-telemetry/library.yaml`](../../library-telemetry/library.yaml) —
`opentelemetry-sdk` is not a base dependency of `mas-library-telemetry`,
only of its `convert` extra.

**Discovery is unaffected.** A plugin with unmet `requires:` is still
registered and still shows up in `PluginRegistry.list()` — it is *marked*
disabled, not hidden, so `mas-ctl plugin list` and `mas registry` can tell you
it exists and how to turn it on. Availability is a filesystem lookup of
each `requires:` name: listing never imports those modules.

**Resolving it is where the gate applies.** `PluginEntry.resolve()` — and
therefore `PluginRegistry.resolve()`/`resolve_by_type()`/`get()`/`create()`,
the paths every spec key (`design_pattern`, `context_manager`, `codec`, a
pipeline `step`, ...) goes through to actually construct a plugin instance
— checks `requires:` first and raises `PluginUnavailable` with the missing
import names and an install hint, instead of letting a bare
`ModuleNotFoundError` surface from deep inside `VariantInfo.load_class()`
or the plugin's own constructor. Because plugin construction already
happens at spec/kernel-compile time (not lazily mid-conversation), this
means a missing dependency fails at startup with a clear message, not
mid-run with a stack trace pointing at an unrelated line.

A plugin declared with no `requires:` (the default — every built-in plugin
today) is unaffected: `missing_requires()` is always `[]`, so
`resolve()`/`create()` behave exactly as before.

## `mas-ctl plugin` CLI

```bash
mas-ctl plugin list [--type TYPE] [--json]   # every discovered plugin, available or disabled + why
mas-ctl plugin doctor [MANIFEST ...]         # check availability for a spec's referenced plugins
                                          # (or every registered plugin, with no arguments)
mas-ctl plugin enable <urn> [--dry-run]      # install the plugin's extra (or requires:), then re-check
```

`doctor` is the CI/authoring-time version of the same check `resolve()`
does at runtime — it reports rather than raises, and can be pointed at a
manifest to check only what that spec actually references:

```console
$ mas-ctl plugin doctor docs/tutorials/01-building-an-agent/agent.yaml
  ok       mas.dp.react

$ mas-ctl plugin list --type observability
[observability]
  mas.observability.native                 available
  mas.observability.otel                   disabled  (missing: opentelemetry.sdk.trace; mas-ctl plugin enable mas.observability.otel)

$ mas-ctl plugin enable mas.observability.otel
Installing: uv pip install mas-library-telemetry[convert]
mas.observability.otel is now available.
```

## Contributor checklist

When adding a new plugin type (not just a new plugin):

- [ ] Does an existing type already fit? Prefer reusing `step`, `codec`,
      `design_pattern`, etc. over inventing a new one.
- [ ] If genuinely new: does some already-resolvable plugin's
      `provides_types:` cover it, or does your manifest need its own
      `types:` entry?
- [ ] Add a manifest test asserting the new type resolves end-to-end
      through `register_manifest_data`/`register_manifest_file` — not just
      through the low-level `_register_candidates_fixpoint` helper (see
      `runtime/tests/test_registry_fixpoint.py` for both styles and why
      the distinction matters).
- [ ] Document the new type and a minimal example in this file.
