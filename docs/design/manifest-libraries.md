# Folder-based manifest libraries + plugin availability

Status: Phase 1 implemented — folder-based `library.yaml` discovery
(goals 1-3) already shipped via `mas.library_roots`/`mas.runtime.
package_refs` (see [`docs/library-discovery.md`](../library-discovery.md));
the availability gate and `mas-ctl plugin` CLI (goals 4-5, and the `mas
plugin` section below) now ship too — see
[`runtime/docs/plugin-registry-manifests.md`](../../runtime/docs/plugin-registry-manifests.md#availability-gate-requiresextra-for-optional-dependencies)
for the actual `requires:`/`extra:` schema, `PluginUnavailable`, and CLI
usage, with `mas.observability.otel` in `library-standard/library.yaml` as
a real, shipping example. Library alignment and the phase 2/3 rollout
below (retiring the central catalog for every internal library, flipping
resolution to scan-first) remain future work.

## Goals

1. A **library is a folder tree with a `library.yaml` at its root** — not a
   Python package. It is discovered by *scanning* known roots, not by importing
   a module.
2. **Discovery is decoupled from availability.** Discovery is a filesystem scan
   (no import, no install). Availability applies only to plugins that carry code
   with third-party dependencies.
3. **Declarative manifests** (infra bundles, app manifests, pipeline YAMLs) have
   no code and no deps — they resolve by scan alone, with nothing installed.
4. **Code plugins declare their deps** and are *discovered always* but *enabled
   only when their deps import*. A missing dep surfaces a clear, actionable
   message **at startup** — never deep inside a run.
5. Dependencies are **opt-in** (never auto-installed on library install).
   `mas-ctl plugin enable <urn>` installs a plugin's declared extra on demand.

## `library.yaml` schema

At the root of every library folder:

```yaml
name: mas-library-kg
description: "KG normalization, verification, neo4j."
version: "0.1.0"
module_base: mas.library.kg          # optional; only for code plugins

# Plugins provided by this library (folder-declared, discovered by scan).
plugins:
  design_patterns:
    mas.dp.react:
      module: mas.library.standard.plugins.design_patterns.react
      class: ReactPlugin
      shortcuts: [react, react@v1]
      requires: []                   # runtime deps (import names); [] = always available
  codecs:
    mas.codec.kg-neo4j:
      module: mas.library.kg.codecs
      class: Neo4jKGCodec
      artifact: kg
      target: neo4j
      requires: [neo4j]              # disabled unless `neo4j` imports
      extra: "mas-library-kg[neo4j]" # what `mas-ctl plugin enable` installs

# Declarative bundles — pure data, no deps, resolved by scan.
infra:   { llm-proxy: libs/claris/llm-proxy.yaml }   # optional explicit catalog
```

`infra`/`apps`/`pipelines` may be an explicit name→path catalog or resolved by
directory convention (`libs/<lib>/<name>.yaml`, `infra/<name>.yaml`, …). The
`requires`/`extra` keys apply only to code plugins.

## Discovery — scan, don't import

`mas.runtime.library_roots` gains a scan that collects library roots from, in
order:

1. `MAS_LIBRARY_PATHS` (path-separated dirs) — local/dev libraries.
2. Workspace members (when in a workspace checkout).
3. `mas.runtime.manifest_libraries` entry points — but treated purely as a
   **locator** for installed libraries (resolve the folder, then read its
   `library.yaml`; do not rely on `package_root()` or importing plugin code).

For each root, read `library.yaml`; register its scheme (`name` tail / dir
name), plugins, and declarative catalogs. No plugin code is imported during
discovery. This is additive: the existing entry-point path keeps working, so
`standard:`/`samples:` continue to resolve during the transition.

A local, unpackaged plugin is therefore just a folder on `MAS_LIBRARY_PATHS`
with a `library.yaml` — no `pip install`, no entry point.

## Availability gate

`PluginRegistry` records each plugin's `requires`. Resolution:

- `resolve()/get_instance()` on a plugin whose `requires` don't import raises
  `PluginUnavailable(urn, missing=[...], extra="…")` with the exact install
  hint. Discovery/listing still shows it (marked `disabled`, with reason).
- **Startup validation:** when a spec/experiment is loaded, the plugins it
  references have their `requires` checked immediately, so a missing dep fails
  fast with the install hint rather than mid-run.
- Declarative refs (infra/app/pipeline) are never gated — they have no deps.

## `mas-ctl plugin` CLI

- `mas-ctl plugin list [--type codec|design_pattern|…]` — every discovered plugin,
  its library, `available|disabled`, and (if disabled) the missing deps + extra.
- `mas-ctl plugin enable <urn>` — read the plugin's `extra` (or `requires`) and
  install it into the active environment (`uv pip install <extra>`), then
  re-check. Explicit and opt-in; nothing is auto-installed on library install.
- `mas-ctl plugin doctor` — validate every referenced plugin's deps for a given
  spec, printing install hints for anything disabled.

## Library alignment

Each library (OSS: standard, samples; internal: kg, telemetry, claris, ioc,
analysis, ioa, memory, openclaw) gets a root `library.yaml` with `plugins:` and
declarative catalogs, and per-plugin `requires`/`extra`. The central runtime
`plugin_registry.yaml` is retired (its content moves into `library-standard`),
so the kernel ships no catalog — consistent with the pure-kernel work.

## Backward compatibility & rollout

- Phase 1 (done): scan discovery, the availability gate, and `mas-ctl plugin`
  all shipped additively; entry-point discovery is still active alongside
  them.
- Phase 2 (future work): add `library.yaml` to every library; move
  built-ins into `library-standard`; retire the central catalog.
- Phase 3 (future work): flip resolution to scan-first; entry points
  become locators only.
