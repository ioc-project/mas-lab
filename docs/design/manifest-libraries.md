Status: Implemented. User-facing reference: [Library discovery](../library-discovery.md) and the runtime [plugin registry manifests](https://github.com/outshift-open/mas-lab/blob/main/runtime/docs/plugin-registry-manifests.md#availability-gate-requiresextra-for-optional-dependencies) page.

# Folder-based manifest libraries and plugin availability

A *library* is a folder of reusable apps, tools, plugins, and datasets. A *plugin* is a unit of behaviour that a manifest selects by name. This note records how the runtime finds libraries and decides whether a plugin is usable.

## The problem

A library may ship plugins whose third-party dependencies are not installed. Importing every library at startup would fail on the first missing dependency. It would also hide the other libraries.

## Decision

1. A **library is a folder tree with a `library.yaml` at its root**. It is not a Python package. The runtime discovers it by *scanning* known roots. It does not import a module.
2. **Discovery is separate from availability.** Discovery is a filesystem scan. It imports no plugin and installs nothing. Availability applies only to plugins that carry code with third-party dependencies.
3. **Declarative manifests** (infra bundles, app manifests, datasets, pipeline YAMLs) have no code and no dependencies. They resolve by scan alone.
4. **Code plugins declare their dependencies.** The runtime always discovers them. It lets you use one only when its dependencies import. A missing dependency produces a clear, actionable message **at startup**, never deep inside a run.
5. Dependencies are **opt-in**. Installing a library never installs them. `mas-ctl plugin enable <urn>` installs a plugin's declared extra on demand.

## `library.yaml`

Every library in the repository has a root `library.yaml`: `library-standard`, `library-samples`, `library-skills`, `library-eval`, `library-ioa`, `library-lab`, `library-telemetry`, and `library-kg`. The schema is [`docs/schemas/library.schema.yaml`](../schemas/library.schema.yaml). The runtime ships no central plugin catalog of its own.

A trimmed example:

```yaml
apiVersion: mas/v1
kind: Library
name: mas-library-telemetry
version: "0.1.0"
module_base: mas.library.telemetry   # optional; only for code plugins

types:
  - observability

plugins:
  - type: observability
    name: otel
    urn: mas.observability.otel
    module: mas.library.telemetry.plugins.otel_plugin
    class: OtelObservabilityPlugin
    requires: [opentelemetry.sdk.trace]       # import names; omitted = always available
    extra: "mas-library-telemetry[convert]"   # what `mas-ctl plugin enable` installs
```

Other top-level keys: `schemes`, `description`, `dependencies`, `apps`, `datasets`, `tools`, `plugin_manifests`, `aliases`, `defaults`.

`apps`, `datasets`, and `tools` may be an explicit name-to-path catalog. They may also resolve by directory convention, for example `apps/<name>/v<N>/`. `requires` and `extra` apply only to code plugins.

## Discovery: scan, do not import

`mas.library_roots` collects library roots in this order. The first-seen name wins.

1. **Lab-local** libraries from the enclosing `lab-config.yaml`.
2. **Workspace config**: the `config.yaml` `manifest_libraries:` search folders.
3. **Installed libraries**: the `mas.runtime.manifest_libraries` entry points. They serve only as a **locator** for the library folder. The runtime does not import plugin code to find it.
4. **`MAS_LIBRARY_PATHS`**: path-separated directories for local and development libraries.
5. **Ancestor walk** from the anchor or current directory, looking for a `library.yaml`. It stops at `.git`.

A local, unpackaged plugin library is therefore a folder with a `library.yaml` on one of these paths. It needs no `pip install` and no entry point. One library with a broken import never prevents discovery of the others.

## Availability gate

`PluginRegistry` records each plugin's `requires`.

- Resolving or instantiating a plugin whose `requires` do not import raises `PluginUnavailable` (`mas.runtime.registry`). The error names the missing modules and the install hint. Listing still shows the plugin, marked `disabled`.
- The gate never applies to declarative refs (infra, app, dataset, pipeline). They have no dependencies.

## `mas-ctl plugin` CLI

- `mas-ctl plugin list [--type <category>] [--json]`: every discovered plugin, marked `available` or `disabled`. For a disabled plugin it shows the missing dependencies and the extra.
- `mas-ctl plugin enable <urn>`: install the plugin's `extra` into the active environment, then re-check. This is explicit and opt-in.
- `mas-ctl plugin doctor [MANIFEST...]`: check the plugins that the given spec or experiment files reference. With no files, check every registered plugin. Print install hints for anything disabled.

## See also

- [Library discovery](../library-discovery.md)
- [Libraries and labs](../labs-and-libraries.md)
- [Schema index](../references/schemas.md)
