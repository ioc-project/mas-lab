<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Plugin registry: manifests, types, and discovery

This page explains how `mas-runtime` knows which plugins exist and which plugin types are valid (`mas.runtime.registry`).
It is for library authors who add plugins or new plugin types.
For the relation between canonical URNs and short names, see [plugin-aliases.md](plugin-aliases.md). This page covers registration, not naming.

A plugin is a Python class that the runtime loads by name to fill one role (design pattern, tool provider, pipeline step, ...). A manifest is the YAML file that declares plugins.
Plugin types are data, not code: a library introduces `type: my_new_thing` in a manifest with no runtime code change, as long as a builtin, a `types:` entry, or another plugin's `provides_types:` establishes the type before registration finishes.
Benchmark steps and artifact codecs work this way: `mas-lab-bench` ships only the pipeline engine (`Pipeline`, `PipelineExecutor`, `PipelineStep` base class, caching, dependency resolution), and every concrete step or codec is a library plugin.

## Smallest example

A library registers one pipeline step by adding this to its `library.yaml`:

```yaml
plugins:
  - type: step
    name: my_step
    module: mas.library.lab.steps.my_step
    class: MyStep
```

`type: step` is a core type, so no `types:` entry is needed. The URN defaults to `mas.step.my_step`.

## Building blocks

| Concept | Where | What it is |
|---------|-------|------------|
| `PluginEntry` | `registry/__init__.py` | One registered plugin: URN, description, variants, shortcuts, attributes |
| `VariantInfo` | `registry/__init__.py` | One implementation of a plugin (`module`, `class_name`, `version`, optional `requires` / `extra`) |
| `PluginRegistry` | `registry/__init__.py` | Process-wide singleton (`get_registry()`) holding all entries, aliases, and known types |
| Known type | `PluginRegistry._known_types` | A category string (`"design_pattern"`, `"step"`, `"codec"`, ...) the registry accepts |
| Manifest | Usually `library.yaml`, or `*.plugins.yaml` for split libraries | YAML declaring `types:`, `plugins:`, `aliases:`, `defaults:`; parsed by `bootstrap._parse_generic_manifest` |

## Manifest schema

The `types:` / `plugins:` / `aliases:` / `defaults:` payload is the same in every file that carries it.
In `library.yaml` it sits beside the `apiVersion: mas/v1` / `kind: Library` header and the library metadata (full validated shape: [`docs/schemas/library.schema.yaml`](../../docs/schemas/library.schema.yaml)).
A split-out `plugins/<name>.plugins.yaml` file has no header; `register_manifest_file` reads the same four keys directly.

```yaml
# Optional: types this manifest introduces, independent of any plugin entry.
# Rarely needed; see "How a type becomes known".
types: [my_new_thing]

plugins:
  - type: step                       # required: the plugin's category
    name: my_step                    # short name; becomes the default shortcut
    urn: mas.step.my_step            # optional: derived from type+name if omitted
    module: mas.library.lab.steps.my_step
    class: MyStep
    shortcuts: [my_step]             # optional: defaults to [name]
    description: "..."               # optional
    attributes: {}                   # optional: merged into PluginEntry.attributes
    provides_types: [my_new_thing]   # optional: see below
    requires: []                     # optional: import names needed to use this plugin
    extra: ""                        # optional: pip/uv extra that installs requires:

  - type: my_new_thing
    name: instance_one
    module: mas.library.lab.widgets
    class: WidgetOne

# Optional: alias table merged into the registry (role/short-name -> URN).
aliases:
  my_short_name: mas.step.my_step

# Optional: per-type default plugin id, used when a spec omits an explicit
# binding. Mirrors the runtime-wide defaults.yaml / defaults.schema.yaml
# mechanism (see agent-defaults.md), scoped to this manifest's own types.
defaults:
  my_new_thing: mas.widget.instance_one
```

An entry can declare `variants:` (a dict of named implementations such as `builtin` / `otel`) instead of one top-level `module` / `class`. Use it when several implementations share one URN.

### `eval_metric`

`eval_metric` plugins register session metrics onto an eval provider (default `mce`). A plugin is either one `EvalMetric` subclass (`module` + `class`) or a family factory (`factory: package.module:build_metrics`, returning an iterable of metrics).
`library-eval` walks this type at load and calls `EvalProvider.register_metric` on the provider. Duplicate ids raise.
`eval_mce` scores every requested id through `get_provider("mce").compute_metrics` (stock MCE session ids, registered plugins, and step-local `prompt_metrics`).

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

## How a type becomes known

The registry does not accept a plugin's `type:` just because it appears in `plugins:`. A type is known, and so registerable, only if one of these holds before that candidate resolves:

1. It is a core runtime type: `design_pattern`, `context_manager`, `assembler`, `memory`, `governance`, `step`, `codec`.
2. It is listed in this manifest's top-level `types:`. This is how `summarizer` is introduced: a sub-plugin composed by the summarising context manager, not a top-level agent spec key.
3. It is listed in `provides_types:` on another candidate that is already resolved (possibly from an earlier manifest, or builtins).

The rule is deliberate. If appearing in `plugins:` were enough, `provides_types` would have no effect, and a typo such as `type: sttep` would silently create a permanently empty category instead of failing.

### Resolution algorithm (fixpoint)

`bootstrap._register_candidates_fixpoint` sweeps the unregistered candidates repeatedly:

1. Register every candidate whose type is currently known. Add its `provides_types` to the known set immediately, so later candidates in the same sweep can use them.
2. Repeat until a sweep makes no progress.
3. If candidates remain, raise `ValueError` naming every stuck URN and type. The failure happens at bootstrap, not as a `None` deep inside a run.

One plugin can therefore register a type for another plugin, regardless of library or manifest file boundaries:

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
# library-b: registers a widget instance. Load order across manifests does not
# matter: register_manifest_data merges reg._known_types (which already includes
# types provided by earlier manifests) with what this manifest declares or provides.
plugins:
  - type: widget
    name: sample
    urn: mas.widget.sample
    module: mas.library.b.widgets
    class: SampleWidget
```

## Discovery: where manifests come from

`bootstrap.load_registry()` runs three steps in order:

1. **Built-ins** (`_BUILTIN_PLUGINS`, `_BUILTIN_TYPES`, plus package defaults from `defaults.yaml`; see [agent-defaults.md](agent-defaults.md)). This is the small set the runtime ships: design patterns, context managers, the context assembler, semantic memory, sample governance. Library content (steps, codecs, tools) does not live here.
2. **Aliases** (`aliases.yaml` plus `config.yaml` overrides); see [plugin-aliases.md](plugin-aliases.md). `PluginRegistry.validate_aliases()` raises immediately if an alias points at an unregistered URN.
3. **Library plugin manifests.** [`mas.library_catalog.discover_plugin_manifests`](../src/mas/library_catalog.py) checks every library root returned by [`mas.library_roots.discover_library_roots`](../src/mas/library_roots.py) (lab-local first, then workspace `manifest_libraries:`, installed libraries, `MAS_LIBRARY_PATHS`, ancestor walk; see [library-discovery.md](../../docs/library-discovery.md)).

`library.yaml` is the plugin manifest. The same file carries the `kind: Library` metadata (name, description, version, `module_base`, validated against [`docs/schemas/library.schema.yaml`](../../docs/schemas/library.schema.yaml)) and optional `apps:` / `datasets:` / `tools:` catalogs.
When it also declares `types:` / `plugins:` directly, that content is registered too. The check is key presence (`mas.library_catalog._declares_plugins`), not a guess from value shape.

A library splits plugin declarations across several manifests in one of two ways:

- an explicit `plugin_manifests:` map in `library.yaml` (`name: relative/path.yaml`), a different key from `plugins:` so the two conventions never need telling apart by value type; or
- the scan fallback: any `plugins/**/*.plugins.yaml` under the library root.

Every discovered manifest is registered through `register_manifest_file()` / `register_manifest_data()`, the same fixpoint mechanism described above. A malformed manifest or an unresolvable type raises at startup.

`mas-library-lab` registers its pipeline steps and artifact codecs this way; see the `types:` / `plugins:` block of `library-lab/library.yaml`.

## Availability gate: `requires:`/`extra:` for optional dependencies

A plugin may need a third-party package that the library does not force on every install (an OTel exporter, a vector DB client). Declare the import names it needs and, optionally, the pip/uv extra that installs them:

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

This is the shipping declaration for `mas.observability.otel` in [`library-telemetry/library.yaml`](../../library-telemetry/library.yaml). `opentelemetry-sdk` is not a base dependency of `mas-library-telemetry`, only of its `convert` extra.

**Discovery is unaffected.** A plugin with unmet `requires:` is still
registered and still shows up in `PluginRegistry.list()` — it is *marked*
disabled, not hidden, so `mas-ctl plugin list` and `mas registry` can tell you
it exists and how to turn it on. Availability is a filesystem lookup of
each `requires:` name: listing never imports those modules.

**Resolution applies the gate.** `PluginEntry.resolve()` checks `requires:` first. So do `PluginRegistry.resolve()`, `resolve_by_type()`, `get()`, and `create()`, which every spec key (`design_pattern`, `context_manager`, `codec`, a pipeline `step`, ...) goes through to construct a plugin.
If a name is missing, they raise `PluginUnavailable` with the missing import names and an install hint, instead of a bare `ModuleNotFoundError` from `VariantInfo.load_class()` or the plugin constructor.
Plugin construction happens at spec and kernel-compile time, so a missing dependency fails at startup with a clear message.

A plugin with no `requires:` (the default, and every built-in plugin) is unaffected: `missing_requires()` is `[]`.

## `mas-ctl plugin` CLI

```bash
mas-ctl plugin list [--type TYPE] [--json]   # every discovered plugin, available or disabled + why
mas-ctl plugin doctor [MANIFEST ...]         # check availability for a spec's referenced plugins
                                             # (or every registered plugin, with no arguments)
mas-ctl plugin enable <urn> [--dry-run]      # install the plugin's extra (or requires:), then re-check
```

The output below is illustrative. The plugin list and availability depend on the installed libraries, and the `disabled` hint printed by the CLI currently spells the command `mas plugin enable`; use `mas-ctl plugin enable`.

`doctor` is the CI and authoring-time form of the check that `resolve()` runs at runtime. It reports instead of raising, and given a manifest it checks only the plugins that spec references:

```console
$ mas-ctl plugin doctor mas.yaml
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

When adding a plugin type (not just a plugin):

- [ ] Reuse an existing type (`step`, `codec`, `design_pattern`, ...) if one fits.
- [ ] If the type is new, check whether an already-resolvable plugin's `provides_types:` covers it, or add a `types:` entry to your manifest.
- [ ] Add a manifest test that the type resolves through `register_manifest_data` / `register_manifest_file`, not only through the low-level `_register_candidates_fixpoint` helper. See `runtime/tests/test_registry_fixpoint.py` for both styles.
- [ ] Document the type and a minimal example in this file.
